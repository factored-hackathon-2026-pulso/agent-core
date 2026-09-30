"""Opción 2 (2026-09-29): las llamadas de Understand se cuentan aparte de `turn_model_calls` (ADR 0005)."""

from dataclasses import replace
from decimal import Decimal

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


def _priced(tokens: int, cost: str, command: str = "continue"):  # type: ignore[no-untyped-def]
    return replace(cmd(command), model_calls=1, tokens=tokens, cost_usd=Decimal(cost))


def test_understand_cobra_tokens_y_costo_a_los_contadores_del_run() -> None:
    """M4 §16: `max_tokens_per_run` y `max_cost_per_run` deben ver el gasto de Understand."""
    w = World()
    w.open_run(active=True)
    before = w.saved().budgets_used
    w.understand.push(_priced(120, "0.0030"))
    w.turn("hola")
    used = w.saved().budgets_used
    assert used.run_tokens - before.run_tokens >= 120
    assert used.run_cost - before.run_cost >= Decimal("0.0030")


def test_el_gasto_de_understand_se_acumula_entre_turnos() -> None:
    w = World()
    w.open_run(active=True)
    base = w.saved().budgets_used
    w.understand.push(_priced(100, "0.0010"), _priced(50, "0.0005"))
    w.turn("uno")
    w.turn("dos")
    used = w.saved().budgets_used
    assert used.run_tokens - base.run_tokens >= 150
    assert used.run_cost - base.run_cost >= Decimal("0.0015")


def test_el_costo_por_principal_no_se_cuenta_dos_veces() -> None:
    """`add_usage` recibe el delta de `run_cost` (ya incluye Understand), sin sumarlo aparte."""
    w = World()
    w.open_run(active=True)
    w.understand.push(_priced(10, "0.0100"))
    w.turn("hola")
    spent = sum((c for _, c in next(iter(w.store.usage.values()))), Decimal(0))
    assert spent == Decimal("0.0100")
