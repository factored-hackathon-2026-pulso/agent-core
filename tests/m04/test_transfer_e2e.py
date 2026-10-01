"""Transfer end to end (ADR 0021): reception reads the directory, chooses and transfers; the specialist
continues in the target. No seeded facts: `directory/list` and `decide` run inside the flow."""

from agent_core.audit import AuditLog, verify_transfer_link
from agent_core.domain import AgentSelector, DecisionModelDef, RunInput, TurnInput
from testing.fakes.decision import make_decision
from tests.m04.harness import DIRECTORY, SPECIALIST_RELEASE_ID, World, flow, node
from tests.m04.helpers import cmd

TEXT = "no reconozco un cargo"
ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["choice"],
    "input_view": [], "providers": [{"provider": "llm_structured"}], "calibration": {"method": "none"}})
ROUTING = flow(
    "recepcion-ruta",
    10,
    node("entender", "collect", {"slot": "problema", "prompt_ref": "t-pedir@1.0.0"}, ok="listar",
         max_attempts="esc"),
    node("listar", "tool",
         {"tool": "directory/list@1.0.0", "args": {"directory": DIRECTORY, "locale": "es"},
          "save_as": "directorio"},
         ok="rutear", error="esc", timeout="esc", denied="esc"),
    node("rutear", "decide",
         {"model": "router@1.0.0", "branch_on": "choice", "save_as": "ruta",
          "choices_from": "facts.directorio.value.choices", "input_view": ["slots.problema"]},
         chosen="transferir", none="esc", low_confidence="esc"),
    node("transferir", "transfer",
         {"target_from": "decisions.ruta.choice", "directory_from": "directorio",
          "packet": {"reason": "routed", "slots": ["problema"]}},
         rejected="esc"),
    node("esc", "escalate", {"reason_code": "policy:transfer_rejected"}),
)


def test_reception_reads_the_directory_chooses_and_the_specialist_continues() -> None:
    w = World(chain_factory=AuditLog, directory=True)
    w.add(ROUTING, ROUTER)
    w.reception(entry="recepcion-ruta", open_run=False)
    # A real reception run (its chain starts with `run_started`), waiting on the first `collect`.
    started = w.engine.start_run(w.principal, None, RunInput(
        agent=AgentSelector(id="recepcion", alias="prod"), idempotency_key="key-e2e",
        subject={"kind": "customer", "ref": "cust-001"}))  # type: ignore[arg-type]
    assert started.session_id is not None
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.decisions.push(make_decision({"choice": "disputas"}, {"choice": True}))

    turn = TurnInput(session_id=started.session_id, text=TEXT, channel="web", client_turn_id="c-e2e")
    result = w.engine.handle_turn(w.principal, None, turn)

    with w.store.uow() as uow:
        source, target = uow.list_runs_by_session(started.session_id)  # two runs
    assert [source.agent.id, target.agent.id] == ["recepcion", "disputas"]
    assert target.release == SPECIALIST_RELEASE_ID and result.run_id == target.run_id
    assert result.messages[-1].text == w.text("t-pedir")  # the specialist's first `collect`
    assert target.active_flow is not None and target.active_flow.node_id == "pedir"
    assert verify_transfer_link(target, w.audit) == []

    # The tool result (a fact the flow saved) is what the transfer and the decision used.
    listed = source.facts["directorio"].value
    assert isinstance(listed, dict) and listed["choices"] == ["disputas", "saldos"]
    transferred = next(e for e in w.audit.read(source.run_id) if e.type == "run_transferred")
    assert transferred.payload.candidates == ["disputas", "saldos"]
    assert transferred.payload.directory_hash == listed["hash"]
    assert transferred.payload.directory == DIRECTORY
    assert transferred.payload.to_agent.id == "disputas"  # chosen among the two published specialists

    # The scripted decision emits no `decision_made`: the choice call is what shows the model was asked.
    assert len(w.decisions.choice_calls) == 1
    model, _inputs, options, locale = w.decisions.choice_calls[0]
    assert model.id == "router" and options == ["disputas", "saldos"] and locale == "es"
