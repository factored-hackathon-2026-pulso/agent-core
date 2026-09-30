"""Motor completo compuesto (M2–M11 reales, puertos externos guionados): escenario `disputa-cargo`."""

from typing import Any

from agent_core.audit import AuditLog
from agent_core.domain import Awaiting, Outcome, ResponseEmitted
from testing.engine_world import DRAFT, EngineWorld

TEXT = "no reconozco un cargo de ciento veinte dólares en una tienda"


def _confirmed(w: EngineWorld) -> tuple[Any, Any, Any]:
    started = w.start()
    w.understands("continue")
    w.matches()
    prompt = w.turn(TEXT)
    assert prompt.confirmation is not None
    done = w.confirm("yes")
    return started, prompt, done


def test_el_primer_turno_pide_el_dato() -> None:
    w = EngineWorld()
    started = w.start()
    assert started.first_turn is not None and started.first_turn.awaiting is Awaiting.slot
    assert [m.text for m in started.first_turn.messages] == ["¿Qué cargo quieres disputar?"]


def test_disputa_cargo_de_punta_a_punta_con_respond_generate_real() -> None:
    w = EngineWorld()
    started, prompt, done = _confirmed(w)
    assert prompt.awaiting is Awaiting.confirmation
    state = w.store.runs[started.run_id]
    assert state.status == "closed" and state.outcome is Outcome.resolved
    assert [m.text for m in done.messages] == [DRAFT] and done.messages[0].kind == "generated"
    (emitted,) = [e for e in w.audit.read(started.run_id) if isinstance(e, ResponseEmitted)]
    assert emitted.payload.kind == "generated" and emitted.payload.llm is not None
    assert emitted.payload.llm.calls == 1 and emitted.payload.transcript_fp is not None
    assert len(w.gateway.calls) == 1


def test_la_cadena_de_auditoria_queda_valida_y_el_transcript_completo() -> None:
    w = EngineWorld()
    started, _, _ = _confirmed(w)
    assert AuditLog(w.audit).verify_chain(started.run_id).ok
    roles = [entry.role for entry in w.transcript.read(started.run_id)]
    assert roles == ["user", "assistant"] * 3  # start_run, el turno de texto y el del botón


def test_los_eventos_de_m5_y_m8_llevan_el_turno_en_que_se_produjeron() -> None:
    """Los puertos de M2 no reciben `turn_id`: M4 lo completa al volcarlos, para poder correlacionarlos."""
    w = EngineWorld()
    started, _, _ = _confirmed(w)
    events = [e for e in w.audit.read(started.run_id) if e.type in ("decision_made", "response_emitted")]
    assert {e.type for e in events} == {"decision_made", "response_emitted"}
    assert [e.type for e in events if e.turn_id is None] == []
