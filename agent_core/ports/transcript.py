from typing import Protocol

from agent_core.domain.shared import TranscriptEntry


class TranscriptStore(Protocol):
    """Puerto de almacenamiento del transcript (M0 §2.9)."""
    def append(self, entry: TranscriptEntry) -> str:
        """Devuelve el `entry_id`."""
        ...

    def write_turn(self, run_id: str, turn_id: str, entries: list[TranscriptEntry]) -> list[str]:
        """Escribe **todas** las entradas del turno, en orden, de forma atómica e idempotente: reemplaza las
        que el turno ya tuviera (un reintento no duplica ni deja restos de un intento a medias). Devuelve los
        `entry_id` en el mismo orden. Cada entrada debe ser de `run_id` y `turn_id` (si no, `ValueError`)."""
        ...

    def read(self, run_id: str) -> list[TranscriptEntry]: ...

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]: ...
