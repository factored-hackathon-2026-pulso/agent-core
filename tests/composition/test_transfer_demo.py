"""Phase 7 demo (ADR 0021, spec §1): reception transfers to `disputas`, which answers in the same turn."""

from typing import Any

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.api.schemas import session_lineage
from agent_core.audit import verify_transfer_link
from agent_core.composition.serve import build_api_deps
from agent_core.domain import JsonValue, Outcome, directory_hash, dumps
from testing.engine_world import CATALOG, EngineWorld, transfer_calibration, transfer_world
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

TEXT = "no reconozco un cargo de ciento veinte dólares en una tienda"
ASK = "¿En qué te puedo ayudar?"
HANDOVER = "Te comunico con un especialista."
DISPUTAS_FIRST = "¿Qué cargo quieres disputar?"
CLARIFY = "No entendí bien qué necesitas. ¿Me lo cuentas con otras palabras?"


def _script_transfer(w: EngineWorld, to: str = "disputas", flow: str = "disputa-cargo") -> None:
    w.understands("continue")               # reception: the text answers its `collect`
    w.routes(to)                            # elegir-especialista picks `to` (classifier, p=0.9 >= "*" 0.8)
    w.understands("start_flow", flow=flow)  # the specialist starts its flow with the same text (P4)


def test_reception_transfers_and_disputas_answers_in_the_same_turn() -> None:
    w = transfer_world()
    started = w.start()
    assert [m.text for m in started.first_turn.messages] == [ASK]
    _script_transfer(w)
    result = w.turn(TEXT)

    assert result.agent is not None and result.agent.id == "disputas"
    assert [m.text for m in result.messages] == [HANDOVER, DISPUTAS_FIRST]
    with w.store.uow() as uow:
        origin, target = uow.list_runs_by_session(started.session_id)
    assert (origin.agent.id, origin.release, origin.outcome) == (
        "recepcion", "recepcion-demo", Outcome.transferred)
    assert (target.agent.id, target.release, target.status) == ("disputas", "disputas-demo", "open")
    assert result.run_id == target.run_id
    assert verify_transfer_link(target, w.audit) == []

    moved = next(e for e in w.audit.read(origin.run_id) if e.type == "run_transferred")
    assert moved.payload.directory == "atencion-cliente"
    assert moved.payload.candidates == ["consultas", "disputas"]
    assert moved.payload.directory_hash == directory_hash(
        [("consultas", "consultas-demo"), ("disputas", "disputas-demo")])

    lineage = session_lineage(started.session_id, [origin, target], "trace-demo")
    assert [r["release"] for r in lineage["runs"]] == ["recepcion-demo", "disputas-demo"]
    assert lineage["runs"][1]["origin"]["transfer_id"] == moved.payload.transfer_id


def test_the_router_passed_by_the_hand_made_wildcard_threshold() -> None:
    w = transfer_world()
    started = w.start()
    _script_transfer(w)
    w.turn(TEXT)
    with w.store.uow() as uow:
        origin = uow.list_runs_by_session(started.session_id)[0]
    routed = [e for e in w.audit.read(origin.run_id)
              if e.type == "decision_made" and e.payload.model.id == "elegir-especialista"]
    assert len(routed) == 1 and routed[0].payload.above_threshold == {"choice": True}


def test_without_the_wildcard_threshold_reception_asks_again() -> None:
    art = transfer_calibration()
    kept = {k: v for k, v in art.thresholds.items() if k[1] != "*"}
    no_wildcard = art.model_copy(update={"thresholds": kept})
    w = transfer_world(calibrations={art.run_id: no_wildcard})
    started = w.start()
    w.understands("continue")
    w.routes("disputas")
    result = w.turn(TEXT)
    assert [m.text for m in result.messages] == [CLARIFY]
    with w.store.uow() as uow:
        runs = uow.list_runs_by_session(started.session_id)
    assert len(runs) == 1
    routed = [e for e in w.audit.read(runs[0].run_id)
              if e.type == "decision_made" and e.payload.model.id == "elegir-especialista"]
    assert len(routed) == 1 and routed[0].payload.above_threshold == {"choice": False}


