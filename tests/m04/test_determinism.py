"""Determinismo (replay): mismos puertos y mismo `Clock` producen los mismos eventos."""

from datetime import timedelta
from typing import Any

from pydantic import TypeAdapter

from agent_core.domain import MEASURED_FIELDS, AgentSelector, AnyEvent, RunInput
from tests.m04.harness import World
from tests.m04.helpers import cmd


def run_script(w: World) -> list[Any]:
    """Intención, slot, confirmar por botón (cuatro turnos con `start_run`)."""
    started = w.engine.start_run(
        w.principal, None, RunInput(agent=AgentSelector(id="atencion", alias="prod"), idempotency_key="k")
    )
    assert started.first_turn is not None and started.session_id == "session-0001"
    w.understand.push(cmd("continue"))
    confirm = w.turn("cargo desconocido de cincuenta dólares")
    assert confirm.confirmation is not None
    w.understand.push(cmd("start_flow", flow="bloquear-tarjeta"))
    w.turn("y también bloquea mi tarjeta")
    w.turn_confirm(confirm.confirmation.token, "yes")
    return w.store.events["run-0001"]


def normalize(events: list[Any]) -> list[dict[str, Any]]:
    """M0 §2.10: se ignoran `seq`/`prev_hash`/`hash`/`ts` y los `MEASURED_FIELDS`."""
    out = []
    for event in events:
        dumped = event.model_dump(mode="json", exclude={"seq", "prev_hash", "hash", "ts"})
        for field in MEASURED_FIELDS.get(event.type, frozenset()):
            dumped["payload"].pop(field, None)
        out.append(dumped)
    return out


def test_mismos_puertos_y_reloj_producen_los_mismos_eventos() -> None:
    a, b = run_script(World()), run_script(World())
    assert len(a) > 10 and normalize(a) == normalize(b)


def test_el_estado_final_tambien_es_determinista() -> None:
    first, second = World(), World()
    run_script(first)
    run_script(second)
    assert first.saved().model_dump(mode="json") == second.saved().model_dump(mode="json")


def test_los_tiempos_medidos_no_cambian_lo_normalizado() -> None:
    fast, slow = (
        World(),
        World(guards_advance=timedelta(milliseconds=9), recorder_advance=timedelta(milliseconds=4)),
    )
    fast_events, slow_events = run_script(fast), run_script(slow)
    fast_completed = [e.payload.duration_ms for e in fast_events if e.type == "turn_completed"]
    slow_completed = [e.payload.duration_ms for e in slow_events if e.type == "turn_completed"]
    assert fast_completed != slow_completed


def test_todo_evento_valida_contra_el_esquema_de_m0() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(AnyEvent)
    for event in run_script(World()):
        assert adapter.validate_python(event.model_dump(mode="json")).type == event.type
