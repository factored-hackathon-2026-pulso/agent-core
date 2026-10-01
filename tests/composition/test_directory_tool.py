"""`directory/list` tool: filtered by eligibility, hash of the full directory (ADR 0021, spec §4)."""

from typing import Any

import pytest

from agent_core.composition.directory import DIRECTORY_TOOL, DirectoryToolExecutor
from agent_core.domain import Agent, EntityRef, Principal, Release, SubjectRef, directory_hash
from agent_core.ports import AuthzDecision, ToolCallContext, ToolStatus
from testing.builders import principal
from testing.fakes.directory import InMemoryDirectory
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.tools import FakeToolExecutor
from tests.m04.harness import agent_data

TOOL = EntityRef(id="directory/list", version="1.0.0")
CARD = {"directory": "customer-care", "examples": []}


class _Authz:
    """Only `authorize_agent` is called by the directory tool; denies the ids in `denied`."""

    def __init__(self, denied: frozenset[str] = frozenset()) -> None:
        self._denied = denied

    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision:
        if agent.id in self._denied:
            return AuthzDecision(allowed=False, reason="denied")
        return AuthzDecision(allowed=True)


def _world(denied: frozenset[str] = frozenset()) -> tuple[DirectoryToolExecutor, ToolCallContext]:
    registry = InMemoryRegistry()
    specs: tuple[tuple[str, dict[str, Any]], ...] = (
        ("disputas", {}), ("asesores", {"invocable_by": ["advisor"]}), ("vetado", {}))
    for agent_id, over in specs:
        agent = Agent.model_validate(agent_data(
            agent_id, routing=CARD | {"summary": agent_id}, accepts={"slots": {}},
            understand="understand@1.0.0", **over))
        registry.add(agent)
        registry.add_release(Release.model_validate({
            "id": f"rel-{agent_id}", "status": "active", "language_detection": "lang@1.0.0",
            "entities": {"agent": {agent_id: "1.0.0"}}}), agent_id)
    ids = FakeIds()
    executor = DirectoryToolExecutor(FakeToolExecutor(ids), InMemoryDirectory(registry), _Authz(denied), ids)
    ctx = ToolCallContext(run_id="run-0001", release="rel-reception", principal=principal(),
                          subject=SubjectRef(kind="customer", ref="cust-001"))
    return executor, ctx


def test_lists_only_eligible_agents_and_hashes_the_full_directory() -> None:
    executor, ctx = _world(frozenset({"vetado"}))
    result = executor.execute(TOOL, {"directory": "customer-care", "locale": "es"}, {}, ctx)
    assert result.status is ToolStatus.ok
    assert isinstance(result.result_full, dict)
    assert result.result_full["choices"] == ["disputas"]
    assert result.result_full["hash"] == directory_hash(
        [("asesores", "rel-asesores"), ("disputas", "rel-disputas"), ("vetado", "rel-vetado")])
    assert result.source == "directory"


def test_locale_not_supported_excludes_everyone() -> None:
    executor, ctx = _world()
    result = executor.execute(TOOL, {"directory": "customer-care", "locale": "fr"}, {}, ctx)
    assert isinstance(result.result_full, dict)
    assert result.result_full["choices"] == []


def test_bad_arguments_are_an_error_result() -> None:
    executor, ctx = _world()
    result = executor.execute(TOOL, {"directory": "customer-care"}, {}, ctx)
    assert result.status is ToolStatus.error and result.error == "bad_args"


def test_definition_is_a_documented_read_tool() -> None:
    executor, _ = _world()
    definition = executor.definition(TOOL)
    assert definition == DIRECTORY_TOOL and definition.risk_class.value == "read"
    assert definition.description and definition.args_schema is not None


def test_other_tools_go_to_the_inner_executor() -> None:
    executor, ctx = _world()
    other = EntityRef(id="otra", version="1.0.0")
    with pytest.raises(KeyError):  # FakeToolExecutor knows nothing about `otra`
        executor.execute(other, {}, {}, ctx)
