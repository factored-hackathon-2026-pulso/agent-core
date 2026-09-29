from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from testing.builders import NOW

_NS_PER_MICROSECOND = 1_000


class FakeClock:
    """Reloj avanzable: `advance` mueve `now()` y `monotonic_ns()` por igual y es lo único que los mueve."""

    def __init__(self, start: datetime = NOW) -> None:
        if start.utcoffset() != timedelta(0):
            raise ValueError("FakeClock necesita un instante con zona UTC")
        self._now = start
        self._mono = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._mono

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("el reloj no retrocede")
        self._now += delta
        # Aritmética entera: `total_seconds()` es float y pierde microsegundos en deltas grandes.
        self._mono += (delta // timedelta(microseconds=1)) * _NS_PER_MICROSECOND


if TYPE_CHECKING:
    from agent_core.ports import Clock

    def _conforms(x: FakeClock) -> Clock:
        return x
