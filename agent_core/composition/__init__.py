"""Raíz de composición: cablea M2, M4, M5, M6, M7, M8, M10 y M11 con adaptadores. Solo la importa `cli`."""

from agent_core.composition.decision import DecisionAdapter
from agent_core.composition.engine import DerivedTrace, EngineConfig, EngineDeps, build_turn_engine
from agent_core.composition.responder import ContextFactory, ResponderAdapter
from agent_core.composition.runtime import EngineRuntime, EngineRuntimeFactory, RuntimeConfig

__all__ = [
    "ContextFactory", "DecisionAdapter", "DerivedTrace", "EngineConfig", "EngineDeps", "EngineRuntime",
    "EngineRuntimeFactory", "ResponderAdapter", "RuntimeConfig", "build_turn_engine",
]
