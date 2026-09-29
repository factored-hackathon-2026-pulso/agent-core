from typing import Protocol

from agent_core.domain.shared import TranscriptEntry


class TranscriptStore(Protocol):
    def append(self, entry: TranscriptEntry) -> str:
        """Devuelve el `entry_id`."""
        ...

    def read(self, run_id: str) -> list[TranscriptEntry]: ...

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]: ...
