"""M4 reports each turn to `TurnTelemetry` (m04 §3.9, T-M4-22). The default is a no-op, and a telemetry that
fails never breaks a turn: M4 logs the failure's type and goes on."""

import logging
from datetime import timedelta

import pytest

from agent_core.audit import AuditLog
from agent_core.domain import AgentSelector, EngineError, ProblemCode, RunInput
from agent_core.turn import NoTurnTelemetry, TransferOutcome
from testing.fakes.telemetry import FailingTelemetry, RecordingTelemetry
from tests.m04.harness import RUN_ID, SESSION_ID, SPECIALIST_RELEASE_ID, World
from tests.m04.helpers import cmd


def _world() -> tuple[World, RecordingTelemetry]:
    telemetry = RecordingTelemetry()
    return World(chain_factory=AuditLog, telemetry=telemetry), telemetry


def _run_input() -> RunInput:
    return RunInput(agent=AgentSelector(id="atencion", alias="prod"), idempotency_key="k")


def test_the_default_telemetry_is_a_no_op() -> None:
    w = World()
    assert isinstance(w.engine._telemetry, NoTurnTelemetry)


def test_handle_turn_opens_one_scope_with_the_correlation_ids() -> None:
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    result = w.turn("hola")
    (turn,) = telemetry.turns
    assert turn.closed and turn.error is None and turn.links == ()
    scope = turn.scope
    assert (scope.run_id, scope.turn_id, scope.session_id) == (RUN_ID, result.turn_id, SESSION_ID)
    assert scope.entry == "turn" and scope.release == w.saved().release and scope.agent == w.saved().agent
    assert scope.principal_type == w.saved().principal.type.value and scope.locale == w.saved().locale


def test_record_receives_exactly_the_chained_events_in_order() -> None:
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("hola")
    (turn,) = telemetry.turns
    assert [e.event_id for e in turn.events] == [e.event_id for e in w.events()]


def test_events_flushed_by_m3_are_recorded_too() -> None:
    """`tool_called` goes through `TurnEventSink` (M3 flushes before and after the write), not `_finish`."""
    w, telemetry = _world()
    prompt = w.seed_at_confirm()
    result = w.turn_confirm(prompt.token, "yes")
    (turn,) = telemetry.turns
    assert "tool_called" in w.event_types()
    assert [e.event_id for e in turn.events] == [e.event_id for e in w.events()]
    assert [e.type for e in turn.events].count("turn_completed") == 1
    assert result.turn_id == turn.scope.turn_id


def test_start_run_opens_a_start_run_scope() -> None:
    w, telemetry = _world()
    run = w.engine.start_run(w.principal, None, _run_input())
    (turn,) = telemetry.turns
    assert turn.closed and turn.error is None
    assert turn.scope.entry == "start_run" and turn.scope.run_id == run.run_id
    assert turn.scope.session_id == run.session_id and turn.scope.release == run.release
    assert [e.event_id for e in turn.events] == [e.event_id for e in w.store.events[run.run_id]]


def test_a_start_run_replayed_by_its_idempotency_key_opens_no_scope() -> None:
    w, telemetry = _world()
    w.engine.start_run(w.principal, None, _run_input())
    w.engine.start_run(w.principal, None, _run_input())
    assert len(telemetry.turns) == 1


def test_no_scope_for_turns_that_emit_no_turn_completed() -> None:  # m04 §3.7, as T-M4-17 builds them
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido", client_turn_id="c-1")
    assert len(telemetry.turns) == 1
    w.turn("cargo desconocido", client_turn_id="c-1")  # duplicate
    assert len(telemetry.turns) == 1
    with w.store.uow() as other:  # 409
        other.acquire_turn(RUN_ID, "turn-otro", w.clock.now(), timedelta(seconds=60))
    with pytest.raises(EngineError) as conflict:
        w.turn("otro", client_turn_id="c-2")
    assert conflict.value.status == 409 and len(telemetry.turns) == 1
    w.clock.advance(timedelta(seconds=61))
    w.store.leases.clear()
    w.understand.push(cmd("handoff"))
    w.turn("quiero un asesor", client_turn_id="c-3")  # closes the run (escalated)
    assert len(telemetry.turns) == 2
    with pytest.raises(EngineError) as gone:  # 410
        w.turn("hola", client_turn_id="c-4")
    assert gone.value.status == 410 and len(telemetry.turns) == 2


def test_an_expired_run_closes_inside_its_scope_and_reports_the_problem() -> None:
    """The abandonment turn emits `turn_completed`, so it has a scope; its 410 is raised inside it."""
    w, telemetry = _world()
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError) as gone:
        w.turn("hola")
    assert gone.value.code is ProblemCode.run_closed
    (turn,) = telemetry.turns
    assert turn.closed and turn.error == "EngineError"
    assert [e.event_id for e in turn.events] == [e.event_id for e in w.events()]
    assert w.saved().status == "closed"  # committed before the error left the scope


