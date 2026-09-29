"""Circuit breaker simple por tool (M2 §3.3, D10). Sin reloj propio: recibe `now` del `Clock` del contexto."""

from collections import deque
from datetime import datetime, timedelta

from agent_core.domain import EntityRef


class CircuitBreaker:
    """Abierto con `threshold` fallas dentro de `window`. Se cierra solo cuando las fallas envejecen."""

    def __init__(self, threshold: int = 5, window: timedelta = timedelta(seconds=60)) -> None:
        if threshold < 1 or window <= timedelta(0):
            raise ValueError("threshold >= 1 y window positiva")
        self._threshold = threshold
        self._window = window
        self._failures: dict[EntityRef, deque[datetime]] = {}

    def _recent(self, tool: EntityRef, now: datetime) -> deque[datetime]:
        failures = self._failures.setdefault(tool, deque())
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        return failures

    def is_open(self, tool: EntityRef, now: datetime) -> bool:
        return len(self._recent(tool, now)) >= self._threshold

    def record_failure(self, tool: EntityRef, now: datetime) -> None:
        self._recent(tool, now).append(now)

    def record_success(self, tool: EntityRef) -> None:
        self._failures.pop(tool, None)
