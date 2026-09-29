"""M3 — protocolo de escritura: confirm → act → verify (docs/specs/motor/m03-acciones.md)."""

from agent_core.actions.context import (
    ActionContext,
    AuditProjector,
    EventRecorder,
    PredicateEvaluator,
    RedactAll,
    RefResolver,
    TemplateRenderer,
    append_events,
    exact_ref,
)
from agent_core.actions.machine import TRANSITIONS, VERIFIABLE, Trigger, next_state
from agent_core.actions.manager import ActionManager
from agent_core.actions.results import Answer, AnswerResult, VerifyResult, WriteResult

__all__ = [
    "TRANSITIONS", "VERIFIABLE", "ActionContext", "ActionManager", "Answer", "AnswerResult", "AuditProjector",
    "EventRecorder", "PredicateEvaluator", "RedactAll", "RefResolver", "TemplateRenderer", "Trigger",
    "VerifyResult", "WriteResult", "append_events", "exact_ref", "next_state",
]