def test_an_exception_closes_the_scope_and_propagates() -> None:
    w, telemetry = _world()
    w.open_run(active=True)

    def boom() -> None:
        raise RuntimeError("boom")

    w.understand.hook = boom
    with pytest.raises(RuntimeError):
        w.turn("hola")
    (turn,) = telemetry.turns
    assert turn.closed and turn.error == "RuntimeError"


def test_telemetry_does_not_change_events_or_ids() -> None:
    plain, (traced, _) = World(chain_factory=AuditLog), _world()
    for w in (plain, traced):
        w.open_run(active=True)
        w.understand.push(cmd("continue"))
        w.turn("hola")
    assert [e.model_dump() for e in plain.events()] == [e.model_dump() for e in traced.events()]



def test_a_transfer_opens_the_target_turn_inside_the_origin_one() -> None:
    """Same turn id, the target's own run; each scope records its own run's events (T5 adds the link)."""
    telemetry = RecordingTelemetry()
    w = World(chain_factory=AuditLog, telemetry=telemetry)
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    result = w.turn("no reconozco un cargo")
    origin, target = telemetry.turns
    source_run, target_run = w.session_runs()
    assert (origin.scope.run_id, target.scope.run_id) == (source_run.run_id, target_run.run_id)
    assert origin.scope.turn_id == target.scope.turn_id == result.turn_id
    assert target.scope.agent.id == "disputas" and target.scope.release == target_run.release
    assert origin.closed and target.closed and origin.error is None and target.error is None
    assert [e.event_id for e in origin.events] == [e.event_id for e in w.audit.read(source_run.run_id)]
    assert [e.event_id for e in target.events] == [e.event_id for e in w.audit.read(target_run.run_id)]


# --- a failing telemetry never breaks a turn (I4) -------------------------------------------------------


@pytest.mark.parametrize("where", ["turn", "enter", "record", "exit"])
def test_a_failing_telemetry_never_breaks_the_turn(where: str, caplog: pytest.LogCaptureFixture) -> None:
    plain = World(chain_factory=AuditLog)
    broken = World(chain_factory=AuditLog, telemetry=FailingTelemetry(where))
    results = []
    with caplog.at_level(logging.WARNING, logger="agent_core.turn"):
        for w in (plain, broken):
            w.open_run(active=True)
            w.understand.push(cmd("continue"))
            results.append(w.turn("hola"))
    assert results[0] == results[1]
    assert [e.model_dump() for e in plain.events()] == [e.model_dump() for e in broken.events()]
    warnings = [r for r in caplog.records if r.name == "agent_core.turn"]
    assert warnings and all("TelemetryBroken" in r.getMessage() for r in warnings)
    assert all(r.exc_info is None for r in warnings)  # only the type: the message may carry a secret
    assert "SECRETO" not in caplog.text


def test_a_failing_start_run_telemetry_never_breaks_the_run() -> None:
    w = World(chain_factory=AuditLog, telemetry=FailingTelemetry("enter", "record", "exit"))
    run = w.engine.start_run(w.principal, None, _run_input())
    assert run.first_turn is not None and run.status == "open"


def test_a_telemetry_cannot_swallow_the_turn_error() -> None:
    """An `__exit__` that returns True would hide the 410 from M9: M4 never lets it."""
    w = World(chain_factory=AuditLog, telemetry=FailingTelemetry(swallow=True))
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")


def test_a_failing_exit_never_hides_the_turn_error(caplog: pytest.LogCaptureFixture) -> None:
    w = World(chain_factory=AuditLog, telemetry=FailingTelemetry("exit"))
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=31))
    with caplog.at_level(logging.WARNING, logger="agent_core.turn"), pytest.raises(EngineError):
        w.turn("hola")
    assert "TelemetryBroken" in caplog.text and "SECRETO" not in caplog.text


def test_m4_never_imports_opentelemetry() -> None:
    """`.importlinter` forbids `agent_telemetry` to M4 (F11); OpenTelemetry is an external package it cannot
    see, so this test covers it."""
    import ast
    from pathlib import Path

    import agent_core.turn

    root = Path(agent_core.turn.__file__).parent
    imported: set[str] = set()
    for path in root.rglob("*.py"):  # subpackages of agent_core/turn too
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert not {m for m in imported if m.split(".")[0] in ("opentelemetry", "agent_telemetry")}


def test_a_failing_transfer_span_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    """`span.transfer(...)` follows the same rule: a no-op transfer span without a link."""
    from agent_core.domain import EntityRef
    from agent_core.turn import TurnScope
    from agent_core.turn.telemetry import observed_turn

    scope = TurnScope(run_id=RUN_ID, turn_id="turn-0001", session_id=SESSION_ID, release="rel-1",
                      agent=EntityRef(id="atencion", version="1.0.0"), entry="turn",
                      principal_type="customer", locale="es")
    with caplog.at_level(logging.WARNING, logger="agent_core.turn"):
        with (
            observed_turn(FailingTelemetry("transfer"), scope) as span,
            span.transfer("transfer-0001") as transfer,
        ):
            assert transfer.link is None
            transfer.finish(TransferOutcome("rejected", reason_code="no_route"))
    assert "TelemetryBroken" in caplog.text and "SECRETO" not in caplog.text


