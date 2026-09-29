"""`TranscriptStore` en memoria (M11). Falla inyectable (`fail_next`) y supresión (`delete_run`), que el
puerto real de la unidad 7 ofrece por su cuenta (retención y supresión propias)."""

import threading
from copy import deepcopy
from typing import TYPE_CHECKING

from agent_core.domain import TranscriptEntry


class TranscriptUnavailable(Exception):
    """Falla simulada del store."""


class InMemoryTranscript:
    def __init__(self) -> None:
        self._entries: dict[str, list[tuple[str, TranscriptEntry]]] = {}
        self._count = 0
        self._failures = 0
        self._lock = threading.RLock()

    def fail_next(self, times: int = 1) -> None:
        with self._lock:
            self._failures += times

    def append(self, entry: TranscriptEntry) -> str:
        with self._lock:
            if self._failures > 0:
                self._failures -= 1
                raise TranscriptUnavailable("store caído")
            self._count += 1
            entry_id = f"entry-{self._count:04d}"
            self._entries.setdefault(entry.run_id, []).append((entry_id, deepcopy(entry)))
            return entry_id

    def read(self, run_id: str) -> list[TranscriptEntry]:
        with self._lock:
            return [deepcopy(e) for _, e in self._entries.get(run_id, [])]

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]:
        with self._lock:
            entries = self._entries.get(run_id, [])
            return [deepcopy(e) for _, e in entries[-n:]] if n > 0 else []

    def delete_run(self, run_id: str) -> None:
        """Supresión (unidad 7). No toca el log de auditoría: T-M11-08."""
        with self._lock:
            self._entries.pop(run_id, None)


if TYPE_CHECKING:
    from agent_core.ports import TranscriptStore

    def _conforms(x: InMemoryTranscript) -> TranscriptStore:
        return x
