"""Ayudantes de las pruebas de M8. Solo datos sintéticos."""

import importlib.metadata
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_core.domain import (
    Fact,
    FactSource,
    GenerateConfig,
    JsonValue,
    LanguageDetection,
    Prompt,
    RunState,
    Template,
)
from agent_core.guards import UNCALIBRATED
from agent_core.ports import GenerationResult
from agent_core.response.numbers import NumberFormat
from agent_core.response.responder import Responder, ResponderContext
from agent_core.response.types import ValidationContext
from agent_core.views import TokenVault
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry

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


class ClockedGateway(ScriptedGateway):
    """Gateway guionado que avanza el `FakeClock` en cada llamada (latencia medible)."""

    def __init__(self, clock: FakeClock, ms: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clock, self._ms = clock, ms

    def generate(self, *args: Any, **kwargs: Any) -> GenerationResult:
        self._clock.advance(timedelta(milliseconds=self._ms))
        return super().generate(*args, **kwargs)


GOOD = "Tu disputa quedó radicada por un valor de 100,00 y te avisaremos cuando haya novedades."
CONFIG = GenerateConfig.model_validate({
    "prompt_ref": "resumen@1.0.0", "allowed_facts": ["facts.pqr.value.id"],
    "fallback_template_ref": "respaldo@1.0.0"})


class World:
    def __init__(self, script: Any = (), *, degraded: bool = False, fallback: str | None = None,
                 fallback_locales: dict[str, str] | None = None, ms: int = 100, **validation: Any) -> None:
        self.clock = FakeClock()
        self.ids = FakeIds()
        self.gateway = ClockedGateway(self.clock, ms, script)
        self.registry = InMemoryRegistry()
        default = "Tu disputa {{ facts.pqr.value.id }} quedó radicada."
        locales = fallback_locales or {"es": fallback or default}
        self.registry.add(
            Template.model_validate({"id": "respaldo", "version": "1.0.0", "locales": locales}),
            Prompt.model_validate({"id": "resumen", "version": "1.0.0", "locales": {"es": "resume"},
                                   "model_profile": "perfil@1.0.0"}))
        self.fact = make_fact("f-pqr", {"id": "pqr-1", "monto": "100.00"})
        self.state: RunState = run_state(facts={"pqr": self.fact})
        vctx = make_ctx({"f-pqr": self.fact}, {"f-pqr"}, number_format=DOT_COMMA, **validation)
        self.ctx = ResponderContext(
            gateway=self.gateway, clock=self.clock, ids=self.ids,
            resolve_ref=lambda kind, ref: ref.require_exact(), locale="es", degraded=degraded,
            release=self.state.release, turn_id="turn-0001", default_target_queue="general",
            priority="normal", facts_model_view_by_name={"pqr": {"value": self.fact.value}},
            validation=vctx, claims=frozenset({"b", "a"}), node_id="responder")
        self.responder = Responder(self.registry)

    def run(self):  # type: ignore[no-untyped-def]
        return self.responder.generate(CONFIG, self.state, self.ctx)
