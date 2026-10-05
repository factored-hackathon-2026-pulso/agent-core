"""Sugerencias estructuradas del copiloto (ADR 0026, M0 §2.8): la lista tipada que un nodo `suggest` entrega.

Cuatro tipos cerrados y una lista que puede estar vacía (un resultado válido). Los límites de longitud son los
de la plataforma que las muestra: lo que el núcleo acepta, ella lo conserva entero (nada se recorta en
silencio). Una `action` es una escritura *preparada*: `executable` es `false` en esta etapa y no hay forma de
construirla con otro valor.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter

from agent_core.domain.base import Locale, Model
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import ReasonCodeStr

SUGGESTION_TYPES: tuple[str, ...] = ("reply", "tool", "action", "escalate")

_Text = StringConstraints(strip_whitespace=True, min_length=1)
Body = Annotated[str, _Text, StringConstraints(max_length=4000)]
Short = Annotated[str, _Text, StringConstraints(max_length=300)]
Medium = Annotated[str, _Text, StringConstraints(max_length=500)]
ToolRefText = Annotated[str, _Text, StringConstraints(max_length=120)]
Citation = Annotated[str, _Text, StringConstraints(max_length=120)]
Evidence = Annotated[str, _Text, StringConstraints(max_length=300)]


class ReplySuggestion(Model):
    """Borrador para el cliente. Lo redacta el modelo; M8 valida cifras y fechas contra los hechos citados."""

    type: Literal["reply"] = "reply"
    text: Body
    citations: list[Citation] = Field(default_factory=list, max_length=10)
    language: Locale


class ToolSuggestion(Model):
    """Una lectura que conviene mirar. `tool` ∈ `suggest.tools_allowed`; el sujeto nunca es argumento."""

    type: Literal["tool"] = "tool"
    tool: ToolRefText
    args: dict[str, JsonValue] = Field(default_factory=dict)
    why: Medium


class ActionSuggestion(Model):
    """Una escritura preparada y NO ejecutable (`executable` es siempre `false`, ADR 0026 §7)."""

    type: Literal["action"] = "action"
    tool: ToolRefText
    args: dict[str, JsonValue] = Field(default_factory=dict)
    summary: Short
    executable: Literal[False] = False


class EscalateSuggestion(Model):
    """Recomendación de escalar. El motivo y la evidencia los fija el flow (una `rule`), no el modelo; el
    modelo solo redacta `motive_draft`."""

    type: Literal["escalate"] = "escalate"
    reason_code: ReasonCodeStr
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    motive_draft: Medium


Suggestion = Annotated[
    ReplySuggestion | ToolSuggestion | ActionSuggestion | EscalateSuggestion, Field(discriminator="type")
]

_LIST = TypeAdapter(list[Suggestion])


def parse_suggestions(value: Sequence[object]) -> list[Suggestion]:
    """La lista tipada; `ValidationError` si algún elemento no cumple su tipo."""
    return _LIST.validate_python(list(value))


def suggestion_counts(suggestions: Sequence[Suggestion]) -> dict[str, int]:
    """Total y cantidad por tipo: lo único de la lista que viaja a un evento (nunca texto)."""
    by_type: Counter[str] = Counter(s.type for s in suggestions)
    return {"count": len(suggestions), **{name: by_type.get(name, 0) for name in SUGGESTION_TYPES}}


def suggestion_texts(suggestion: Suggestion) -> list[str]:
    """Todo el texto libre de una sugerencia (para comprobar PII o valores sensibles)."""
    match suggestion:
        case ReplySuggestion():
            return [suggestion.text]
        case ToolSuggestion():
            return [suggestion.why]
        case ActionSuggestion():
            return [suggestion.summary]
        case EscalateSuggestion():
            return [suggestion.motive_draft, *suggestion.evidence]


class SuggestEscalation(Model):
    """Lo que el flow fija de una recomendación de escalar: motivo y evidencia salen de la `rule`."""

    reason_code: ReasonCodeStr
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=5)
