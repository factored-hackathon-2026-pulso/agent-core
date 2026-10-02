"""Transfer end to end in M4 (ADR 0021, T-TR-01, T-TR-02, T-TR-07, T-TR-08): the target run opens and
answers in the same turn, inside the same unit of work."""

from typing import Any

import pytest

from agent_core.audit import AuditLog, check_chain
from agent_core.domain import DecisionModelDef, IllegalTransition, TurnResult
from testing.fakes.decision import make_decision
from testing.fakes.storage import SimulatedCrash
from tests.m02.harness import tool_def
from tests.m04.harness import RUN_ID, SPECIALIST_RELEASE_ID, World, flow, node
from tests.m04.helpers import cmd

TEXT = "no reconozco un cargo"


def _world() -> World:
    w = World(chain_factory=AuditLog)
    w.reception()
    return w


def _transfer(w: World, client_turn_id: str | None = None) -> TurnResult:
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    return w.turn(TEXT, client_turn_id=client_turn_id)


def test_specialist_answers_in_the_same_turn() -> None:  # T-TR-01
    w = _world()
    result = _transfer(w)
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion", "disputas"]
    source, target = runs
    assert source.status == "closed" and source.outcome is not None and source.outcome.value == "transferred"
    assert target.status == "open" and result.run_id == target.run_id
    assert result.agent is not None and result.agent.id == "disputas"
    assert result.messages[-1].text == w.text("t-pedir")  # disputa's first `collect` asks for details
    assert target.active_flow is not None and target.active_flow.node_id == "pedir"
    assert result.status == "open"


def test_target_runs_on_the_specialists_release_with_the_seeded_slots() -> None:
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    assert target.release == SPECIALIST_RELEASE_ID and target.session_id == source.session_id
    slot = target.slots["problema"]
    assert slot.status == "validated" and slot.source_turn == 1
    assert slot.value == source.slots["problema"].value
    assert set(target.slots) == {"problema"}  # only the packet's slots travel
    assert target.turn_count == 1 and target.facts == {} and target.decisions == {}


def test_target_processes_the_same_text_with_the_same_turn_id() -> None:  # P4
    w = _world()
    result = _transfer(w)
    assert len(w.understand.calls) == 2 and len(w.guards.calls) == 2
    assert w.guards.calls[0] == w.guards.calls[1]
    assert w.understand.calls[1].state.run_id != RUN_ID
    source, target = w.session_runs()
    turn_ids = {e.turn_id for e in w.audit.read(target.run_id) if e.turn_id is not None}
    assert turn_ids == {result.turn_id}
    assert result.turn_id in {e.turn_id for e in w.audit.read(source.run_id)}


def test_target_has_its_own_token_vault() -> None:
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    vaults = w.runtimes.vaults
    assert [v._run_id for v in vaults[-2:]] == [source.run_id, target.run_id]
    origin_tokens = set(vaults[-2]._by_token)
    assert origin_tokens and not origin_tokens & set(vaults[-1]._by_token)


def test_chains_are_linked_by_hash() -> None:  # T-TR-02 (P2)
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    source_events = w.audit.read(source.run_id)
    assert [e.type for e in source_events][-3:] == ["run_transferred", "run_closed", "turn_completed"]
    first = w.audit.read(target.run_id)
    assert [e.type for e in first][:3] == ["run_started", "transfer_received", "turn_started"]
    origin = first[0].payload.origin
    assert origin is not None and origin.from_event_hash == source_events[-1].hash
    assert origin.transfer_id == source_events[-3].payload.transfer_id == first[1].payload.transfer_id
    assert origin.from_run_id == source.run_id and origin.from_agent == source.agent
    assert origin.from_release_id == source.release and origin.depth == 1
    assert target.origin == origin
    assert source_events[-3].payload.to_run_id == target.run_id


def test_both_chains_verify_and_altering_the_origin_breaks_it() -> None:  # T-TR-02
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    source_events = w.audit.read(source.run_id)
    assert check_chain(source.run_id, source_events).ok
    assert check_chain(target.run_id, w.audit.read(target.run_id)).ok
    transferred = source_events[-3]
    tampered = transferred.model_copy(
        update={"payload": transferred.payload.model_copy(update={"reason": "otra"})})
    altered = [*source_events[:-3], tampered, *source_events[-2:]]
    assert not check_chain(source.run_id, altered).ok


def test_transfer_received_carries_slot_names_and_the_packet_fingerprint() -> None:
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    transferred = next(e for e in w.audit.read(source.run_id) if e.type == "run_transferred")
    received = next(e for e in w.audit.read(target.run_id) if e.type == "transfer_received")
    assert received.payload.accepted_slots == ["problema"]
    assert received.payload.packet_fp == transferred.payload.packet_fp
    started = w.audit.read(target.run_id)[0]
    assert started.payload.agent == target.agent and started.turn_id is None


