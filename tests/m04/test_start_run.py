"""`start_run` (m04 §3.1 final): crear, avanzar y, en modo task, llegar a un terminal."""

from typing import Any

import pytest

from agent_core.domain import AgentSelector, Awaiting, Outcome, RunInput
from tests.m04.harness import World


def run_input(agent: str = "atencion", **over: Any) -> RunInput:
    return RunInput.model_validate(
        {"agent": AgentSelector(id=agent, alias="prod"), "idempotency_key": "key-1", **over}
    )


def start(w: World, **over: Any) -> Any:
    return w.engine.start_run(w.principal, None, run_input(**over))


def test_crea_el_run_con_release_fijada_y_locale_inicial() -> None:
    w = World()
    result = start(w, lang="pt")
    state = w.store.runs[result.run_id]
    assert state.release == w.release_id and state.locale == "pt" and state.mode == "conversational"
    assert state.active_flow is not None and state.session_id == result.session_id
    assert state.turn_count == 1 and state.principal.model_dump().get("credential") is None
    assert result.first_turn is not None and result.output is None and result.status == "open"


def test_el_primer_turno_ya_avanzo_hasta_pedir_el_dato() -> None:
    w = World()
    result = start(w)
    assert result.first_turn is not None
    assert [m.text for m in result.first_turn.messages] == [w.text("t-pedir")]
    assert result.first_turn.awaiting is Awaiting.slot
    assert w.store.runs[result.run_id].awaiting_node_id == "pedir"


def test_lang_no_soportado_cae_al_default_locale() -> None:
    w = World()
    assert w.store.runs[start(w, lang="fr").run_id].locale == "es"


def test_emite_run_started_turn_started_y_turn_completed_start_run() -> None:
    w = World()
    result = start(w)
    events = w.store.events[result.run_id]
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert types[0] == "run_started" and types[1] == "turn_started" and types[-1] == "turn_completed"
    assert events[-1].payload.entry == "start_run"  # type: ignore[attr-defined]
    assert events[-1].payload.awaiting is Awaiting.slot  # type: ignore[attr-defined]


def test_sin_authz_run_started_no_lleva_atributos() -> None:
    w = World()
    result = start(w)
    started = w.store.events[result.run_id][0]
    assert started.payload.reportable_attrs == {}  # type: ignore[attr-defined]
    assert started.payload.subject_kind is None  # type: ignore[attr-defined]


def test_solo_los_reportable_attrs_del_authz_viajan_en_run_started() -> None:
    from agent_core.turn import TurnEngine

    w = World()

    class Authz:
        def reportable_attrs(self) -> frozenset[str]:
            return frozenset({"country"})

    engine = TurnEngine(**{**w.engine_kwargs(), "authz": Authz()})
    result = engine.start_run(w.principal, None, run_input())
    started = w.store.events[result.run_id][0]
    assert started.payload.reportable_attrs == {"country": "CO"}  # type: ignore[attr-defined]


def test_task_avanza_hasta_terminal_y_devuelve_output() -> None:
    w = World()
    result = start(w, agent="tarea")
    state = w.store.runs[result.run_id]
    assert result.output == {"dato": {"ok": True}} and result.first_turn is None
    assert (state.status, state.outcome) == ("closed", Outcome.completed) and state.session_id is None
    assert result.session_id is None and state.inactive_after is None
    types = [e.type for e in w.store.events[result.run_id]]  # type: ignore[attr-defined]
    assert types[-2:] == ["run_closed", "turn_completed"]


def test_task_con_input_lo_guarda_como_slots_claimed() -> None:
    w = World()
    result = start(w, agent="tarea", input={"cliente": "demo"})
    slot = w.store.runs[result.run_id].slots["cliente"]
    assert (slot.value, slot.status) == ("demo", "claimed")


def test_task_que_espera_es_un_bug_y_escala_validation_failed() -> None:
    w = World()
    result = start(w, agent="tarea-bug")
    state = w.store.runs[result.run_id]
    assert (state.status, state.outcome) == ("escalated", Outcome.escalated)
    escalated = [e for e in w.store.events[result.run_id] if e.type == "escalated"]  # type: ignore[attr-defined]
    assert [e.payload.reason_code for e in escalated] == ["validation_failed"]  # type: ignore[attr-defined]
    assert result.handoff_ref == state.handoff_ref


def test_start_run_es_una_sola_transaccion_de_estado() -> None:
    w = World()
    start(w)
    assert w.uow_factory.commits == 1  # type: ignore[attr-defined]


def test_start_run_no_toma_lease_ni_guarda_resultado_por_client_turn_id() -> None:
    w = World()
    result = start(w)
    assert w.store.leases == {} and w.store.turn_results == {} and result.run_id == "run-0001"


def test_un_run_task_abierto_nunca_lleva_inactive_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """m04 §3.6: `inactive_after` es del modo conversacional; el barrido no debe ver runs task."""
    from agent_core.turn.closing import Closer

    monkeypatch.setattr(Closer, "apply_outcome", lambda self, frame, outcome: None)
    w = World()
    result = start(w, agent="tarea")
    state = w.store.runs[result.run_id]
    assert state.status == "open" and state.mode == "task" and state.inactive_after is None
