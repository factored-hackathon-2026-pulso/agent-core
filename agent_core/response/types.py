"""Tipos públicos del validador de respuesta de M8 (spec §2)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from agent_core.domain import FactSource, JsonValue, LanguageDetection, PageMeta
from agent_core.domain.base import Model
from agent_core.guards import LangThresholds
from agent_core.response.numbers import NumberFormat
from agent_core.views import TokenVault

CheckId = Literal[
    "format", "citations", "numbers", "tokens_pii", "language", "page_citations", "page_audience"]


class Draft(Model):
    text: str
    citations: list[str]  # fact_id | page_ref


class Failure(Model):
    check: CheckId
    detail: str


class ValidationResult(Model):
    ok: bool  # ok == (failures == [])
    failures: list[Failure]


@dataclass(frozen=True, slots=True, repr=False)
class ValidationContext:
    facts_model_view: Mapping[str, JsonValue]  # por fact_id, ya en vista `model`
    fact_sources: Mapping[str, FactSource]  # por fact_id
    allowed: frozenset[str]  # fact_ids que el nodo permite citar (las páginas van en `page_refs`)
    pages_model_view: Mapping[str, JsonValue]  # `page_ref` → contenido en vista `model` (M12)
    vault: TokenVault
    locale: str
    lang_cfg: LanguageDetection
    lang_thresholds: LangThresholds
    supported_locales: tuple[str, ...]
    # Cierre que liga `ViewService.find_clear_pii` con los hechos `full` sin exponerlos: devuelve rutas.
    find_clear_pii: Callable[[str], list[str]]
    number_format: NumberFormat | None = None  # dato opcional producido fuera de M8
    # M12 (comprobaciones 6 y 7): páginas de los `save_as` de `knowledge_from`, sus metadatos y el instante
    page_refs: frozenset[str] = frozenset()  # `ruta@snapshot#ancla` que el nodo permite citar
    pages_meta: Mapping[str, PageMeta] = field(default_factory=dict)
    customer_facing: bool = False  # la respuesta la lee un cliente: solo páginas public + approved + vigentes
    # instante del `Clock` para la vigencia; sin él, una respuesta al cliente falla
    now: datetime | None = None

    def __repr__(self) -> str:
        # No muestra hechos, vault ni formato: pueden contener PII o tokens.
        return f"ValidationContext(locale={self.locale!r}, allowed={len(self.allowed)} citas)"


def parse_draft(output: JsonValue) -> Draft | Failure:
    """Comprobación 1 (`format`): la salida del gateway debe ser `{text, citations}`. No lanza ni hace eco."""
    try:
        return Draft.model_validate(output)
    except ValidationError:
        return Failure(check="format", detail="la salida del gateway no es {text, citations}")
