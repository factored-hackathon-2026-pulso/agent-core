"""Reglas de decisión de idioma con puntajes fabricados (M6 §3.1). T-M6-01…06 (parte pura)."""

from typing import Any

from agent_core.domain import LanguageDetection
from agent_core.guards.language import decide
from agent_core.guards.models import UNCALIBRATED, LangThresholds

CFG = LanguageDetection.model_validate({
    "id": "lang", "version": "1.0.0", "detector": "lingua@2.1.1", "candidates": ["es", "pt"],
    "unsupported": ["en"], "min_letters": 12, "min_letters_unsupported": 24,
})
CALIBRATED = LangThresholds(switch_threshold=0.90, unsupported_threshold=0.90, min_distance=0.20)
SUPPORTED = ["es", "pt"]


def _decide(*, letters: int = 40, top2: list[tuple[str, float]] | None = None,
            thresholds: LangThresholds = CALIBRATED, prior: str | None = "es", **over: Any) -> Any:
    return decide(letters=letters, top2=top2 if top2 is not None else [("es", 0.95), ("pt", 0.03)],
                  cfg=over.get("cfg", CFG), thresholds=thresholds, supported=SUPPORTED, prior=prior)


def test_t_m6_01_switch_to_pt_with_calibrated_thresholds() -> None:
    """T-M6-01: turno en PT con prior es y umbrales calibrados → switched a PT."""
    out = _decide(top2=[("pt", 0.97), ("es", 0.02)])
    assert (out.decision, out.locale, out.locale_prior) == ("switched", "pt", "es")
    assert out.top2 == [("pt", 0.97), ("es", 0.02)] and out.detector == "lingua@2.1.1"


def test_supported_same_as_prior_is_kept() -> None:
    out = _decide(top2=[("es", 0.97), ("pt", 0.02)])
    assert (out.decision, out.locale) == ("kept", "es")


def test_hysteresis_below_switch_threshold_is_kept() -> None:
    out = _decide(top2=[("pt", 0.80), ("es", 0.15)])
    assert (out.decision, out.locale) == ("kept", "es")


def test_t_m6_02_short_messages_keep_prior() -> None:
    """T-M6-02: "sí", "ok", "não" o solo un monto → short, conserva."""
    for letters in (0, 2, 3, 11):
        out = _decide(letters=letters, top2=[], prior="pt")
        assert (out.decision, out.locale, out.letters) == ("short", "pt", letters)
        assert out.top2 == []


def test_t_m6_03_portunol_without_distance_is_undetermined() -> None:
    """T-M6-03: portuñol sin distancia suficiente → undetermined, conserva."""
    out = _decide(top2=[("pt", 0.52), ("es", 0.45)])
    assert (out.decision, out.locale) == ("undetermined", "es")


def test_t_m6_04_single_english_word_is_not_unsupported() -> None:
    """T-M6-04: una palabra suelta en inglés no da unsupported (letters < min_letters → short)."""
    out = _decide(letters=7, top2=[], prior="es")
    assert out.decision == "short"


def test_unsupported_needs_length_even_above_threshold() -> None:
    out = _decide(letters=15, top2=[("en", 0.99), ("es", 0.005)])
    assert (out.decision, out.locale) == ("kept", "es")


def test_t_m6_05_long_english_sentence_is_unsupported() -> None:
    """T-M6-05: frase larga en inglés sobre umbral → unsupported (conserva el locale previo)."""
    out = _decide(letters=60, top2=[("en", 0.98), ("es", 0.01)])
    assert (out.decision, out.locale, out.locale_prior) == ("unsupported", "es", "es")


def test_unsupported_below_threshold_is_kept() -> None:
    out = _decide(letters=60, top2=[("en", 0.80), ("es", 0.05)])
    assert out.decision == "kept"


def test_t_m6_06_uncalibrated_never_switches_nor_unsupported() -> None:
    """T-M6-06: sin corrida de calibración nunca hay switched ni unsupported, ni con score 1.0."""
    switch = _decide(top2=[("pt", 1.0), ("es", 0.0)], thresholds=UNCALIBRATED)
    assert (switch.decision, switch.locale) == ("kept", "es")
    unsupported = _decide(letters=200, top2=[("en", 1.0), ("es", 0.0)], thresholds=UNCALIBRATED)
    assert (unsupported.decision, unsupported.locale) == ("kept", "es")


def test_prior_none_uses_first_supported_and_keeps_none_in_result() -> None:
    out = _decide(top2=[("es", 0.97), ("pt", 0.02)], prior=None)
    assert out.locale == "es" and out.locale_prior is None and out.decision == "kept"


def test_single_score_counts_as_full_distance() -> None:
    out = _decide(top2=[("pt", 0.99)])
    assert out.decision == "switched"


def test_decide_is_deterministic() -> None:
    args = {"top2": [("pt", 0.97), ("es", 0.02)]}
    assert _decide(**args) == _decide(**args)
