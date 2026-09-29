from typing import Protocol

from agent_core.domain.base import UtcDatetime


class Clock(Protocol):
    """Única fuente de tiempo del motor (M0 §2.9)."""

    def now(self) -> UtcDatetime:
        """Instante UTC con zona. Única fuente de tiempo para decidir."""
        ...

    def monotonic_ns(self) -> int:
        """Solo para medir duraciones (MEASURED_FIELDS). Nunca decide nada; no decrece."""
        ...
