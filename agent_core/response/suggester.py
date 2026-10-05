"""`Suggester` de M8: la cadena del nodo `suggest` (ADR 0026): generar → validar → regenerar una vez → fallar.

Una sola llamada por intento, sin ReAct: el modelo *recomienda* lecturas y *prepara* acciones, no las ejecuta.
La salida es una lista plana que se valida elemento por elemento y se convierte en `Suggestion` tipadas:

- `reply`: M8 completo (formato, citas a hechos de `reads`, cifras contra los hechos citados, PII, idioma).
- `tool`/`action`: la tool ∈ catálogo del nodo y sus `args` cumplen el `args_schema` (el sujeto nunca es un
  argumento: ningún esquema lo declara y `additionalProperties` es falso). `action` solo si el nodo declara
  `actions_allowed`.
- `escalate`: el modelo NO la crea. Solo existe si el flow la declaró (`SuggestEscalation`, que sale de una
  `rule`); el modelo aporta únicamente `motive_draft`, y el motivo y la evidencia son los del flow.
- Todo texto libre (`why`, `summary`, `motive_draft`): sin PII en claro, sin tokens y con las cifras
  respaldadas por algún hecho de `reads`.

Ninguna falla repite el texto del modelo: el detalle lleva la posición, nunca el contenido.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from pydantic import ValidationError

from agent_core.domain import (
    EntityRef,
    EscalateSuggestion,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    LlmUsage,
    Locale,
    ReplySuggestion,
    SuggestEscalation,
    Suggestion,
    ToolSuggestion,
    check_output,
    dumps,
)
from agent_core.domain.suggestions import ActionSuggestion
from agent_core.ports import Clock, LLMGateway
from agent_core.response.checks import CHECKS, TOKEN_RE
from agent_core.response.types import Draft, ValidationContext
from agent_core.response.usage import UsageMeter
from agent_core.response.validate import validate

SUGGESTIONS_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suggestions"],
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type"],
                "properties": {
                    "type": {"type": "string", "enum": ["reply", "tool", "action", "escalate"]},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "language": {"type": "string"},
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "why": {"type": "string"},
                    "summary": {"type": "string"},
                    "motive_draft": {"type": "string"},
                },
            },
        }
    },
}

_FIELDS: Mapping[str, frozenset[str]] = {
    "reply": frozenset({"type", "text", "citations", "language"}),
    "tool": frozenset({"type", "tool", "args", "why"}),
    "action": frozenset({"type", "tool", "args", "summary"}),
    "escalate": frozenset({"type", "motive_draft"}),
}
_ORDER = {"escalate": 0, "reply": 1, "tool": 2, "action": 3}
# Un texto que no es el borrador al cliente: formato, cifras contra los hechos de `reads` y PII.
_TEXT_CHECKS = tuple(entry for entry in CHECKS if entry[0] in ("format", "numbers", "tokens_pii"))


@dataclass(frozen=True, slots=True)
class ToolEntry:
    """Una tool del catálogo del nodo: la referencia como se declaró, la exacta y lo que ve el modelo."""

    ref: str
    exact: str
    description: str
    args_schema: dict[str, JsonValue]


@dataclass(frozen=True, slots=True, repr=False)
class SuggesterContext:
    """Puertos y datos de un nodo `suggest`; los arma quien cablea M2 (composición). `inputs` son las rutas de
    `reads` en vista `model`; `citable` mapea el nombre de cada hecho leído a su `fact_id` (lo único que un
    borrador puede citar); `validation.allowed` debe ser esos mismos ids."""

    gateway: LLMGateway
    clock: Clock
    prompt: EntityRef
    locale: Locale
    goal: str
    inputs: dict[str, JsonValue]
    citable: Mapping[str, str]
    tools: tuple[ToolEntry, ...]
    actions: tuple[ToolEntry, ...]
    escalation: SuggestEscalation | None
    max_items: int
    validation: ValidationContext
    degraded: bool = False
    max_regenerations: int = 1

    def __repr__(self) -> str:
        # `inputs` y los hechos son datos del cliente: no se muestran
        return f"SuggesterContext(locale={self.locale!r}, degraded={self.degraded})"


@dataclass(frozen=True, slots=True)
class SuggestOutcome:
    """La lista validada y ordenada, o los ids de las comprobaciones que fallaron (sin datos) y el uso."""

    suggestions: list[Suggestion] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    regenerations: int = 0
    llm: LlmUsage | None = None


@dataclass(frozen=True, slots=True)
class _Fail:
    check: str
    detail: str


class Suggester:
    def generate(self, ctx: SuggesterContext) -> SuggestOutcome:
        if ctx.degraded:
            return SuggestOutcome(failures=["degraded"])
        meter = UsageMeter(ctx.clock)
        last: list[_Fail] = []
        for attempt in range(1 + ctx.max_regenerations):
            try:
                result = meter.call(partial(
                    ctx.gateway.generate, ctx.prompt, _inputs(ctx, last), ctx.locale, SUGGESTIONS_SCHEMA))
            except GatewayError as error:
                if error.kind is not GatewayErrorKind.invalid_output:
                    return SuggestOutcome(failures=[f"gateway_{error.kind.value}"], regenerations=attempt,
                                          llm=meter.usage())
                last = [_Fail("format", "la salida del gateway no cumple el esquema")]
                continue
            except Exception:
                return SuggestOutcome(failures=["gateway_error"], regenerations=attempt, llm=meter.usage())
            suggestions, last = _check(result.output, ctx)
            if not last:
                return SuggestOutcome(suggestions=suggestions, regenerations=attempt, llm=meter.usage())
        return SuggestOutcome(failures=list(dict.fromkeys(f.check for f in last)),
                              regenerations=ctx.max_regenerations, llm=meter.usage())


def _catalog(entries: Sequence[ToolEntry]) -> list[JsonValue]:
    return [{"tool": e.ref, "description": e.description, "args_schema": e.args_schema} for e in entries]


def _inputs(ctx: SuggesterContext, failures: list[_Fail]) -> dict[str, JsonValue]:
    """Lo que ve el modelo: solo la vista `model`. La evidencia de la escalación NO va: es del flow."""
    escalation: JsonValue = (
        None if ctx.escalation is None else {"required": True, "reason_code": ctx.escalation.reason_code})
    inputs: dict[str, JsonValue] = {
        "goal": ctx.goal, "inputs": ctx.inputs, "citable_facts": dict(ctx.citable),
        "tools": _catalog(ctx.tools), "actions": _catalog(ctx.actions), "escalation": escalation,
        "max_items": ctx.max_items, "output_schema": SUGGESTIONS_SCHEMA}
    if failures:
        inputs["validation_feedback"] = [{"check": f.check, "detail": f.detail} for f in failures]
    return inputs


def _check(output: JsonValue, ctx: SuggesterContext) -> tuple[list[Suggestion], list[_Fail]]:
    error = check_output(SUGGESTIONS_SCHEMA, output)
    if error is not None:
        del error  # su mensaje puede traer un nombre de propiedad escrito por el modelo
        return [], [_Fail("format", "la salida no cumple el esquema")]
    assert isinstance(output, dict)
    items = output["suggestions"]
    assert isinstance(items, list)
    if len(items) > ctx.max_items:
        return [], [_Fail("too_many", "más sugerencias que el máximo del nodo")]
    built: list[Suggestion] = []
    motive: str | None = None
    failures: list[_Fail] = []
    kinds: list[str] = []
    for position, item in enumerate(items, start=1):
        assert isinstance(item, dict)
        kind = str(item["type"])
        kinds.append(kind)
        extra = sorted(set(item) - _FIELDS[kind])
        if extra:
            failures.append(_Fail("format", f"sugerencia {position}: propiedad no permitida para {kind}"))
            continue
        try:
            if kind == "escalate":
                found = _escalate(item, position, ctx)
                if isinstance(found, str):
                    motive = found
                else:
                    failures += found
            else:
                made, problems = _typed(kind, item, position, ctx)
                failures += problems
                if made is not None:
                    built.append(made)
        except ValidationError:
            failures.append(_Fail("format", f"sugerencia {position}: no cumple el tipo {kind}"))
    failures += _shape(kinds, ctx)
    if failures:
        return [], failures
    if ctx.escalation is not None and motive is not None:
        try:
            built.append(EscalateSuggestion(reason_code=ctx.escalation.reason_code,
                                            evidence=list(ctx.escalation.evidence), motive_draft=motive))
        except ValidationError:  # p. ej. un motivo de más de 500 caracteres: nunca se propaga con el valor
            return [], [_Fail("format", "el motivo de la escalación no cumple el tipo")]
    return sorted(built, key=lambda s: _ORDER[s.type]), []


def _shape(kinds: list[str], ctx: SuggesterContext) -> list[_Fail]:
    """Una respuesta y una escalación a lo sumo (la plataforma solo conserva una de cada una: un segundo
    borrador se perdería sin aviso), y la escalación declarada por el flow tiene que estar."""
    failures: list[_Fail] = []
    if kinds.count("reply") > 1 or kinds.count("escalate") > 1:
        failures.append(_Fail("duplicate", "más de una respuesta o de una escalación"))
    if ctx.escalation is not None and "escalate" not in kinds:
        failures.append(_Fail("escalate_missing", "el flow declaró una escalación y falta su borrador"))
    return failures


def _escalate(item: dict[str, Any], position: int, ctx: SuggesterContext) -> str | list[_Fail]:
    if ctx.escalation is None:
        return [_Fail("escalate_not_allowed", f"sugerencia {position}: el flow no declaró una escalación")]
    motive = item.get("motive_draft")
    if not isinstance(motive, str):
        return [_Fail("format", f"sugerencia {position}: falta motive_draft")]
    problems = _text_problems(motive, position, ctx)
    return problems if problems else motive


def _typed(kind: str, item: dict[str, Any], position: int, ctx: SuggesterContext,
           ) -> tuple[Suggestion | None, list[_Fail]]:
    if kind == "reply":
        return _reply(item, position, ctx)
    entries = ctx.tools if kind == "tool" else ctx.actions
    absent = "tool_not_allowed" if kind == "tool" else "action_not_allowed"
    ref = item.get("tool")
    entry = next((e for e in entries if ref in (e.ref, e.exact)), None)
    if entry is None:
        return None, [_Fail(absent, f"sugerencia {position}: tool fuera del catálogo del nodo")]
    args = item.get("args", {})
    if check_output(entry.args_schema, args) is not None or _unknown_args(entry, args):
        return None, [_Fail("args_invalid", f"sugerencia {position}: args no cumple el esquema de la tool")]
    leaked = _args_problems(args, position, ctx)
    if leaked:
        return None, leaked
    label = item.get("why" if kind == "tool" else "summary")
    if not isinstance(label, str):
        return None, [_Fail("format", f"sugerencia {position}: falta el texto")]
    problems = _text_problems(label, position, ctx)
    if problems:
        return None, problems
    if kind == "tool":
        return ToolSuggestion(tool=entry.ref, args=args, why=label), []
    return ActionSuggestion(tool=entry.ref, args=args, summary=label), []


def _reply(item: dict[str, Any], position: int,
           ctx: SuggesterContext) -> tuple[Suggestion | None, list[_Fail]]:
    text, citations, language = item.get("text"), item.get("citations", []), item.get("language")
    if not isinstance(text, str) or not isinstance(language, str):
        return None, [_Fail("format", f"sugerencia {position}: faltan text o language")]
    failures: list[_Fail] = []
    if language != ctx.locale:
        failures.append(_Fail("language", f"sugerencia {position}: idioma distinto del del run"))
    failures += _no_token(text, position)
    failures += [_Fail(f.check, f"sugerencia {position}: {f.detail}")
                 for f in validate(Draft(text=text, citations=list(citations)), ctx.validation).failures]
    if failures:
        return None, failures
    return ReplySuggestion(text=text, citations=list(citations), language=language), []


def _unknown_args(entry: ToolEntry, args: object) -> bool:
    """Un argumento que el esquema de la tool no declara (aunque no ponga `additionalProperties: false`): el
    sujeto nunca es argumento y nada se cuela por una tool con esquema abierto."""
    props = entry.args_schema.get("properties")
    return isinstance(args, dict) and not set(args) <= set(props if isinstance(props, dict) else {})


def _args_problems(args: object, position: int, ctx: SuggesterContext) -> list[_Fail]:
    """Los `args` también salen hacia la plataforma: ni tokens ni PII en claro (un entero o un texto)."""
    text = dumps(args)
    failures = _no_token(text, position)
    if ctx.validation.find_clear_pii(text):
        failures.append(_Fail("tokens_pii", f"sugerencia {position}: PII en claro en args"))
    return failures


_URL = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,15}://|www\.)")


def _no_token(text: str, position: int) -> list[_Fail]:
    """Una sugerencia no lleva tokens (se mostraría `⟦pii:1⟧` o, peor, el dato que el token oculta) ni
    enlaces: un texto escrito por un modelo a partir de datos del cliente no debe llevar al asesor a una URL
    (`http://`, `https://`, cualquier `esquema://` o `www.`). Diseño mínimo; ADR 0026 §6."""
    failures: list[_Fail] = []
    if TOKEN_RE.search(text):
        failures.append(_Fail("tokens_pii", f"sugerencia {position}: token en el texto"))
    if _URL.search(text):
        failures.append(_Fail("url", f"sugerencia {position}: enlace en el texto"))
    return failures


def _text_problems(text: str, position: int, ctx: SuggesterContext) -> list[_Fail]:
    draft = Draft(text=text, citations=list(ctx.citable.values()))
    failures = _no_token(text, position)
    failures += [_Fail(f.check, f"sugerencia {position}: {f.detail}")
                 for f in validate(draft, ctx.validation, _TEXT_CHECKS).failures]
    return failures
