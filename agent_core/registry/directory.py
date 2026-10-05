"""`AgentDirectory` over the registry: `prod` aliases of active releases with a matching routing card."""

from collections.abc import Callable

from agent_core.domain import Agent, EntityKind, EntityRef, Release
from agent_core.ports import DirectoryMember, RegistryPort
from agent_core.registry.store import RegistryStore

ReleaseReader = Callable[[str], Release]  # same shape as `EngineDeps.releases` (`PostgresRegistry.release`)


class RegistryDirectory:
    def __init__(self, store: RegistryStore, registry: RegistryPort, releases: ReleaseReader) -> None:
        self._store, self._registry, self._releases = store, registry, releases

    def members(self, directory: str) -> list[DirectoryMember]:
        with self._store.transaction() as tx:
            pairs = tx.aliases_named("prod")
            paused = tx.paused_agents()
        found: list[DirectoryMember] = []
        for agent_id, release_id in pairs:
            if agent_id in paused:  # en pausa: no recibe casos nuevos (el alias `prod` no se toca)
                continue
            if self._registry.release_status(release_id) != "active":
                continue
            version = self._releases(release_id).entities.get(EntityKind.agent, {}).get(agent_id)
            if version is None:
                continue
            agent = self._registry.get(EntityRef(id=agent_id, version=version), Agent)
            if agent.routing is not None and agent.routing.directory == directory:
                found.append((release_id, agent))
        return sorted(found, key=lambda member: member[1].id)  # codepoint order, not DB collation
