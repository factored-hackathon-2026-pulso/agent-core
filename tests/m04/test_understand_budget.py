"""Opción 2 (2026-09-29): las llamadas de Understand se cuentan aparte de `turn_model_calls` (ADR 0005)."""

from dataclasses import replace

from agent_core.turn import TurnConfig
from tests.m04.harness import World
from tests.m04.helpers import cmd
from tests.m04.test_global_handlers import reasons


def _understand(calls: int, command: str = "out_of_scope"):  # type: ignore[no-untyped-def]
    return replace(cmd(command), model_calls=calls)


def test_las_dos_llamadas_de_understand_se_cuentan_y_no_tocan_turn_model_calls() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(_understand(2))
    result = w.turn("hola")
    assert result.status != "escalated"
    used = w.saved().budgets_used
    assert (used.turn_understand_calls, used.turn_model_calls) == (2, 0)


def test_el_contador_se_reinicia_en_cada_turno() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(_understand(2, "continue"), _understand(1, "continue"))
    w.turn("uno")
    assert w.saved().budgets_used.turn_understand_calls == 2
    w.turn("dos")
    assert w.saved().budgets_used.turn_understand_calls == 1


def test_superar_el_tope_escala_con_budget_exceeded_y_no_avanza_el_flow() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(_understand(3))
    result = w.turn("hola")
    assert result.status == "escalated" and reasons(w) == ["budget_exceeded"]
    assert w.saved().budgets_used.turn_understand_calls == 3
    types = w.event_types()
    assert types.index("command_emitted") < types.index("escalated") < types.index("run_closed")


def test_el_tope_es_configurable() -> None:
    w = World(config=TurnConfig(max_understand_calls_per_turn=1))
    w.open_run(active=True)
    w.understand.push(_understand(2))
    assert w.turn("hola").status == "escalated"
