"""Ayudantes de las pruebas de M8. Solo datos sintéticos."""

import importlib.metadata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from agent_core.domain import Fact, FactSource, JsonValue, LanguageDetection
from agent_core.guards import UNCALIBRATED
from agent_core.response.numbers import NumberFormat
from agent_core.response.types import ValidationContext
from agent_core.views import TokenVault
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider

DETECTOR = f"lingua@{importlib.metadata.version('lingua-language-detector')}"
DOT_COMMA = NumberFormat(thousands=".", decimal=",")  # 1.234,56
COMMA_DOT = NumberFormat(thousands=",", decimal=".")  # 1,234.56
TS = datetime(2026, 9, 29, tzinfo=UTC)


def make_lang(**over: Any) -> LanguageDetection:
    base: dict[str, Any] = {
        "id": "lang", "version": "1.0.0", "detector": DETECTOR, "candidates": ["es", "pt"],
        "unsupported": ["en"], "min_letters": 12, "min_letters_unsupported": 24, "thresholds_from": "calib-1",
    }
    return LanguageDetection.model_validate(base | over)


def make_fact(fact_id: str, value: JsonValue, kind: str = "tool", **source: Any) -> Fact:
    src = {"kind": kind, "ref": f"{kind}-{fact_id}@1.0.0"} | source
    return Fact(fact_id=fact_id, value=value, source=FactSource.model_validate(src), ts=TS)


def make_ctx(facts: Mapping[str, Fact] | None = None, allowed: set[str] | None = None,
             **over: Any) -> ValidationContext:
    """Contexto con los hechos dados ya en vista `model` (los sintéticos no llevan PII).

    `allowed` es por defecto todos los `fact_id`; `number_format` es `None` salvo que se pase."""
    facts = facts or {}
    base: dict[str, Any] = {
        "facts_model_view": {fid: f.value for fid, f in facts.items()},
        "fact_sources": {fid: f.source for fid, f in facts.items()},
        "allowed": frozenset(facts if allowed is None else allowed),
        "pages_model_view": {},
        "vault": TokenVault("run-0001", FakeKeyProvider.default(), FakeIds()),
        "locale": "es",
        "lang_cfg": make_lang(),
        "lang_thresholds": UNCALIBRATED,
        "supported_locales": ("es", "pt"),
        "find_clear_pii": lambda text: [],
        "number_format": None,
    }
    return ValidationContext(**(base | over))
