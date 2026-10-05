"""`suggest` (M2 §3.8, ADR 0026): una lista tipada de sugerencias, posiblemente vacía, para `RunResult`.

El nodo no ejecuta tools: el modelo *recomienda* lecturas y *prepara* acciones (ninguna es ejecutable). Lo que
ve el modelo son las rutas de `reads` en vista `model` (slots envueltos como texto no confiable). La
recomendación de escalar (`escalate`) la fija el flow: M1 (G0-28) exige que el nodo solo se alcance por la
rama `true` de una `rule`; aquí se arma su evidencia con valores escalares de slots o hechos (que nunca van al
modelo) y el modelo solo redacta el borrador del motivo. La validación de la lista es de M8, detrás del
`SuggesterPort`."""

import re
from contextlib import suppress
from decimal import Decimal

from agent_core.domain import (
    IllegalTransition,
    JsonValue,
    RunState,
    SuggestEscalation,
    SuggestionsProduced,
    SuggestionsProducedPayload,
    SuggestNode,
    suggestion_counts,
)
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.ports import SuggestRequest, SuggestResult
from agent_core.interpreter.projection import model_inputs
from agent_core.interpreter.resolve import MissingPath, parse_runtime_path, resolve_path
from agent_core.views import TOKEN_PATTERN

_TOKEN = re.compile(TOKEN_PATTERN)
# Solo se necesita la huella con clave de la lista; estos nombres la proyectan como texto no confiable para
# no sembrar tokens de cada campo en el vault del run.
_TEXT_FIELDS = ["text", "citations", "language", "tool", "args", "why", "summary", "reason_code", "evidence",
                "motive_draft"]


class _Unfit(Exception):
    """La evidencia que pide el flow no se puede dar tal cual (no es escalar, trae PII o no existe)."""


def _scalar(value: JsonValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | Decimal):
        return str(value)
    raise _Unfit


def _evidence_line(raw: str, state: RunState, ctx: StepContext) -> str:
    """`nombre: valor` de una ruta escalar. `full` solo sale por una vista: el detector de M7 no debe hallar
    PII (ni un token) en la línea; si la halla, el nodo se rinde."""
    path = parse_runtime_path(raw)
    if path is None:
        raise _Unfit  # M1 (G0-01) exige una ruta: un literal no es evidencia
    try:
        text = _scalar(resolve_path(state, path))
    except MissingPath:
        raise _Unfit from None
    if ctx.views.tokenize_text(text, ctx.vault) != text or _TOKEN.search(text):
        raise _Unfit
    label = ".".join([path.name or "", *(part for part in path.rest if part != "value")])
    return f"{label}: {text}"


def _escalation(node: SuggestNode, state: RunState, ctx: StepContext) -> SuggestEscalation | None:
    spec = node.config.escalate
    if spec is None:
        return None
    lines = tuple(_evidence_line(raw, state, ctx) for raw in spec.evidence_from)
    return SuggestEscalation(reason_code=spec.reason_code, evidence=lines)


def _event(node: SuggestNode, state: RunState, ctx: StepContext, result: SuggestResult | None,
           failures: list[str]) -> SuggestionsProduced:
    ok = result is not None and not failures
    suggestions = result.suggestions if result is not None and ok else []
    fingerprint = None
    if suggestions:
        listed: JsonValue = [s.model_dump(mode="json") for s in suggestions]
        fingerprint = ctx.views.project(listed, "suggestions", _TEXT_FIELDS, ctx.vault).fingerprint
    payload = SuggestionsProducedPayload(
        node_id=node.id, result="ok" if ok else "failed", **suggestion_counts(suggestions),
        regenerations=0 if result is None else result.regenerations, failures=failures,
        text_fp=fingerprint, llm=None if result is None else result.llm)
    return Events(ctx).suggestions_produced(state, payload)


def _gave_up(node: SuggestNode, state: RunState, ctx: StepContext, result: SuggestResult | None,
             failures: list[str]) -> NodeResult:
    return NodeResult(state, result_key="gave_up", events=[_event(node, state, ctx, result, failures)])


def handle_suggest(node: SuggestNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    if ctx.suggester is None:
        raise IllegalTransition(f"el nodo suggest {node.id} necesita un SuggesterPort en el StepContext")
    if ctx.degraded:  # `injection_flagged`: sin modelo en este turno (spec general §4.1 paso 6)
        return _gave_up(node, state, ctx, None, ["degraded"])
    if model_budget_exhausted(state, ctx):
        return escalate_now(state, ctx, "budget_exceeded")
    try:
        inputs = model_inputs(node.config.reads, state, ctx)
    except MissingPath:
        return _gave_up(node, state, ctx, None, ["missing_input"])  # sin sus entradas no se llama al modelo
    for raw in node.config.optional_reads:
        with suppress(MissingPath):  # un slot opcional ausente no es un error: simplemente no se envía
            inputs |= model_inputs([raw], state, ctx)
    try:
        escalation = _escalation(node, state, ctx)
    except _Unfit:
        return _gave_up(node, state, ctx, None, ["escalation_evidence"])
    result = ctx.suggester.suggest(SuggestRequest(node.id, node.config, inputs, escalation), state)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    if result.failures:
        return _gave_up(node, state, ctx, result, list(result.failures))
    return NodeResult(state, result_key="suggested", suggestions=list(result.suggestions),
                      events=[_event(node, state, ctx, result, [])])