def test_turn_messages_are_the_origins_then_the_targets() -> None:
    w = World(chain_factory=AuditLog)
    w.reception(flow="recepcion-aviso")  # says "te comunico con…" (no await) before transferring
    result = _transfer(w)
    assert [m.text for m in result.messages] == [w.text("t-comunico"), w.text("t-pedir")]


def test_the_whole_transfer_is_one_commit() -> None:  # spec §5.2.7
    w = _world()
    w.uow_factory.commits = 0  # type: ignore[attr-defined]
    _transfer(w)
    assert w.uow_factory.commits == 1  # type: ignore[attr-defined]


def test_retry_after_transfer_returns_the_same_result() -> None:  # Review Focus 1
    w = _world()
    first = _transfer(w, client_turn_id="c-1")
    opened = w.runtimes.opened
    again = w.turn(TEXT, client_turn_id="c-1")
    assert again == first and len(w.session_runs()) == 2
    assert w.runtimes.opened == opened  # nothing was processed again: no third run


def test_retry_of_an_earlier_origin_turn_returns_its_result() -> None:
    w = _world()
    w.understand.push(cmd("clarify"))
    earlier = w.turn("hola", client_turn_id="c-0")
    assert earlier.run_id == RUN_ID and earlier.status == "open"
    _transfer(w, client_turn_id="c-1")
    opened = w.runtimes.opened
    again = w.turn("hola", client_turn_id="c-0")
    assert again == earlier and w.runtimes.opened == opened
    assert len(w.session_runs()) == 2


def test_crash_on_commit_leaves_nothing() -> None:  # T-TR-07, Review Focus 5
    w = _world()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.turn(TEXT, client_turn_id="c-1")
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion"] and runs[0].status == "open"
    assert len(w.store.runs) == 1 and w.audit.read(RUN_ID) == []
    assert w.store.turn_results == {}
    assert w.store.events == {}  # no event under any run id, the target's included
    # Outside the UoW: the transcript entries of both runs were written and stay orphaned (m04 §3.8.7).
    assert [call[0] for call in w.recorder.calls] == [RUN_ID, "run-0002"]
    # The retry with the same client_turn_id runs the whole transfer again (the lease was released).
    _transfer(w, client_turn_id="c-1")
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion", "disputas"]
    assert runs[1].run_id != "run-0002"  # `to_run_id` comes from the IdSource again: it changes on retry


def test_origin_is_saved_before_the_target() -> None:
    w = _world()
    order: list[str] = []
    base = w.store.uow

    def uow():
        inner = base()
        save = inner.save_run

        def tracked(state, expected_version):
            order.append(state.run_id)
            return save(state, expected_version)

        inner.save_run = tracked  # type: ignore[method-assign]
        return inner

    w.uow_factory.base = uow  # type: ignore[attr-defined]
    _transfer(w)
    source, target = w.session_runs()
    assert order.index(source.run_id) < order.index(target.run_id)


def test_the_session_keeps_its_principal() -> None:  # T-TR-08
    w = _world()
    _transfer(w)
    source, target = w.session_runs()
    assert target.principal == source.principal and target.subject == source.subject
    assert target.on_behalf_of == source.on_behalf_of and target.locale == source.locale


def test_next_turn_goes_to_the_target() -> None:
    w = _world()
    _transfer(w)
    w.understand.push(cmd("continue"))
    result = w.turn("un cargo de cincuenta")
    _, target = w.session_runs()
    assert result.run_id == target.run_id and result.confirmation is not None


# --- review fixes (2026-10-01) ---------------------------------------------------------------------------


def test_crash_on_commit_leaves_orphan_transcript_entries_and_no_events() -> None:  # I1
    """The turn's UoW covers runs, event chains, usage and turn results. The transcript is written by the
    `TurnRecorder` outside it (M11): after a crash its entries for both runs stay, orphaned."""
    w = _world()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.turn(TEXT, client_turn_id="c-1")
    recorded = [call[0] for call in w.recorder.calls]
    assert recorded == [RUN_ID, "run-0002"]  # origin, then the target that was never committed
    assert w.store.events == {} and w.store.usage == {}
    assert set(w.store.runs) == {RUN_ID} and w.store.sessions == {"session-0001": [RUN_ID]}


