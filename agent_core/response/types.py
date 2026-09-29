"""Tipos públicos del validador de respuesta de M8 (spec §2).

Nota: `ValidationContext` trae por ahora solo los campos del spec §2 (con `number_format` opcional,
decidido 2026-09-29). Los campos que necesitan las comprobaciones de tokens/PII y de idioma están pendientes
de decisión (spec §11) y no se añaden aquí.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from agent_core.domain import FactSource, JsonValue, LanguageDetection
from agent_core.domain.base import Model
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
