"""M0 port: the directory of agents a conversation can be transferred to (ADR 0021)."""

from typing import Protocol

from agent_core.domain.entities import Agent

DirectoryMember = tuple[str, Agent]  # (release_id, published agent)


class AgentDirectory(Protocol):
    """Published agents (alias `prod`, active release) whose routing card carries a directory tag (ADR 0021).

    Not filtered by principal: eligibility is the caller's job. Sorted by agent id."""

    def members(self, directory: str) -> list[DirectoryMember]: ...
