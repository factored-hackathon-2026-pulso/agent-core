"""In-memory `AgentDirectory` over `InMemoryRegistry` (ADR 0021)."""

from agent_core.domain import Agent, EntityKind, EntityRef
from agent_core.ports import DirectoryMember
from testing.fakes.registry import InMemoryRegistry


class InMemoryDirectory:
    def __init__(self, registry: InMemoryRegistry) -> None:
        self._registry = registry

    def members(self, directory: str) -> list[DirectoryMember]:
        found: list[DirectoryMember] = []
        for agent_id, release_id in self._registry.aliases("prod"):
            if self._registry.release_status(release_id) != "active":
                continue
            release = self._registry.resolve_release_by_id(release_id)
            version = release.entities.get(EntityKind.agent, {}).get(agent_id)
            if version is None:
                continue
            agent = self._registry.get(EntityRef(id=agent_id, version=version), Agent)
            if agent.routing is not None and agent.routing.directory == directory:
                found.append((release_id, agent))
        return found
