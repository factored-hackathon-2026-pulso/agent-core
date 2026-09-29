"""Puertos grabados: nunca llaman nada real; una llamada no grabada es un ReplayDesync."""

import pytest

from agent_core.audit.chain import chain_events
from agent_core.audit.replay.fixture import FullToolResult
from agent_core.audit.replay.ports import (
    FixtureFullSource,
    ForbiddenFullSource,
    FullViewAccessError,
    RecordedClock,
    RecordedGateway,
    RecordedIds,
    RecordedToolExecutor,
    ReplayDesync,
    build_ports,
)
from agent_core.domain import EntityRef
from agent_core.ports import IdKind, ToolCallContext
from testing.builders import principal
from tests.m11.helpers import event

CTX = ToolCallContext(run_id="run-0001", release="rel-2026-09-28", principal=principal())


def run_events():  # type: ignore[no-untyped-def]
    return chain_events("run-0001", [
        event("run_started", turn_id=None, n=1),
        event("turn_started", n=2),
        event("tool_called", n=3),      # call-0001, buscar_transacciones@1.0.0, ok
        event("decision_made", n=4),    # decision-0001
        event("response_emitted", n=5),
        event("turn_completed", n=6),
    ], None)


def test_clock_is_constant_within_a_turn_and_monotonic_is_fixed() -> None:
    clock = RecordedClock.from_events(run_events())
    clock.enter_turn(None)
    started = clock.now()
    clock.enter_turn("turn-0001")
    assert clock.now() == clock.now() and clock.monotonic_ns() == clock.monotonic_ns() == 0
    assert started == clock.now()  # ts idéntico en el sobre sintético


def test_ids_replay_recorded_ids_in_order_then_fall_back_deterministically() -> None:
    ids = RecordedIds.from_events(run_events())
    assert ids.new_id(IdKind.call) == "call-0001" and ids.new_id(IdKind.decision) == "decision-0001"
    assert ids.new_id(IdKind.call) == "call-replay-0001"
    assert ids.secret_token() != ids.secret_token() and len(ids.secret_token()) >= 22


def test_tools_fixture_mode_serves_full_and_audit_mode_serves_audit_view() -> None:
    events = run_events()
    tool = event("tool_called").payload.tool  # type: ignore[attr-defined]
    full = FixtureFullSource({"call-0001": FullToolResult(status="ok", result_full={"count": 2, "sec": "x"},
                                                          source="tx")})
    fx = RecordedToolExecutor(events, "fixture", full, lambda ref: (_ for _ in ()).throw(KeyError()))
    got = fx.execute(tool, {}, {}, CTX)
    assert got.call_id == "call-0001" and got.result_full == {"count": 2, "sec": "x"} and got.source == "tx"
    au = RecordedToolExecutor(events, "audit", ForbiddenFullSource(),
                              lambda ref: (_ for _ in ()).throw(KeyError()))
    assert au.execute(tool, {}, {}, CTX).result_full == {"count": 2}  # vista audit del evento; no toca full


def test_unrecorded_tool_call_is_a_desync_with_the_expected_event() -> None:
    events = run_events()
    tools = RecordedToolExecutor(events, "audit", ForbiddenFullSource(), lambda r: None)  # type: ignore[arg-type, return-value]
    with pytest.raises(ReplayDesync) as info:
        tools.execute(EntityRef.parse("otra_tool@1.0.0"), {}, {}, CTX)
    assert info.value.event_seq == 2
    tools.execute(event("tool_called").payload.tool, {}, {}, CTX)  # type: ignore[attr-defined]
    with pytest.raises(ReplayDesync):  # ya no quedan llamadas grabadas
        tools.execute(event("tool_called").payload.tool, {}, {}, CTX)  # type: ignore[attr-defined]


def test_forbidden_full_source_raises() -> None:
    with pytest.raises(FullViewAccessError):
        ForbiddenFullSource().tool_result("call-0001")


def test_gateway_serves_recorded_drafts_then_desyncs() -> None:
    gw = RecordedGateway(["uno"])
    assert gw.generate(EntityRef.parse("p@1.0.0"), {}, "es").output == "uno"
    with pytest.raises(ReplayDesync):
        gw.generate(EntityRef.parse("p@1.0.0"), {}, "es")


def test_build_ports_wires_everything_for_the_mode() -> None:
    ports = build_ports(run_events(), "audit", full=ForbiddenFullSource(), drafts=[],
                        definitions=lambda r: None)  # type: ignore[arg-type, return-value]
    assert ports.mode == "audit" and len(ports.readings.events_of("tool_called")) == 1
    assert ports.readings.events_of("turn_started", "turn-0001")