def test_reception_can_also_transfer_to_consultas() -> None:
    w = transfer_world()
    w.start()
    _script_transfer(w, to="consultas", flow="consulta-pqr")
    result = w.turn("quiero saber cómo va mi reclamo de la semana pasada")
    assert result.agent is not None and result.agent.id == "consultas"
    assert [m.text for m in result.messages] == [HANDOVER, "¿Cuál es el número de radicado de tu PQR?"]


def test_no_event_carries_the_customer_text() -> None:
    w = transfer_world()
    started = w.start()
    _script_transfer(w)
    w.turn(TEXT)
    with w.store.uow() as uow:
        runs = uow.list_runs_by_session(started.session_id)
    assert len(runs) == 2
    for run in runs:
        assert TEXT not in dumps(w.audit.read(run.run_id))


def _router_inputs(w: EngineWorld) -> dict[str, JsonValue]:
    spec, inputs, _locale = w.classifier.calls[-1]
    assert spec.provider == "classifier"
    return inputs


def test_the_directory_ids_reach_the_router_in_clear() -> None:
    """F4: `choices`, `agent_id` and `release_id` are public; without the rules they would be tokens."""
    w = transfer_world()
    w.start()
    _script_transfer(w)
    w.turn(TEXT)
    entries = _router_inputs(w)["facts.directorio.value.entries"]
    assert isinstance(entries, list)
    assert [e["agent_id"] for e in entries if isinstance(e, dict)] == ["consultas", "disputas"]
    assert [e["release_id"] for e in entries if isinstance(e, dict)] == ["consultas-demo", "disputas-demo"]


def test_the_routing_cards_reach_the_router_as_untrusted_text() -> None:
    """Q3: `summary` and `examples` are registry-authored text, wrapped as untrusted, never tokenized."""
    assert CATALOG["directory.entries.summary"].field_class == "untrusted_text"
    assert CATALOG["directory.entries.examples"].field_class == "untrusted_text"
    w = transfer_world()
    w.start()
    _script_transfer(w)
    w.turn(TEXT)
    entries = _router_inputs(w)["facts.directorio.value.entries"]
    assert isinstance(entries, list)
    disputas: Any = entries[1]
    shown = dumps(disputas)
    assert "Disputa un cargo de la tarjeta que la persona no reconoce." in shown
    assert "no reconozco un cargo" in shown
    assert str(disputas["summary"]).startswith("<datos_no_confiables")  # wrapped, not tokenized
    assert "⟦pii" not in dumps([disputas["summary"], disputas["examples"]])


def test_the_transfer_demo_goes_through_http() -> None:
    world = transfer_world()
    issuer = TestIdentityIssuer(world.clock)
    ports = make_ports(world, issuer)
    assert ports.directory is not None  # deps.directory of the non-recording world (F5)
    client = TestClient(create_app(build_api_deps(ports)), raise_server_exceptions=False)
    bearer = {"Authorization": f"Bearer {issuer.customer()}"}
    created = client.post("/v1/runs", json={"agent": "recepcion"},
                          headers={**bearer, "Idempotency-Key": "k-tr"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert [m["text"] for m in body["first_turn"]["messages"]] == [ASK]
    _script_transfer(world)
    turn = client.post(f"/v1/sessions/{body['session_id']}/turns", headers=bearer,
                       json={"text": TEXT, "channel": "web", "client_turn_id": "c-1"})
    assert turn.status_code == 200, turn.text
    assert [m["text"] for m in turn.json()["messages"]] == [HANDOVER, DISPUTAS_FIRST]
    assert turn.json()["run_id"] != body["run_id"]
    lineage = client.get(f"/v1/sessions/{body['session_id']}/lineage", headers=bearer)
    assert lineage.status_code == 200, lineage.text
    assert [r["release"] for r in lineage.json()["runs"]] == ["recepcion-demo", "disputas-demo"]


def test_the_recording_world_records_the_directory_call() -> None:
    """F5: when recording, `directory/list` goes through the recorder and the engine gets no directory."""
    w = transfer_world(record=True)
    assert w.deps.directory is None and w.recording_tools is not None
    w.start()
    _script_transfer(w)
    result = w.turn(TEXT)
    assert [m.text for m in result.messages] == [HANDOVER, DISPUTAS_FIRST]
    sources = [r.source for r in w.recording_tools.captured.values()]
    assert sources.count("directory") == 1
