"""Ayudantes de las pruebas de M6. Solo datos sintéticos."""

import importlib.metadata
from typing import Any

from agent_core.domain import Agent, LanguageDetection, Release, RunState
from agent_core.guards.models import LangThresholds
from agent_core.guards.service import GuardService
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.injection_fixtures import RULESET

VERSION = importlib.metadata.version("lingua-language-detector")
DETECTOR = f"lingua@{VERSION}"
CALIBRATED = LangThresholds(switch_threshold=0.90, unsupported_threshold=0.90, min_distance=0.20)

ES = "Quiero consultar el saldo de mi cuenta y revisar los últimos movimientos"
PT = "Gostaria de consultar o saldo da minha conta e revisar as últimas transações realizadas"
EN = "I would like to check the balance of my account and review the latest transactions please"

_TEMPLATES = {
    "clarify": "t/aclarar", "abstain": "t/abstencion", "handoff": "t/traspaso", "pending_ack": "t/acuse",
    "pending_offer": "t/oferta", "unsupported_language": "t/idioma_no_soportado",
    "input_too_large": "t/mensaje_largo",
}
_BUDGETS = {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
            "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000}


def make_agent(**over: Any) -> Agent:
    base: dict[str, Any] = {
        "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "disputa-cargo@1",
        "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"], "default_locale": "es", "tools_allowed": [],
        "budgets": _BUDGETS, "understand": "understand@1", "templates": _TEMPLATES, "max_clarifications": 2,
        "on_clarify_exhausted": "escalate", "default_target_queue": "general",
    }
    return Agent.model_validate(base | over)


def make_lang(**over: Any) -> LanguageDetection:
    base: dict[str, Any] = {
        "id": "lang", "version": "1.0.0", "detector": DETECTOR, "candidates": ["es", "pt"],
        "unsupported": ["en"], "min_letters": 12, "min_letters_unsupported": 24, "thresholds_from": "calib-1",
    }
    return LanguageDetection.model_validate(base | over)


def make_release(**over: Any) -> Release:
    base: dict[str, Any] = {
        "id": "rel-1", "status": "active", "language_detection": "lang@1.0.0",
        "injection_ruleset": "injection-rules@1.0.0", "max_input_chars": 4000,
    }
    return Release.model_validate(base | over)


def make_state(**over: Any) -> RunState:
    return run_state(**over)


def make_service(*, lang: LanguageDetection | None = None,
                 calibrations: dict[str, LangThresholds] | None = None) -> GuardService:
    registry = InMemoryRegistry()
    registry.add(lang or make_lang(), RULESET)
    cal = {"calib-1": CALIBRATED} if calibrations is None else calibrations
    return GuardService(registry, FakeClock(), FakeIds(), cal)
