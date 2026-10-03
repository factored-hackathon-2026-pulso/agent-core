"""Límites por principal ya validado (M9 §3.1 chequeo 5): tasa y costo diario, con `CostCounters`.

Solo cuenta lo que M4 registra con `UnitOfWork.add_usage` al procesar un turno: un rechazo no consume cuota,
así que un exceso con firma inválida nunca toca la del principal suplantado (T-M9-10).

Solo limitan las operaciones que gastan (crear un run, procesar un turno): una lectura o la repetición de una
operación idempotente ya hecha no consume ni se bloquea. Las peticiones en vuelo del mismo principal cuentan
contra el máximo (reserva en proceso), de modo que una ráfaga simultánea no pasa todas el chequeo antes de que
el primer turno registre su uso; entre varias réplicas el exceso queda acotado por las peticiones en vuelo de
cada una. Un principal `service` habla por muchos clientes: su tope es `service_multiplier` veces el de los
demás."""

import math
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from agent_core.domain import EngineError, Principal, PrincipalKey, PrincipalType, ProblemCode
from agent_core.ports import Clock, CostCounters


@dataclass(frozen=True)
class RateLimitConfig:
    """Valores de demo, ajustables. `max_hits` turnos por `window`; `daily_budget_usd` por día UTC."""

    window: timedelta = timedelta(seconds=60)
    max_hits: int = 30
    daily_budget_usd: Decimal = Decimal("5.00")
    service_multiplier: int = 10

    def __post_init__(self) -> None:
        if (self.window <= timedelta(0) or self.max_hits < 1 or self.daily_budget_usd <= 0
                or self.service_multiplier < 1):
            raise ValueError("RateLimitConfig: ventana, máximo de turnos, tope diario y multiplicador "
                             "deben ser positivos")

    def scale(self, principal_type: PrincipalType) -> int:
        return self.service_multiplier if principal_type is PrincipalType.service else 1


def _seconds_to_midnight_utc(now: datetime) -> int:
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((midnight - now).total_seconds()))


class LimitGuard:
    def __init__(self, counters: CostCounters, clock: Clock, config: RateLimitConfig) -> None:
        self._counters = counters
        self._clock = clock
        self._config = config
        self._lock = threading.Lock()
        self._in_flight: Counter[PrincipalKey] = Counter()

    def check(self, principal: Principal) -> None:
        """`429 rate_limited` o `429 cost_budget_exceeded` (con `Retry-After`). Un anónimo no tiene `id` (sus
        contadores serían los de todos los anónimos): su límite es el del gateway por IP/canal."""
        with self._lock:
            self._verify(principal)

    def _verify(self, principal: Principal) -> None:
        if principal.id is None:
            return
        now = self._clock.now()
        scale = self._config.scale(principal.type)
        used = self._counters.hits(principal.key, self._config.window, now) + self._in_flight[principal.key]
        if used >= self._config.max_hits * scale:
            raise EngineError(ProblemCode.rate_limited, retry_after=max(1, math.ceil(
                self._config.window.total_seconds())))
        if self._counters.spent_today(principal.key, now) >= self._config.daily_budget_usd * scale:
            raise EngineError(ProblemCode.cost_budget_exceeded, retry_after=_seconds_to_midnight_utc(now))

    @contextmanager
    def slot(self, principal: Principal) -> Iterator[None]:
        """Chequea y reserva un lugar mientras la petición está en vuelo."""
        if principal.id is None:
            yield
            return
        with self._lock:
            self._verify(principal)
            self._in_flight[principal.key] += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight[principal.key] -= 1
                if self._in_flight[principal.key] <= 0:
                    del self._in_flight[principal.key]
