"""Tipos públicos del validador de respuesta de M8 (spec §2)."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from agent_core.domain import FactSource, JsonValue, LanguageDetection
from agent_core.domain.base import Model
from agent_core.guards import LangThresholds
from agent_core.response.numbers import NumberFormat
from agent_core.views import TokenVault

CheckId = Literal["format", "citations", "numbers", "tokens_pii", "language"]


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
    allowed: frozenset[str]  # fact_ids/page_refs que el nodo permite citar
    pages_model_view: Mapping[str, JsonValue]  # vacío hasta M12
    vault: TokenVault
    locale: str
    lang_cfg: LanguageDetection
    lang_thresholds: LangThresholds
    supported_locales: tuple[str, ...]
    # Cierre que liga `ViewService.find_clear_pii` con los hechos `full` sin exponerlos: devuelve rutas.
    find_clear_pii: Callable[[str], list[str]]
    number_format: NumberFormat | None = None  # dato opcional producido fuera de M8

    def __repr__(self) -> str:
        # No muestra hechos, vault ni formato: pueden contener PII o tokens.
        return f"ValidationContext(locale={self.locale!r}, allowed={len(self.allowed)} citas)"


def parse_draft(output: JsonValue) -> Draft | Failure:
    """Comprobación 1 (`format`): la salida del gateway debe ser `{text, citations}`. No lanza ni hace eco."""
    try:
        return Draft.model_validate(output)
    except ValidationError:
        return Failure(check="format", detail="la salida del gateway no es {text, citations}")
