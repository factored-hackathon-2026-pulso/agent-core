"""`Responder` de M8: plantillas y cadena `respond(generate)` (spec §3.2). Sin hora ni azar propios."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial

from agent_core.domain import (
    DomainError,
    EngineEvent,
    EntityKind,
    EntityRef,
    EscalationRequest,
    GenerateConfig,
    JsonValue,
    Locale,
    Message,
    NodeId,
    RefSpec,
    RejectedDraft,
    ResponseEmitted,
    ResponseEmittedPayload,
    ResponseFailed,
    ResponseFailedPayload,
    RunState,
    Template,
    ValidatorOutcome,
)
from agent_core.ports import Clock, IdKind, IdSource, LLMGateway, RegistryPort
from agent_core.response.checks import PII_DETAIL_PREFIX, check_tokens_pii
from agent_core.response.templates import TemplateUnavailable, render_template_text
from agent_core.response.types import Draft, Failure, ValidationContext, parse_draft
from agent_core.response.usage import UsageMeter
from agent_core.response.validate import validate

DRAFT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "citations"],
    "additionalProperties": False,
}
_FACT_PATH = re.compile(r"facts\.([a-z0-9_]+)(?:\..*)?")


@dataclass(frozen=True, slots=True, repr=False)
class ResponderContext:
    """Puertos y datos de un turno para `Responder.generate`; los arma quien cablea M2/M4."""

    gateway: LLMGateway
    clock: Clock
    ids: IdSource
    resolve_ref: Callable[[EntityKind, RefSpec], EntityRef]  # M2 inyecta `exact_ref`; M8 no importa `flows`
    locale: Locale
    degraded: bool
    release: str
    turn_id: str | None
    default_target_queue: str
    priority: str
    facts_model_view_by_name: Mapping[str, JsonValue]  # `{nombre: {"value": ...}}` en vista `model`
    validation: ValidationContext
    claims: frozenset[str] = frozenset()
    node_id: NodeId | None = None
    max_regenerations: int = 1

    def __repr__(self) -> str:
        return f"ResponderContext(locale={self.locale!r}, degraded={self.degraded})"


class Responder:
    def __init__(self, registry: RegistryPort) -> None:
        self._registry = registry

    def template(self, template_ref: EntityRef, locale: Locale,
                 facts_model_view: Mapping[str, JsonValue]) -> Message:
        """Renderiza una plantilla exacta. No valida (las comprobaciones son de la cadena)."""
        try:
            template = self._registry.get(template_ref, Template)
        except (KeyError, DomainError) as error:
            raise TemplateUnavailable(f"plantilla no disponible: {template_ref}") from error
        text = template.locales.get(locale)
        if text is None:
            raise TemplateUnavailable(f"la plantilla {template_ref} no tiene el locale {locale}")
        return Message(kind="template", text=render_template_text(text, facts_model_view), locale=locale)

    def generate(self, node_config: GenerateConfig, state: RunState, ctx: ResponderContext,
                 ) -> tuple[Message | EscalationRequest, list[RejectedDraft], list[EngineEvent]]:
        """generar -> validar -> regenerar -> plantilla -> escalar (spec §3.2)."""
        if ctx.degraded:
            return self._fallback(node_config, state, ctx, UsageMeter(ctx.clock), [], [], 0, degraded=True)
        names = _allowed_names(node_config, state)
        allowed_ids = frozenset(state.facts[name].fact_id for name in names)
        validation = replace(ctx.validation, allowed=allowed_ids)
        prompt = ctx.resolve_ref(EntityKind.prompt, node_config.prompt_ref)
        meter = UsageMeter(ctx.clock)
        base_inputs = _inputs(names, state, ctx)
        rejected: list[RejectedDraft] = []
        last_failures: list[Failure] = []
        regenerations = 0
        for attempt in range(1 + ctx.max_regenerations):
            inputs = dict(base_inputs)
            if last_failures:
                inputs["validation_feedback"] = [
                    {"check": f.check, "detail": f.detail} for f in last_failures]
            try:
                result = meter.call(partial(ctx.gateway.generate, prompt, inputs, ctx.locale, DRAFT_SCHEMA))
            except Exception:
                break
            parsed = parse_draft(result.output)
            if isinstance(parsed, Failure):
                text, failures = "", [parsed]
            else:
                text, failures = parsed.text, validate(parsed, validation).failures
            if not failures:
                outcome = ValidatorOutcome(ok=True, regenerations=attempt)
                event = _event(state, ctx, "generated", False, outcome, meter)
                return Message(kind="generated", text=text, locale=ctx.locale), rejected, [event]
            # Con PII en claro el texto no se conserva: iría al transcript con el dato que se quiso evitar.
            has_clear_pii = any(f.check == "tokens_pii" and f.detail.startswith(PII_DETAIL_PREFIX)
                                for f in failures)
            reason = "; ".join(f"{f.check}: {f.detail}" for f in failures)
            rejected.append(RejectedDraft(
                text_model="" if has_clear_pii else text, reason=reason,
                failures=list(dict.fromkeys(f.check for f in failures))))
            last_failures = failures
            regenerations = attempt
        return self._fallback(node_config, state, ctx, meter, rejected, last_failures, regenerations,
                              degraded=False)

    def _fallback(self, node_config: GenerateConfig, state: RunState, ctx: ResponderContext,
                  meter: UsageMeter, rejected: list[RejectedDraft], failures: list[Failure],
                  regenerations: int, *, degraded: bool,
                  ) -> tuple[Message | EscalationRequest, list[RejectedDraft], list[EngineEvent]]:
        escalation = EscalationRequest(
            reason_code="validation_failed", target_queue=ctx.default_target_queue, priority=ctx.priority)
        try:
            ref = ctx.resolve_ref(EntityKind.template, node_config.fallback_template_ref)
            message = self.template(ref, ctx.locale, ctx.facts_model_view_by_name)
        except TemplateUnavailable:
            return escalation, rejected, [_failed_event(state, ctx, failures, regenerations, meter)]
        if check_tokens_pii(Draft(text=message.text, citations=[]), ctx.validation):
            return escalation, rejected, [_failed_event(state, ctx, failures, regenerations, meter)]
        outcome = ValidatorOutcome(ok=degraded, failures=list(dict.fromkeys(f.check for f in failures)),
                                   regenerations=regenerations)
        return message, rejected, [_event(state, ctx, "template", True, outcome, meter)]


def _allowed_names(node_config: GenerateConfig, state: RunState) -> list[str]:
    names: list[str] = []
    for path in node_config.allowed_facts:
        match = _FACT_PATH.fullmatch(path)
        if match is not None and match.group(1) in state.facts and match.group(1) not in names:
            names.append(match.group(1))
    return names


def _inputs(names: list[str], state: RunState, ctx: ResponderContext) -> dict[str, JsonValue]:
    """Solo hechos permitidos, en vista `model`, con su `fact_id` para que el modelo pueda citarlos."""
    facts: dict[str, JsonValue] = {}
    for name in names:
        view = ctx.facts_model_view_by_name.get(name)
        entry: dict[str, JsonValue] = {"fact_id": state.facts[name].fact_id}
        if isinstance(view, dict):
            entry.update(view)
        facts[name] = entry
    return {"facts": facts}


def _failed_event(state: RunState, ctx: ResponderContext, failures: list[Failure], regenerations: int,
                  meter: UsageMeter) -> EngineEvent:
    """`response_failed`: la cadena escala y el uso del LLM (también el fallido) queda en la auditoría."""
    outcome = ValidatorOutcome(ok=False, failures=list(dict.fromkeys(f.check for f in failures)),
                               regenerations=regenerations)
    payload = ResponseFailedPayload.model_validate({
        "node_id": ctx.node_id, "reason_code": "validation_failed", "validator": outcome,
        "claims": sorted(ctx.claims), "llm": meter.usage()})
    return ResponseFailed.model_validate({
        "event_id": ctx.ids.new_id(IdKind.event), "run_id": state.run_id, "turn_id": ctx.turn_id,
        "session_id": state.session_id, "release": ctx.release, "ts": ctx.clock.now(), "payload": payload})


def _event(state: RunState, ctx: ResponderContext, kind: str, fallback_used: bool, outcome: ValidatorOutcome,
           meter: UsageMeter) -> EngineEvent:
    payload = ResponseEmittedPayload.model_validate({
        "node_id": ctx.node_id, "kind": kind, "validator": outcome, "fallback_used": fallback_used,
        "claims": sorted(ctx.claims), "transcript_fp": None, "llm": meter.usage()})
    return ResponseEmitted.model_validate({
        "event_id": ctx.ids.new_id(IdKind.event), "run_id": state.run_id, "turn_id": ctx.turn_id,
        "session_id": state.session_id, "release": ctx.release, "ts": ctx.clock.now(), "payload": payload})
