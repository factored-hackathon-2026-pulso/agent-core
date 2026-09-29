"""Pasos 11-12: avanzar, cerrar y escalar (T-M4-06, T-M4-08 completo; ADR 0013)."""

from typing import Any

import pytest

from agent_core.domain import EngineError, Outcome
from testing.fakes.storage import InMemoryUoW
from tests.m04.harness import RUN_ID, World, flow, node
from tests.m04.helpers import cmd


def escalated_reasons(w: World) -> list[str]:
    return [e.payload.reason_code for e in w.events() if e.type == "escalated"]


def test_t_m4_06_superar_max_repair_turns_escala_sumando_unclear_de_confirm() -> None:
    w = World(agent_over={"max_repair_turns_per_run": 2})
    w.seed_at_confirm()  # confirm con max_attempts 5
    for _ in range(2):
        w.understand.push(cmd("out_of_scope"))
        w.turn("no sé")  # unclear ×2 → repair 2
    assert w.saved().status == "open" and w.saved().repair_turns_used == 2
    w.understand.push(cmd("out_of_scope"))
    result = w.turn("no sé")  # 3 > 2
    assert result.status == "escalated"
    assert escalated_reasons(w) == ["low_confidence"]
    assert w.saved().actions[0].state.value == "cancelled"  # invalidadas antes de escalar


def test_los_reintentos_de_collect_tambien_cuentan_para_el_tope() -> None:
    codigo = flow(
        "codigo",
        10,
        node(
            "pedir",
            "collect",
            {
                "slot": "codigo",
                "prompt_ref": "t-pedir@1.0.0",
                "max_attempts": 5,
                "validator": {"kind": "regex", "value": "[0-9]{4}"},
            },
            ok="fin",
            max_attempts="fin",
        ),
        node("fin", "end", {"outcome": "resolved"}),
    )
    w = World(agent_over={"max_repair_turns_per_run": 2}, extra=(codigo,))
    w.open_run(
        active_flow={"flow": "codigo@1.0.0", "node_id": "pedir"}, awaiting="slot", awaiting_node_id="pedir"
    )
    for _ in range(3):
        w.understand.push(cmd("continue"))
    w.turn("abc")
    w.turn("abd")
    assert w.saved().repair_turns_used == 2 and w.saved().status == "open"
    assert w.turn("abe").status == "escalated" and escalated_reasons(w) == ["low_confidence"]


def test_t_m4_08_escalate_emite_handoff_created_y_el_turno_siguiente_da_410() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("handoff"))
    w.turn("quiero un asesor")
    assert [m.type for m in w.outbox().pending(10)] == ["handoff_created"]
    saved = w.saved()
    assert saved.status == "escalated" and saved.outcome is Outcome.escalated and saved.handoff_ref
    assert saved.inactive_after is None and saved.awaiting.value == "none"
    with pytest.raises(EngineError) as exc:
        w.turn("¿hola?")
    assert exc.value.status == 410


def test_t_m4_08_estado_escalated_evento_y_outbox_en_la_misma_transaccion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("handoff"))

    def boom(self: InMemoryUoW, message: Any) -> None:
        raise RuntimeError("outbox caído")

    monkeypatch.setattr(InMemoryUoW, "enqueue_outbox", boom)
    with pytest.raises(RuntimeError):
        w.turn("quiero un asesor")
    assert w.saved().status == "open" and w.saved().state_version == 1
    assert w.store.handoffs == {} and "escalated" not in w.event_types()
    assert w.outbox().pending(10) == [] and w.store.leases == {}


def test_run_closed_lo_emite_solo_m4_y_una_vez() -> None:
    from agent_core.domain import EVENT_EMITTERS

    assert EVENT_EMITTERS["run_closed"] == frozenset({"M4"})
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("handoff"))
    w.turn("quiero un asesor")
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert len(closed) == 1 and closed[0].payload.closed_by == "escalation"
    types = w.event_types()
    assert types.index("escalated") < types.index("run_closed") < types.index("turn_completed")
    assert types[-1] == "turn_completed"


def test_end_del_flow_cierra_el_run_con_run_closed_flow() -> None:
    w = World()
    prompt = w.seed_at_confirm()
    result = w.turn_confirm(prompt.token, "yes")
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert [(e.payload.closed_by, e.payload.outcome) for e in closed] == [("flow", Outcome.resolved)]
    assert result.status == "closed" and w.saved().closed_at == w.clock.now()
    assert w.saved().active_flow is None and w.saved().inactive_after is None


def test_un_turno_cerrado_no_deja_lease() -> None:
    w = World()
    prompt = w.seed_at_confirm()
    w.turn_confirm(prompt.token, "yes")
    assert w.store.leases == {} and RUN_ID in w.store.runs
