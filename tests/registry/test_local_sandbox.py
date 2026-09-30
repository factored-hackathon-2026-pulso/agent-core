from agent_core.domain import EntityRef
from agent_core.ports import ToolCallContext
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalTarget
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import SandboxSeed
from testing.builders import principal
from testing.fakes.ids import FakeIds
from tests.registry.helpers import demo_pinned


def _target() -> EvalTarget:
    pinned = demo_pinned()
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))


CTX = ToolCallContext(run_id="r", release="demo", principal=principal())
TOOL = EntityRef(id="buscar_transacciones", version="1.0.0")


def test_tools_are_marked_as_sandbox_and_serve_seeded_replies() -> None:
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(SandboxSeed.model_validate(
        {"tools": {"buscar_transacciones": [{"result": [1]}, {"result": [2]}]}}), _target())
    tools = sandbox.tools(handle)
    assert getattr(tools, "is_sandbox", False) is True
    assert [tools.execute(TOOL, {}, {}, CTX).result_full for _ in range(3)] == [[1], [2], [2]]
    assert tools.definition(TOOL).id == "buscar_transacciones"


def test_same_idempotency_key_replays_the_write() -> None:
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(SandboxSeed.model_validate(
        {"tools": {"radicar_pqr": [{"result": {"id": "pqr-1"}}, {"result": {"id": "pqr-2"}}]}}), _target())
    tools = sandbox.tools(handle)
    radicar = EntityRef(id="radicar_pqr", version="1.0.0")
    first = tools.execute(radicar, {}, {}, CTX, idempotency_key="k1")
    again = tools.execute(radicar, {}, {}, CTX, idempotency_key="k1")
    assert first.result_full == again.result_full == {"id": "pqr-1"}


def test_unseeded_tool_is_an_error_not_a_crash() -> None:
    sandbox = LocalSandbox(FakeIds())
    tools = sandbox.tools(sandbox.provision(SandboxSeed(), _target()))
    assert tools.execute(TOOL, {}, {}, CTX).status.value == "error"


def test_each_provision_is_isolated() -> None:  # T-REG-23 (sandbox)
    sandbox = LocalSandbox(FakeIds())
    seed = SandboxSeed.model_validate({"tools": {"buscar_transacciones": [{"result": [1]}, {"result": [2]}]}})
    a = sandbox.tools(sandbox.provision(seed, _target()))
    b = sandbox.tools(sandbox.provision(seed, _target()))
    assert a.execute(TOOL, {}, {}, CTX).result_full == [1]
    assert b.execute(TOOL, {}, {}, CTX).result_full == [1]
