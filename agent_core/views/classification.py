"""Clasificación de campos (M7 §3.1, ADR 0008). El catálogo es dato: la `FieldClassification` de la unidad 3
lo extiende o reemplaza; el motor solo trae el mecanismo."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import Field, PositiveInt, model_validator

from agent_core.domain.base import Model
from agent_core.views.tokens import TAG_PATTERN

FieldClass = Literal["pii_direct", "pii_quasi", "financial", "untrusted_text", "public"]

DEFAULT_TAG = "pii"


class QuasiRule(Model):
    """Operación genérica sobre un `pii_quasi`; qué operación aplica a cada campo lo decide el catálogo."""

    op: Literal["drop", "age_bucket"] = "drop"
    width: PositiveInt = 10


class FieldRule(Model):
    """Clase de un campo, tag de sus tokens y, si es `pii_quasi`, su generalización."""

    field_class: FieldClass
    tag: str = Field(default=DEFAULT_TAG, pattern=rf"^{TAG_PATTERN}$")
    quasi: QuasiRule = Field(default_factory=QuasiRule)

    @model_validator(mode="after")
    def _quasi_only_for_quasi(self) -> "FieldRule":
        if self.field_class != "pii_quasi" and self.quasi != QuasiRule():
            raise ValueError("quasi solo aplica a pii_quasi")
        return self


def _pii(tag: str) -> FieldRule:
    return FieldRule(field_class="pii_direct", tag=tag)


_QUASI = FieldRule(field_class="pii_quasi")
UNTRUSTED = FieldRule(field_class="untrusted_text")
UNCLASSIFIED = FieldRule(field_class="pii_direct", tag=DEFAULT_TAG)

# Catálogo por defecto: exactamente el §8.1 de la spec general. Lo demás llega con la FieldClassification.
DEFAULT_CATALOG: Mapping[str, FieldRule] = MappingProxyType({
    "first_name": _pii("name"),
    "last_name": _pii("name"),
    "document_number": _pii("doc"),
    "email": _pii("email"),
    "mobile_phone": _pii("tel"),
    "landline_phone": _pii("tel"),
    "address": _pii("addr"),
    "product_number": _pii("prod"),
    "ip_address": _pii("ip"),
    "date_of_birth": _QUASI,
    "postal_code": _QUASI,
    "latitude": _QUASI,
    "longitude": _QUASI,
    "complaints.description": UNTRUSTED,
    "complaints.resolution": UNTRUSTED,
    "call_transcripts.full_text": UNTRUSTED,
    "call_transcripts.customer_text": UNTRUSTED,
    "call_transcripts.agent_text": UNTRUSTED,
    "satisfaction_surveys.open_comments": UNTRUSTED,
})


def field_name(path: str) -> str:
    """Último segmento de una ruta `tabla.campo` (el nombre con el que se pregunta a la política)."""
    return path.rsplit(".", 1)[-1]


class FieldClassifier:
    """Busca la ruta exacta y luego el nombre del campo. Sin clasificar → `pii_direct`."""

    def __init__(self, catalog: Mapping[str, FieldRule] = DEFAULT_CATALOG) -> None:
        self._catalog = dict(catalog)

    def lookup(self, path: str) -> FieldRule | None:
        """Regla explícita del catálogo, o `None` si el campo no está clasificado."""
        rule = self._catalog.get(path)
        if rule is None and "." in path:
            rule = self._catalog.get(field_name(path))
        return rule

    def rule(self, path: str) -> FieldRule:
        rule = self.lookup(path)
        return UNCLASSIFIED if rule is None else rule

    def classify(self, path: str) -> FieldClass:
        return self.rule(path).field_class
