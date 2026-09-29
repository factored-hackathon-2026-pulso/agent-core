"""Registro de handlers por tipo de nodo (M2 §2). Agregar un tipo de nodo = agregar su handler aquí."""

from collections.abc import Mapping
from types import MappingProxyType

from agent_core.interpreter.handlers.base import NodeHandler, NodeResult
from agent_core.interpreter.handlers.collect import handle_collect
from agent_core.interpreter.handlers.respond import handle_respond
from agent_core.interpreter.handlers.rule import handle_rule
from agent_core.interpreter.handlers.terminal import handle_end, handle_escalate

HANDLERS: Mapping[str, NodeHandler] = MappingProxyType({
    "collect": handle_collect,
    "respond": handle_respond,
    "rule": handle_rule,
    "escalate": handle_escalate,
    "end": handle_end,
})

__all__ = ["HANDLERS", "NodeHandler", "NodeResult"]
