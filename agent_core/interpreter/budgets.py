"""Presupuestos duros por turno y por run (M2 §3.5). Nada de esto lee la hora fuera de `ctx.clock`."""

from datetime import timedelta
from decimal import Decimal

from agent_core.domain import RunState
from agent_core.interpreter.context import StepContext
from agent_core.ports import Clock


def begin_turn(state: RunState, clock: Clock) -> RunState:
    """Pone en cero los contadores del turno (D4). Lo llama M4 (y el arnés) antes de `advance`."""
    used = state.budgets_used.model_copy(
        update={"turn_nodes": 0, "turn_model_calls": 0, "turn_started_at": clock.now()}
    )
    return state.model_copy(update={"budgets_used": used})


def enter_node(state: RunState, ctx: StepContext) -> tuple[RunState, bool]:
    """Descuenta un nodo. Devuelve `True` si algún presupuesto ya estaba agotado (el nodo no se cuenta)."""
    used = state.budgets_used
    limits = ctx.agent.budgets
    now = ctx.clock.now()
    started = used.turn_started_at or now
    elapsed_ms = (now - started) // timedelta(milliseconds=1)
    exhausted = (
        used.turn_nodes >= limits.max_nodes_per_turn
        or used.run_tokens >= limits.max_tokens_per_run
        or used.run_cost >= limits.max_cost_per_run
        or elapsed_ms >= limits.max_wall_ms_per_turn
    )
    update: dict[str, object] = {"turn_started_at": started}
    if not exhausted:
        update["turn_nodes"] = used.turn_nodes + 1
    return state.model_copy(update={"budgets_used": used.model_copy(update=update)}), exhausted


def model_budget_exhausted(state: RunState, ctx: StepContext) -> bool:
    return state.budgets_used.turn_model_calls >= ctx.agent.budgets.max_model_calls_per_turn


def charge_model(state: RunState, *, calls: int, tokens: int, cost: Decimal) -> RunState:
    used = state.budgets_used
    charged = used.model_copy(update={
        "turn_model_calls": used.turn_model_calls + calls,
        "run_tokens": used.run_tokens + tokens,
        "run_cost": used.run_cost + cost,
    })
    return state.model_copy(update={"budgets_used": charged})
