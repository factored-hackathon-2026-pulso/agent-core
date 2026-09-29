"""Trazabilidad T-M4-01…17 y los seis eventos de M4 en un recorrido completo."""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from agent_core.domain import AgentSelector, EngineError, RunInput
from tests.m04.harness import World
from tests.m04.helpers import cmd

HERE = Path(__file__).parent
M4_EVENT_TYPES = {
    "run_started",
    "turn_started",
    "command_emitted",
    "expiry_evaluated",
    "turn_completed",
    "run_closed",
}


def test_cobertura_t_m4_01_a_17() -> None:
    names = " ".join(
        name
        for path in HERE.glob("test_*.py")
        for name in re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M)
    )
    missing = [f"T-M4-{n:02d}" for n in range(1, 18) if f"t_m4_{n:02d}" not in names]
    assert missing == []


def test_los_seis_eventos_de_m4_se_emiten_en_el_recorrido_completo() -> None:
    w = World()
    started = w.engine.start_run(
        w.principal, None, RunInput(agent=AgentSelector(id="atencion", alias="prod"), idempotency_key="k")
    )
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido")
    w.understand.push(cmd("handoff"))
    w.turn("quiero un asesor")
    seen = {e.type for e in w.store.events[started.run_id]}  # type: ignore[attr-defined]
    assert M4_EVENT_TYPES <= seen


def test_expiry_evaluated_en_abandono_y_run_closed_abandonment() -> None:
    w = World()
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")
    assert {"expiry_evaluated", "run_closed", "turn_completed", "turn_started"} <= set(w.event_types())
