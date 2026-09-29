"""detect_language con lingua real (M6 §3.1). T-M6-01…06 de extremo a extremo y pureza."""

import importlib.metadata

from agent_core.domain import LanguageDetection
from agent_core.guards.language import detect_language
from agent_core.guards.models import UNCALIBRATED, LangThresholds

VERSION = importlib.metadata.version("lingua-language-detector")
CFG = LanguageDetection.model_validate({
    "id": "lang", "version": "1.0.0", "detector": f"lingua@{VERSION}", "candidates": ["es", "pt"],
    "unsupported": ["en"], "min_letters": 12, "min_letters_unsupported": 24,
})
CALIBRATED = LangThresholds(switch_threshold=0.90, unsupported_threshold=0.90, min_distance=0.20)
SUPPORTED = ["es", "pt"]

ES = "Quiero consultar el saldo de mi cuenta y revisar los últimos movimientos"
PT = "Gostaria de consultar o saldo da minha conta e revisar as últimas transações realizadas"
EN = "I would like to check the balance of my account and review the latest transactions please"


def test_t_m6_01_pt_switches_with_calibration() -> None:
    out = detect_language(PT, CFG, CALIBRATED, SUPPORTED, "es")
    assert (out.decision, out.locale, out.locale_prior) == ("switched", "pt", "es")
    assert out.detector == CFG.detector and out.top2[0][0] == "pt"


def test_es_kept_when_prior_es() -> None:
    out = detect_language(ES, CFG, CALIBRATED, SUPPORTED, "es")
    assert (out.decision, out.locale) == ("kept", "es")


def test_t_m6_02_short_inputs_keep_prior() -> None:
    for text in ("sí", "ok", "não", "$ 250.000", "12345", "😀", ""):
        out = detect_language(text, CFG, CALIBRATED, SUPPORTED, "pt")
        assert (out.decision, out.locale) == ("short", "pt"), text


def test_t_m6_03_portunol_never_switches_the_locale() -> None:
    """Un texto mezclado: el resultado real de lingua puede ser undetermined o kept, pero nunca switched."""
    out = detect_language("quero saber mi saldo por favor de la cuenta", CFG, LangThresholds(
        switch_threshold=0.99, unsupported_threshold=0.99, min_distance=0.20), SUPPORTED, "es")
    assert out.decision in {"undetermined", "kept"} and out.locale == "es"


def test_t_m6_04_single_english_word_is_not_unsupported() -> None:
    out = detect_language("balance", CFG, CALIBRATED, SUPPORTED, "es")
    assert out.decision == "short" and out.locale == "es"


def test_t_m6_05_long_english_is_unsupported() -> None:
    out = detect_language(EN, CFG, CALIBRATED, SUPPORTED, "es")
    assert (out.decision, out.locale) == ("unsupported", "es")


def test_t_m6_06_uncalibrated_never_switches_nor_unsupported() -> None:
    for text in (PT, EN, ES):
        out = detect_language(text, CFG, UNCALIBRATED, SUPPORTED, "es")
        assert out.decision not in {"switched", "unsupported"} and out.locale == "es", text


def test_pii_tokens_and_amounts_do_not_count_as_letters() -> None:
    out = detect_language("⟦doc:1⟧ $1.500,00", CFG, CALIBRATED, SUPPORTED, "es")
    assert out.decision == "short" and out.letters == 0


def test_detect_language_is_pure() -> None:
    first = detect_language(PT, CFG, CALIBRATED, SUPPORTED, "es")
    second = detect_language(PT, CFG, CALIBRATED, SUPPORTED, "es")
    assert first == second
