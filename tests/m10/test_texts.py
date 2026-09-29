"""Textos por defecto: deterministas, ES/PT, sin valores de campos ni promesas de tiempos (ADR 0013)."""

import pytest

from agent_core.domain import ReasonCode
from agent_core.handoff.texts import FALLBACK_LOCALE, default_handoff_message, reason_key, render_summary

BASE_KEYS = [code.value for code in ReasonCode]


def test_reason_key_maps_prefixes_and_base_codes() -> None:
    assert reason_key("rule:mora-alta") == "rule"
    assert reason_key("policy:escalamiento-disputa-monto") == "policy"
    assert reason_key("interrupt:fraude") == "interrupt"
    assert reason_key("customer_request") == "customer_request"
    assert reason_key("algo_raro") == "default"


@pytest.mark.parametrize("locale", ["es", "pt"])
@pytest.mark.parametrize("code", [*BASE_KEYS, "rule:x", "policy:x", "interrupt:x"])
def test_every_reason_has_a_summary_in_both_locales(code: str, locale: str) -> None:
    text = render_summary(code, locale, flow="disputa-cargo", facts=2, actions=1, uncertain=0)
    assert text and "{" not in text and "}" not in text


def test_summary_is_deterministic_and_mentions_counts_only() -> None:
    a = render_summary("policy:x", "es", flow="disputa-cargo", facts=3, actions=2, uncertain=1)
    assert a == render_summary("policy:x", "es", flow="disputa-cargo", facts=3, actions=2, uncertain=1)
    assert "disputa-cargo" in a and "3" in a
    assert "incierta" in render_summary("tool_failure", "es", flow=None, facts=0, actions=1, uncertain=1)
    assert "incierta" not in render_summary("tool_failure", "es", flow=None, facts=0, actions=1, uncertain=0)


def test_unknown_locale_falls_back_to_spanish() -> None:
    assert FALLBACK_LOCALE == "es"
    assert render_summary("customer_request", "fr", flow=None, facts=0, actions=0, uncertain=0) == \
        render_summary("customer_request", "es", flow=None, facts=0, actions=0, uncertain=0)
    assert default_handoff_message("fr") == default_handoff_message("es")
    assert default_handoff_message("pt") != default_handoff_message("es")


def test_handoff_message_promises_no_response_time() -> None:
    for locale in ("es", "pt"):
        text = default_handoff_message(locale).lower()
        assert not any(word in text for word in ("minuto", "hora", "minuto", "segundos"))
