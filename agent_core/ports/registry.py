from typing import Literal, Protocol

from agent_core.domain.entities import RegistryEntity, Release
from agent_core.domain.identity import Principal
from agent_core.domain.refs import AgentSelector, EntityRef


class RegistryPort(Protocol):
    """Puerto de lectura del registro de entidades y releases (M0 §2.9)."""
    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release: ...

    def release_status(self, release_id: str) -> Literal["active", "revoked"]: ...

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        """Entidad exacta. Una referencia no exacta en su contenido lanza `InvalidRuntimeRef`."""
        ...
