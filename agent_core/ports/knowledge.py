from typing import Protocol

from agent_core.domain.json import JsonValue


class KnowledgeSource(Protocol):
    """PROVISIONAL hasta cerrar el tema #10 (M12). No se implementa en la fase 1."""

    def capabilities(self) -> frozenset[str]: ...

    def read(self, path: str, snapshot: str, view: str) -> dict[str, JsonValue] | None: ...