def test_plain_chain_without_hashes_rolls_back_cleanly() -> None:  # M3
    w = World()  # PlainChain: events carry no hash, so there is nothing to link the target to
    w.reception()
    before = w.store.runs[RUN_ID]
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    with pytest.raises(IllegalTransition):
        w.turn(TEXT, client_turn_id="c-1")
    assert w.store.runs == {RUN_ID: before} and w.store.events == {} and w.store.turn_results == {}
    assert w.store.leases == {}  # an exception (not a crash) releases the lease


def test_target_escalating_in_its_first_turn_is_the_turns_result() -> None:  # I3 (a)
    w = _world()
    w.understand.push(cmd("continue"), cmd("handoff"))
    first = w.turn(TEXT, client_turn_id="c-1")
    source, target = w.session_runs()
    assert source.outcome is not None and source.outcome.value == "transferred"
    assert target.status == "escalated" and first.status == "escalated"
    assert first.run_id == target.run_id and first.agent == target.agent
    assert w.store.turn_results[(target.run_id, "c-1")] == first
    opened = w.runtimes.opened
    assert w.turn(TEXT, client_turn_id="c-1") == first and w.runtimes.opened == opened


ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["choice"],
    "input_view": [], "providers": [{"provider": "llm_structured"}], "calibration": {"method": "none"}})
# A specialist flow that reads the directory, chooses and transfers again (I3 b).
REENVIO = flow(
    "reenvio",
    20,
    node("leer", "tool", {"tool": "leer_directorio@1.0.0", "args": {}, "save_as": "directorio"},
         ok="elegir", failed="esc"),
    node("elegir", "decide",
         {"model": "router@1.0.0", "branch_on": "choice", "save_as": "ruta",
          "choices_from": "facts.directorio.value.choices", "input_view": []},
         chosen="transferir", none="esc", low_confidence="esc"),
    node("transferir", "transfer",
         {"target_from": "decisions.ruta.choice", "directory_from": "directorio",
          "packet": {"reason": "routed", "slots": ["problema"]}},
         rejected="esc"),
    node("esc", "escalate", {"reason_code": "policy:transfer_rejected"}),
)


def test_a_transfer_from_the_target_hits_the_session_limit() -> None:  # I3 (b), P5
    w = World(chain_factory=AuditLog)
    w.add(ROUTER, REENVIO)
    served: dict[str, Any] = {}  # the tool serves the same directory the reception read
    w.add_tool(tool_def("leer_directorio", "read"), handler=lambda _: served["directorio"])
    state = w.reception()
    assert state is not None
    served["directorio"] = state.facts["directorio"].value
    w.decisions.push(make_decision({"choice": "disputas"}, {"choice": True}))
    w.understand.push(cmd("continue"), cmd("start_flow", flow="reenvio"))
    result = w.turn(TEXT, client_turn_id="c-1")
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion", "disputas"]  # no third run
    target = runs[1]
    assert target.origin is not None and target.origin.depth == 1
    rejected = [e for e in w.audit.read(target.run_id) if e.type == "transfer_rejected"]
    assert [e.payload.reason_code for e in rejected] == ["transfer_limit"]
    assert target.status == "escalated" and result.status == "escalated" and result.run_id == target.run_id
    assert w.decisions.choice_calls[0][2] == ["disputas"]


CONFIRMAR_PRIMERO = flow(
    "confirmar-primero",
    20,
    node("confirmar", "confirm",
         {"action": {"tool": "radicar_pqr@1.0.0", "args": {"descripcion": "slots.problema"}},
          "summary_template": "t-resumen@1.0.0", "reprompt_template": "t-reprompt@1.0.0", "max_attempts": 5},
         yes="radicar", no="fin", unclear="confirmar", max_attempts="fin"),
    node("radicar", "tool", {"action_from": "confirmar", "save_as": "pqr"},
         ok="fin", uncertain="fin", denied="fin"),
    node("fin", "end", {"outcome": "resolved"}),
)


def test_the_target_can_only_propose_a_write_in_the_transfer_turn() -> None:  # I2, current behaviour
    """A write needs a proposal confirmed in a later turn of the same run, so the target's first turn can
    only propose it: no M3 commit of its own happens inside the transfer turn (m04 §11, Abierto)."""
    w = World(chain_factory=AuditLog)
    w.add(CONFIRMAR_PRIMERO)
    w.reception()
    w.uow_factory.commits = 0  # type: ignore[attr-defined]
    w.understand.push(cmd("continue"), cmd("start_flow", flow="confirmar-primero"))
    result = w.turn(TEXT)
    _, target = w.session_runs()
    assert result.confirmation is not None and result.run_id == target.run_id
    assert [a.state for a in target.actions] == ["proposed"]
    assert w.write_calls() == [] and w.uow_factory.commits == 1  # type: ignore[attr-defined]
