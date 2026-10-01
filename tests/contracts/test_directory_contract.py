"""`AgentDirectory` contract: in-memory and registry-backed (ADR 0021, spec §4)."""

from collections.abc import Iterator

import pytest

from agent_core.domain import Agent, Release
from agent_core.ports import AgentDirectory
from testing.fakes.directory import InMemoryDirectory
from testing.fakes.registry import InMemoryRegistry
from tests.m04.harness import agent_data

CARD = {"directory": "customer-care", "summary": "s", "examples": []}


def _agent(agent_id: str, directory: str | None = "customer-care") -> Agent:
    card = None if directory is None else CARD | {"directory": directory}
    return Agent.model_validate(agent_data(agent_id, routing=card, accepts={"slots": {}},
                                           understand="understand@1.0.0"))


def _release(release_id: str, agent: Agent) -> Release:
    return Release.model_validate({"id": release_id, "status": "active", "language_detection": "lang@1.0.0",
                                   "entities": {"agent": {agent.id: agent.version}}})


@pytest.fixture
def memory() -> Iterator[tuple[InMemoryRegistry, AgentDirectory]]:
    registry = InMemoryRegistry()
    yield registry, InMemoryDirectory(registry)


def test_members_are_prod_agents_with_the_tag(memory: tuple[InMemoryRegistry, AgentDirectory]) -> None:
    registry, directory = memory
    for agent_id, tag in (("disputas", "customer-care"), ("saldos", "customer-care"), ("interno", "staff"),
                          ("sin-ficha", None)):
        agent = _agent(agent_id, tag)
        registry.add(agent)
        registry.add_release(_release(f"rel-{agent_id}", agent), agent_id, alias="prod")
    assert [(rid, a.id) for rid, a in directory.members("customer-care")] == [
        ("rel-disputas", "disputas"), ("rel-saldos", "saldos")]


def test_members_are_sorted_by_codepoint_whatever_the_insertion_order(
        memory: tuple[InMemoryRegistry, AgentDirectory]) -> None:
    registry, directory = memory
    for agent_id in ("a_b", "ab", "a-z", "a/b"):  # collations disagree with codepoint order on these
        agent = _agent(agent_id)
        registry.add(agent)
        registry.add_release(_release(f"rel-{agent_id.replace('/', '-')}", agent), agent_id, alias="prod")
    assert [a.id for _, a in directory.members("customer-care")] == ["a-z", "a/b", "a_b", "ab"]


def test_revoked_releases_are_not_members(memory: tuple[InMemoryRegistry, AgentDirectory]) -> None:
    registry, directory = memory
    agent = _agent("disputas")
    registry.add(agent)
    registry.add_release(_release("rel-1", agent), "disputas", alias="prod")
    registry.revoke("rel-1")
    assert directory.members("customer-care") == []
