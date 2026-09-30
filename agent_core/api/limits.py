"""Límites por principal ya validado (M9 §3.1 chequeo 5): tasa y costo diario, con `CostCounters`.

Solo cuenta lo que M4 registra con `UnitOfWork.add_usage` al procesar un turno: un rechazo no consume cuota,
así que un exceso con firma inválida nunca toca la del principal suplantado (T-M9-10)."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from agent_core.domain import EngineError, Principal, ProblemCode
from agent_core.ports import Clock, CostCounters


@dataclass(frozen=True)
class RateLimitConfig:
    """Valores de demo, ajustables. `max_hits` turnos por `window`; `daily_budget_usd` por día UTC."""

    window: timedelta = timedelta(seconds=60)
    max_hits: int = 30
    daily_budget_usd: Decimal = Decimal("5.00")

    def __post_init__(self) -> None:
        if self.window <= timedelta(0) or self.max_hits < 1 or self.daily_budget_usd <= 0:
            raise ValueError("RateLimitConfig: ventana, máximo de turnos y tope diario deben ser positivos")


class LimitGuard:
    def __init__(self, counters: CostCounters, clock: Clock, config: RateLimitConfig) -> None:
        self._counters = counters
        self._clock = clock
        self._config = config

    def check(self, principal: Principal) -> None:
        """`429 rate_limited` o `429 cost_budget_exceeded`. Un anónimo no tiene `id` (sus contadores serían
        los de todos los anónimos): su límite es el del gateway por IP/canal, fuera del motor."""
        if principal.id is None:
            return
        now = self._clock.now()
        if self._counters.hits(principal.key, self._config.window, now) >= self._config.max_hits:
            raise EngineError(ProblemCode.rate_limited)
        if self._counters.spent_today(principal.key, now) >= self._config.daily_budget_usd:
            raise EngineError(ProblemCode.cost_budget_exceeded)
