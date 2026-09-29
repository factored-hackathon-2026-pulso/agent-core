"""T-M11-02, 03, 04 y 10 sobre el Replayer con un motor de prueba (T-M11-01 va en la Task 14)."""

import pytest

from agent_core.audit.chain import chain_events
from agent_core.audit.log import AuditLog
from agent_core.audit.replay.fixture import Fixture, FullToolResult
from agent_core.audit.replay.ports import FullViewAccessError
from agent_core.audit.replay.runner import Replayer
from testing.fakes.clock import FakeClock
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.engine_stub import StubEngine
from tests.m11.helpers import event

FULL = {"call-0001": FullToolResult(status="ok", result_full={"count": 2}, source="tx")}


def recorded() -> list:  # type: ignore[type-arg]
    return chain_events("run-0001", [event("run_started", turn_id=None, n=1), event("turn_started", n=2),
                                     event("rule_evaluated", n=3), event("tool_called", n=4),
                                     event("turn_completed", n=5), event("run_closed", n=6)], None)


def fixture(events: list | None = None) -> Fixture:  # type: ignore[type-arg]
    return Fixture(name="demo", run_id="run-0001", release="rel-2026-09-28", inputs=[],
                   events=events or recorded(), full=FULL, drafts=[])


def flip_rule(e):  # type: ignore[no-untyped-def]
    if getattr(e, "type", "") == "rule_evaluated":
        return e.model_copy(update={"payload": e.payload.model_copy(update={"result": True})})
    return e


def replayer(engine: StubEngine, store: InMemoryStore | None = None) -> Replayer:
    audit = InMemoryAuditSink(store) if store else None
    return Replayer(engine, FakeClock(), audit=audit, definitions=lambda r: None)  # type: ignore[arg-type, return-value]


def test_t_m11_01_shape_fixture_replay_of_an_unchanged_engine_matches() -> None:
    report = replayer(StubEngine()).replay(fixture(), "fixture")
    assert (report.verdict, report.mode, report.run_id, report.release) == (
        "match", "fixture", "run-0001", "rel-2026-09-28")
    assert report.first_divergence is None and report.duration_ms is not None


def test_t_m11_02_changed_rule_diverges_with_first_divergence() -> None:
    report = replayer(StubEngine(flip_rule)).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence is not None
    assert report.first_divergence.event_seq == 2
    assert report.first_divergence.expected["payload"]["result"] is False  # type: ignore[index]
    assert report.first_divergence.actual["payload"]["result"] is True  # type: ignore[index]


@pytest.mark.parametrize("mode", ["fixture", "audit"])
def test_t_m11_03_altered_event_is_chain_broken_in_both_modes(mode: str) -> None:
    events = recorded()
    events[2] = events[2].model_copy(update={"release": "rel-alterada"})
    engine = StubEngine()
    report = replayer(engine).replay(fixture(events), mode)  # type: ignore[arg-type]
    assert report.verdict == "chain_broken" and report.chain_broken_at == 2
    assert engine.calls == []  # el motor ni siquiera corre


def test_t_m11_04_audit_replay_of_a_recorded_run_matches_without_touching_full() -> None:
    store = InMemoryStore()
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", [e.model_copy(update={"seq": None, "prev_hash": None, "hash": None})
                                     for e in recorded()])
        uow.commit()
    report = replayer(StubEngine(), store).replay("run-0001", "audit")
    assert report.verdict == "match" and report.mode == "audit"


def test_t_m11_04_audit_engine_touching_full_raises_full_view_access_error() -> None:
    with pytest.raises(FullViewAccessError):
        replayer(StubEngine(touch_full=True)).replay(fixture(), "audit")


def test_t_m11_10_measured_fields_differing_still_match() -> None:
    def remeasure(e):  # type: ignore[no-untyped-def]
        kind = getattr(e, "type", "")
        if kind == "tool_called":
            return e.model_copy(update={"payload": e.payload.model_copy(update={"latency_ms": 9999})})
        if kind == "turn_completed":
            return e.model_copy(update={"payload": e.payload.model_copy(update={"duration_ms": 1})})
        return e

    assert replayer(StubEngine(remeasure)).replay(fixture(), "fixture").verdict == "match"


def test_t_m11_10_change_elsewhere_in_the_same_event_diverges() -> None:
    def both(e):  # type: ignore[no-untyped-def]
        if getattr(e, "type", "") == "tool_called":
            changed = e.payload.model_copy(update={"latency_ms": 1, "attempt": 2})
            return e.model_copy(update={"payload": changed})
        return e

    report = replayer(StubEngine(both)).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence.event_seq == 3  # type: ignore[union-attr]


def test_engine_desync_is_reported_as_divergence() -> None:
    class Wrong(StubEngine):
        def run(self, case, ports):  # type: ignore[no-untyped-def]
            from agent_core.domain import EntityRef

            ports.tools.execute(EntityRef.parse("otra@1.0.0"), {}, {}, None)  # type: ignore[arg-type]
            return []

    report = replayer(Wrong()).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence is not None


def test_usage_errors() -> None:
    with pytest.raises(ValueError):
        replayer(StubEngine()).replay("run-0001", "audit")      # run_id sin AuditSink
    with pytest.raises(ValueError):
        replayer(StubEngine(), InMemoryStore()).replay("run-0001", "fixture")  # fixture pide un Fixture


def test_replay_never_receives_real_ports() -> None:
    """El motor solo recibe `RecordedPorts`: no hay camino a modelos ni tools reales."""
    seen: list[object] = []

    class Spy(StubEngine):
        def run(self, case, ports):  # type: ignore[no-untyped-def]
            seen.append(ports)
            return list(case.recorded)

    replayer(Spy()).replay(fixture(), "fixture")
    from agent_core.audit.replay.ports import RecordedPorts

    assert isinstance(seen[0], RecordedPorts)
