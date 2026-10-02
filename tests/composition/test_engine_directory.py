"""U3 (ADR 0021, phase 7): `EngineDeps.directory` makes the engine serve `directory/list`."""

from dataclasses import replace

from agent_core.composition import DIRECTORY_TOOL, DirectoryToolExecutor, build_turn_engine
from agent_core.domain import EntityRef
from testing.engine_world import EngineWorld
from testing.fakes.directory import InMemoryDirectory

TOOL = EntityRef(id="directory/list", version="1.0.0")


def _runtime_tools(engine: object) -> object:
    return engine._runtimes._tools  # type: ignore[attr-defined]  # composition detail, as EngineWorld does


def test_without_a_directory_the_tools_are_the_given_ones() -> None:
    world = EngineWorld()
    assert _runtime_tools(build_turn_engine(world.deps)) is world.deps.tools


def test_with_a_directory_the_engine_serves_directory_list_and_delegates_the_rest() -> None:
    world = EngineWorld()
    deps = replace(world.deps, directory=InMemoryDirectory(world.registry))
    tools = _runtime_tools(build_turn_engine(deps))
    assert isinstance(tools, DirectoryToolExecutor)
    assert tools.definition(TOOL) == DIRECTORY_TOOL
    assert tools.definition(EntityRef(id="obtener_pqr", version="1.0.0")).id == "obtener_pqr"
