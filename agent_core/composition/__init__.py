"""Raíz de composición: cablea M2, M4, M5, M6, M7, M8, M10 y M11 con adaptadores. Solo la importa `cli`."""

from agent_core.composition.decision import DecisionAdapter
from agent_core.composition.responder import ContextFactory, ResponderAdapter

__all__ = ["ContextFactory", "DecisionAdapter", "ResponderAdapter"]
