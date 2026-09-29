"""M8 — validador de respuesta y `respond(generate)` (spec docs/specs/motor/m08-validador-de-respuesta.md)."""

from agent_core.response.checks import CHECKS
from agent_core.response.numbers import NumberFormat
from agent_core.response.responder import Responder, ResponderContext
from agent_core.response.templates import TemplateUnavailable
from agent_core.response.types import Draft, Failure, ValidationContext, ValidationResult, parse_draft
from agent_core.response.validate import validate

__all__ = [
    "CHECKS", "Draft", "Failure", "NumberFormat", "Responder", "ResponderContext", "TemplateUnavailable",
    "ValidationContext", "ValidationResult", "parse_draft", "validate",
]
