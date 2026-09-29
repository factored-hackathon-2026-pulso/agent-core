from typing import Any

from agent_core.guards import LangThresholds
from agent_core.response.checks import check_language
from agent_core.response.types import Draft, Failure
from tests.m08.helpers import make_ctx, make_lang

ES = "Quiero consultar el saldo de mi cuenta y revisar los últimos movimientos"
PT = "Gostaria de consultar o saldo da minha conta e revisar as últimas transações realizadas"
EN = "I would like to check the balance of my account and review the latest transactions please"
CALIBRATED = LangThresholds(switch_threshold=0.90, unsupported_threshold=0.90, min_distance=0.20)


def _check(text: str, locale: str, **over: Any) -> list[Failure]:
    return check_language(Draft(text=text, citations=[]), make_ctx(locale=locale, **over))


def test_t_m8_04a_other_language_is_rejected_same_language_passes() -> None:
    failures = _check(PT, "es")
    assert [f.check for f in failures] == ["language"]
    assert "esperado es" in failures[0].detail and "detectado pt" in failures[0].detail
    assert _check(ES, "es") == []
    assert _check(PT, "pt") == []


def test_t_m8_04b_short_text_is_not_rejected() -> None:
    # "Listo, gracias" tiene 12 letras: con `min_letters=12` ya se detecta; con 20 es `short`.
    assert _check("Listo, gracias", "pt", lang_cfg=make_lang(min_letters=20)) == []
    assert _check("Sí", "pt") == []


def test_unsupported_language_is_rejected() -> None:
    assert [f.check for f in _check(EN, "es")] == ["language"]


def test_result_does_not_depend_on_switch_thresholds() -> None:  # P5
    for thresholds in (LangThresholds(), CALIBRATED):
        assert [f.check for f in _check(PT, "es", lang_thresholds=thresholds)] == ["language"]
        assert _check(ES, "es", lang_thresholds=thresholds) == []


def test_detail_never_contains_the_text() -> None:
    (failure,) = _check(PT, "es")
    assert "saldo" not in failure.detail
