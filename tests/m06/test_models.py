"""Modelos de las guardas (M6 §2)."""

import pytest
from pydantic import ValidationError

from agent_core.guards.models import (
    UNCALIBRATED,
    GuardResult,
    InjectionResult,
    LangDecision,
    LangThresholds,
)


def _lang(**over: object) -> LangDecision:
    base: dict[str, object] = {
        "decision": "kept", "locale": "es", "locale_prior": "es", "letters": 30,
        "top2": [("es", 0.91), ("pt", 0.07)], "detector": "lingua@2.1.1",
    }
    return LangDecision.model_validate(base | over)


def test_uncalibrated_thresholds_are_disabled() -> None:
    assert UNCALIBRATED.switch_threshold == 1.0
    assert UNCALIBRATED.unsupported_threshold == 1.0
    assert not UNCALIBRATED.switch_active
    assert not UNCALIBRATED.unsupported_active


def test_calibrated_thresholds_are_active() -> None:
    thresholds = LangThresholds(switch_threshold=0.9, unsupported_threshold=0.85, min_distance=0.2)
    assert thresholds.switch_active and thresholds.unsupported_active


def test_thresholds_reject_out_of_range() -> None:
    with pytest.raises(ValidationError):
        LangThresholds(switch_threshold=1.2)
    with pytest.raises(ValidationError):
        LangThresholds(min_distance=-0.1)


def test_lang_decision_rejects_unknown_decision() -> None:
    with pytest.raises(ValidationError):
        _lang(decision="maybe")


def test_to_output_maps_to_m0_guards_output() -> None:
    result = GuardResult(
        lang=_lang(decision="switched", locale="pt", locale_prior="es"),
        size_ok=True,
        injection=InjectionResult(flagged=True, signals=["ignore-instructions"], ruleset="inj@1.0.0"),
    )
    output = result.to_output()
    assert output.lang.decision == "switched"
    assert output.lang.locale == "pt" and output.lang.locale_prior == "es"
    assert [(s.lang, s.score) for s in output.lang.top2] == [("es", 0.91), ("pt", 0.07)]
    assert output.lang.detector == "lingua@2.1.1" and output.lang.letters == 30
    assert output.injection.flagged and output.injection.signals == ["ignore-instructions"]
    assert output.injection.ruleset == "inj@1.0.0"
    assert output.size_ok is True