TEXT = "no reconozco un cargo"


def _reception(telemetry: object, **kwargs: object) -> World:
    w = World(chain_factory=AuditLog, telemetry=telemetry)
    w.reception(**kwargs)  # type: ignore[arg-type]
    return w


def test_a_valid_transfer_reports_its_outcome_and_links_the_target_turn() -> None:  # T-TR-16
    telemetry = RecordingTelemetry()
    w = _reception(telemetry)
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    origin, target = telemetry.turns[-2:]
    (transfer,) = origin.transfers
    moved = next(e for e in w.audit.read(origin.scope.run_id) if e.type == "run_transferred")
    assert transfer.transfer_id == moved.payload.transfer_id
    assert transfer.outcome == TransferOutcome(
        outcome="transferred", to_agent="disputas", to_release_id=SPECIALIST_RELEASE_ID)
    assert target.links == (transfer.link,) and target.transfers == []
    assert target.scope.run_id == moved.payload.to_run_id and target.scope.turn_id == origin.scope.turn_id
    assert origin.scope.agent.id == "recepcion" and target.scope.agent.id == "disputas"
    assert origin.links == ()


def test_a_rejection_echoes_to_agent_only_when_the_event_does() -> None:
    telemetry = RecordingTelemetry()
    w = _reception(telemetry, choice="saldos")  # absent from the directory read: no `to_agent` echo
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    (turn,) = telemetry.turns
    (transfer,) = turn.transfers
    rejected = next(e for e in w.events() if e.type == "transfer_rejected")
    assert rejected.payload.to_agent is None
    assert transfer.outcome == TransferOutcome(outcome="rejected", reason_code="not_in_directory")


def test_a_rejection_of_a_listed_target_echoes_it_without_release() -> None:
    telemetry = RecordingTelemetry()
    w = _reception(telemetry, origin_depth=1)  # `transfer_limit`: the target is in the directory
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    rejected = next(e for e in w.events() if e.type == "transfer_rejected")
    (transfer,) = telemetry.turns[-1].transfers
    assert rejected.payload.to_agent == "disputas"
    assert transfer.outcome == TransferOutcome(
        outcome="rejected", to_agent="disputas", reason_code="transfer_limit")
    assert len(telemetry.turns) == 1  # nothing to link: no target turn


def _chains(w: World) -> list[list[dict[str, object]]]:
    return [[e.model_dump() for e in w.audit.read(run.run_id)] for run in w.session_runs()]


def test_transfer_telemetry_does_not_change_the_two_chains() -> None:
    plain = World(chain_factory=AuditLog)
    traced = World(chain_factory=AuditLog, telemetry=RecordingTelemetry())
    for w in (plain, traced):
        w.reception()
        w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
        w.turn(TEXT)
    assert len(_chains(plain)) == 2 and _chains(plain) == _chains(traced)


@pytest.mark.parametrize("where", [("transfer",), ("link",), ("finish",), ("transfer", "link", "finish"),
                                   ("enter", "record", "exit", "transfer", "link", "finish")])
def test_a_failing_transfer_telemetry_never_changes_a_valid_transfer(
        where: tuple[str, ...], caplog: pytest.LogCaptureFixture) -> None:
    """Enter, `link` and `finish` of the transfer span are contained: same result, byte-identical chains."""
    plain = World(chain_factory=AuditLog)
    broken = World(chain_factory=AuditLog, telemetry=FailingTelemetry(*where))
    results = []
    with caplog.at_level(logging.WARNING, logger="agent_core.turn"):
        for w in (plain, broken):
            w.reception()
            w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
            results.append(w.turn(TEXT))
    assert results[0] == results[1] and len(_chains(plain)) == 2
    assert _chains(plain) == _chains(broken)
    warnings = [r for r in caplog.records if r.name == "agent_core.turn"]
    assert warnings and all("TelemetryBroken" in r.getMessage() and r.exc_info is None for r in warnings)
    assert "SECRETO" not in caplog.text


@pytest.mark.parametrize("where", ["transfer", "finish"])
def test_a_failing_transfer_telemetry_never_changes_a_rejection(
        where: str, caplog: pytest.LogCaptureFixture) -> None:
    plain = World(chain_factory=AuditLog)
    broken = World(chain_factory=AuditLog, telemetry=FailingTelemetry(where))
    results = []
    with caplog.at_level(logging.WARNING, logger="agent_core.turn"):
        for w in (plain, broken):
            w.reception(origin_depth=1)
            w.understand.push(cmd("continue"))
            results.append(w.turn(TEXT))
    assert results[0] == results[1]
    assert [e.model_dump() for e in plain.events()] == [e.model_dump() for e in broken.events()]
    assert "TelemetryBroken" in caplog.text and "SECRETO" not in caplog.text

