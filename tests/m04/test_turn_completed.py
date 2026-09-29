"""`turn_completed` y medición por etapas (m04 §3.7; T-M4-16, T-M4-17)."""

from datetime import timedelta
from typing import Any

import pytest

from agent_core.domain import MEASURED_FIELDS, AgentSelector, EngineError, RunInput
from tests.m04.harness import RELEASE_ID, World
from tests.m04.helpers import cmd, unsupported


def ms(n: int) -> timedelta:
    return timedelta(milliseconds=n)


def completed(w: World) -> list[Any]:
    return [e for e in w.events() if e.type == "turn_completed"]


def test_t_m4_16_duration_y_stages_exactos_con_fakeclock() -> None:
    w = World(guards_advance=ms(2), understand_advance=ms(30), tool_advance=ms(50), recorder_advance=ms(5))
    w.open_run()
    w.understand.push(cmd("start_flow", flow="procesar"))  # nodo `tool` de lectura → end
    w.turn("hola")
    (event,) = completed(w)
    stages = event.payload.stages
    assert (stages.guards_ms, stages.understand_ms, stages.flow_ms, stages.response_ms) == (2, 30, 50, 5)
    assert event.payload.duration_ms == 87 and event.payload.entry == "turn"
    assert event.payload.awaiting.value == "none" and event.payload.degraded is False


def test_t_m4_16_boton_deja_understand_ms_en_none() -> None:
    w = World(guards_advance=ms(2), tool_advance=ms(10))
    prompt = w.seed_at_confirm()
    w.turn_confirm(prompt.token, "yes")
    (event,) = completed(w)
    stages = event.payload.stages
    assert stages.understand_ms is None and stages.guards_ms == 2 and stages.flow_ms == 20  # write + readback
    assert w.understand.calls == []


def test_t_m4_17_duplicado_409_y_410_no_emiten_turn_completed() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido", client_turn_id="c-1")
    assert len(completed(w)) == 1
    w.turn("cargo desconocido", client_turn_id="c-1")  # duplicado
    assert len(completed(w)) == 1
    with w.store.uow() as other:  # 409
        other.acquire_turn("run-0001", "turn-otro", w.clock.now(), timedelta(seconds=60))
    with pytest.raises(EngineError) as conflict:
        w.turn("otro", client_turn_id="c-2")
    assert conflict.value.status == 409 and len(completed(w)) == 1
    w.clock.advance(timedelta(seconds=61))
    w.store.leases.clear()
    w.understand.push(cmd("handoff"))
    w.turn("quiero un asesor", client_turn_id="c-3")  # cierra el run (escalated)
    assert len(completed(w)) == 2
    with pytest.raises(EngineError) as gone:  # 410
        w.turn("hola", client_turn_id="c-4")
    assert gone.value.status == 410 and len(completed(w)) == 2


def test_t_m4_17_idioma_unsupported_emite_uno_con_flow_ms_none() -> None:
    w = World()
    w.open_run(active=True)
    w.guards.result = unsupported("fr")
    w.turn("bonjour")
    (event,) = completed(w)
    assert event.payload.stages.flow_ms is None and event.payload.stages.understand_ms is None
    assert event.payload.awaiting.value == "slot"  # el run sigue esperando lo mismo


def test_start_run_emite_turn_completed_entry_start_run() -> None:
    w = World()
    result = w.engine.start_run(
        w.principal, None, RunInput(agent=AgentSelector(id="atencion", alias="prod"), idempotency_key="k")
    )
    events = w.store.events[result.run_id]
    assert [e.type for e in events].count("turn_completed") == 1  # type: ignore[attr-defined]
    assert events[-1].payload.entry == "start_run" and events[-1].payload.client_turn_id is None  # type: ignore[attr-defined]


def test_release_revocada_y_abandono_tambien_emiten_turn_completed() -> None:
    revoked = World()
    revoked.open_run(active=True)
    revoked.registry.revoke(RELEASE_ID)
    revoked.turn("hola")
    assert len(completed(revoked)) == 1 and completed(revoked)[0].payload.awaiting.value == "none"
    abandoned = World()
    abandoned.open_run(active=True)
    abandoned.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        abandoned.turn("hola")
    assert len(completed(abandoned)) == 1


def test_turn_completed_es_el_ultimo_evento_del_turno() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido")
    types = w.event_types()
    assert types[-1] == "turn_completed" and types[0] == "turn_started"


def _script(w: World) -> None:
    w.open_run(active=True)
    w.understand.push(cmd("continue"), cmd("continue"))
    w.turn("cargo desconocido")


def _comparable(w: World) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for event in w.events():
        skip = MEASURED_FIELDS.get(event.type, frozenset()) | {"now", "last_activity_at"}
        payload = event.payload.model_dump(mode="json", exclude=set(skip))
        out.append((event.type, payload))
    return out


def test_los_campos_de_medicion_no_influyen_en_las_decisiones() -> None:
    fast = World()
    slow = World(guards_advance=ms(7), understand_advance=ms(90), recorder_advance=ms(13))
    _script(fast)
    _script(slow)
    assert _comparable(fast) == _comparable(slow)
    assert (fast.saved().status, fast.saved().awaiting) == (slow.saved().status, slow.saved().awaiting)
    assert completed(fast)[0].payload.duration_ms != completed(slow)[0].payload.duration_ms
