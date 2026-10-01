"""M2 — intérprete de nodos (docs/specs/motor/m02-interprete.md). Interfaz pública."""

from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.budgets import begin_turn
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome, Stop, TransferRequest
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.loop import advance, start_flow
from agent_core.interpreter.ports import (
    AgentFinal,
    AgentObservation,
    AgentPort,
    AgentRequest,
    AgentStepResult,
    AgentToolCall,
    DecisionPort,
    DecisionResult,
    GenerateRequest,
    GenerateResult,
    ResponderPort,
)
from agent_core.interpreter.projection import Projector

__all__ = [
    "NO_RESUME", "AgentFinal", "AgentObservation", "AgentPort", "AgentRequest", "AgentStepResult",
    "AgentToolCall", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest", "GenerateResult",
    "Projector", "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop",
    "TransferRequest", "advance", "begin_turn", "evaluate", "start_flow", "truthy",
]
