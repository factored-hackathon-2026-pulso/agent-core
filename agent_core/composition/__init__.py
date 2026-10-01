"""Raíz de composición: cablea M2, M4, M5, M6, M7, M8, M10 y M11 con adaptadores. Solo la importa `cli`."""

from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
from agent_core.composition.decision import DecisionAdapter
from agent_core.composition.engine import (
    BuiltEngine,
    DerivedTrace,
    EngineConfig,
    EngineDeps,
    build_engine,
    build_turn_engine,
)
from agent_core.composition.evaluation import EngineScenarioHarness, EvalStorage
from agent_core.composition.registry import UowRunReleases, build_registry_service
from agent_core.composition.responder import ContextFactory, ResponderAdapter
from agent_core.composition.runtime import EngineRuntime, EngineRuntimeFactory, RuntimeConfig

__all__ = [
    "BUILDER_TOOL_DEFS", "BuilderToolExecutor", "BuiltEngine", "ContextFactory", "DecisionAdapter",
    "DerivedTrace", "EngineConfig", "EngineDeps", "EngineRuntime",
    "EngineRuntimeFactory", "EngineScenarioHarness", "EvalStorage", "ResponderAdapter", "RuntimeConfig",
    "UowRunReleases", "build_engine", "build_registry_service", "build_turn_engine",
]
