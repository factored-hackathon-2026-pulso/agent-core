"""Primer turno: prior desde el request (M6 §3.1.2). T-M6-08."""

from tests.m06.helpers import ES, PT, make_agent, make_release, make_service, make_state


def _first(text: str, request_lang: str | None, default: str = "es") -> tuple[str, str | None]:
    agent = make_agent(default_locale=default)
    result, _ = make_service().run(text, make_state(locale=default), agent, make_release(), request_lang,
                                   True, turn_id="turn-0001")
    return result.lang.locale, result.lang.locale_prior


def test_t_m6_08_supported_request_lang_is_the_prior() -> None:
    """T-M6-08: primer turno con lang soportado → se usa como prior (y se conserva si el texto es corto)."""
    assert _first("sí", "pt") == ("pt", "pt")


def test_t_m6_08_unsupported_request_lang_falls_back_to_default_locale() -> None:
    assert _first("sí", "fr") == ("es", "es")
    assert _first("sí", None) == ("es", "es")


def test_first_turn_can_switch_away_from_request_lang() -> None:
    locale, prior = _first(PT, "es")
    assert (locale, prior) == ("pt", "es")


def test_first_turn_kept_when_text_matches_request_lang() -> None:
    assert _first(ES, "es") == ("es", "es")
