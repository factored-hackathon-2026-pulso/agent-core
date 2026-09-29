"""Único lugar del repo que lee la hora del sistema (M0 §3)."""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


if TYPE_CHECKING:
    from agent_core.ports.clock import Clock

    def _conforms(x: SystemClock) -> Clock:
        return x
