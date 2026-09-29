"""`Responder` de M8: plantillas y cadena `respond(generate)` (spec §3.2). Sin hora ni azar propios."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agent_core.domain import (
    DomainError,
    EntityKind,
    EntityRef,
    JsonValue,
    Locale,
    Message,
    RefSpec,
    Template,
)
from agent_core.ports import Clock, IdSource, LLMGateway, RegistryPort
from agent_core.response.templates import TemplateUnavailable, render_template_text
from agent_core.response.types import ValidationContext


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
