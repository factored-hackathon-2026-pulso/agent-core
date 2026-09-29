"""M2 — intérprete de nodos (docs/specs/motor/m02-interprete.md). Interfaz pública."""

from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome, Stop
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.ports import (
    DecisionPort,
    DecisionResult,
    GenerateRequest,
    GenerateResult,
    ResponderPort,
)

__all__ = [
    "NO_RESUME", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest", "GenerateResult",
    "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop", "evaluate", "truthy",
]
