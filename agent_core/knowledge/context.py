"""Lo que `KnowledgeService` necesita del exterior (M12 §2). M12 no importa `StepContext`: M2 arma este
contexto."""

from dataclasses import dataclass

from agent_core.domain import Release
from agent_core.ports import Clock, IdSource
from agent_core.views import TokenVault, ViewService


@dataclass(frozen=True, repr=False)
class KnowledgeContext:
    """Release (snapshot fijado), reloj (vigencia), IDs (eventos) y vistas de M7 (proyección a `model`)."""

    release: Release
    clock: Clock
    ids: IdSource
    views: ViewService
    vault: TokenVault
    turn_id: str | None = None

    def __repr__(self) -> str:
        # No muestra el vault: puede contener PII.
        return f"KnowledgeContext(release={self.release.id!r}, turn_id={self.turn_id!r})"
