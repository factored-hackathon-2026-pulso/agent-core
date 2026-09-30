"""`RegistryPort` de solo lectura sobre una release en memoria (spec §5.3). Solo lo usa el evaluador."""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from agent_core.domain import (
    AgentSelector,
    EntityKind,
    EntityRef,
    Principal,
    RegistryEntity,
    Release,
    require_exact_refs,
)


class SnapshotRegistry:
    def __init__(self, release: Release, entities: Iterable[RegistryEntity]) -> None:
        require_exact_refs(release)
        self._release = release.model_copy(deep=True)
        self._entities: dict[tuple[type[BaseModel], str, str], BaseModel] = {
            (type(e), e.id, e.version): e.model_copy(deep=True) for e in entities}

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        agents = self._release.entities.get(EntityKind.agent, {})
        if selector.id not in agents:
            raise KeyError(selector.id)
        if selector.version is not None and selector.version != agents[selector.id]:
            raise KeyError(f"{selector.id}@{selector.version}")
        return self._release.model_copy(deep=True)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        if release_id != self._release.id:
            raise KeyError(release_id)
        return "active"

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        stored = self._entities[(kind, ref.id, ref.version)]
        require_exact_refs(stored)
        entity = stored.model_copy(deep=True)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref.id}@{ref.version} no es {kind.__name__}")
        return entity
