"""Wildcard threshold for runtime choices (ADR 0021, P3)."""

from agent_core.decision import WILDCARD_LABEL
from agent_core.decision.service import DecisionService
from tests.m05.helpers import artifact


def _artifact(thresholds: dict[tuple[str, str, str, str], float]):  # type: ignore[no-untyped-def]
    return artifact(method="none", thresholds=thresholds)


def test_wildcard_label_is_a_star() -> None:
    assert WILDCARD_LABEL == "*"


def test_threshold_falls_back_to_the_wildcard_label() -> None:
    art = _artifact({("choice", WILDCARD_LABEL, "llm_structured", "es"): 0.6})
    assert DecisionService._passes(art, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.7)
    assert not DecisionService._passes(art, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.5)


def test_a_specific_label_wins_over_the_wildcard() -> None:
    art = _artifact({("choice", WILDCARD_LABEL, "llm_structured", "es"): 0.6,
                     ("choice", "disputas", "llm_structured", "es"): 0.9})
    assert not DecisionService._passes(art, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.7)
    assert DecisionService._passes(art, "choice", {"choice": "saldos"}, "llm_structured", "es", 0.7)


def test_wildcard_is_per_language_and_provider() -> None:
    art = _artifact({("choice", WILDCARD_LABEL, "llm_structured", "es"): 0.6})
    assert not DecisionService._passes(art, "choice", {"choice": "x"}, "llm_structured", "pt", 0.99)
    assert not DecisionService._passes(art, "choice", {"choice": "x"}, "jev", "es", 0.99)


def test_without_wildcard_an_absent_combination_still_never_passes() -> None:
    art = _artifact({})
    assert not DecisionService._passes(art, "choice", {"choice": "x"}, "llm_structured", "es", 1.0)
