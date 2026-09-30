"""Formato del fixture (decisión 14): ida y vuelta con Decimal, cadena y eventos intactos."""

from decimal import Decimal

import pytest

from agent_core.audit.chain import chain_events, check_chain
from agent_core.audit.replay.fixture import Fixture, FullToolResult, dump_fixture, load_fixture
from tests.m11.helpers import event


def sample() -> Fixture:
    events = chain_events("run-0001", [event("run_started"), event("rule_evaluated", n=2),
                                       event("tool_called", n=3), event("run_closed", n=4)], None)
    return Fixture(
        name="demo", run_id="run-0001", release="rel-2026-09-28",
        inputs=[{"text_model": "hola", "client_turn_id": "c-1"}], events=events,
        full={"call-0001": FullToolResult(status="ok", result_full={"monto": Decimal("120.50"), "n": 500,
                                                                     "entero": Decimal("500")}, source="tx")},
        drafts=["Listo ⟦name:1⟧"],
    )


def test_roundtrip_preserves_events_chain_and_decimals() -> None:
    back = load_fixture(dump_fixture(sample()))
    assert back.events == sample().events and check_chain("run-0001", back.events).ok
    result = back.full["call-0001"].result_full
    assert isinstance(result, dict) and result["monto"] == Decimal("120.50")
    assert str(result["monto"]) == "120.50" and result["entero"] == 500


def test_dump_is_stable_text() -> None:
    assert dump_fixture(sample()) == dump_fixture(load_fixture(dump_fixture(sample())))


def test_loader_rejects_duplicate_keys_and_extra_fields() -> None:
    with pytest.raises(ValueError):
        load_fixture("name: a\nname: b\n")
    with pytest.raises(ValueError):
        load_fixture(dump_fixture(sample()) + "extra: 1\n")
