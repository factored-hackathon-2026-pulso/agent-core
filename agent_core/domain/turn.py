"""Entrada y salida de turnos y runs (M0 §2.8)."""

from typing import Literal

from pydantic import model_validator

from agent_core.domain.base import Locale, Model, UtcDatetime
from agent_core.domain.identity import AuthLevel, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Outcome
from agent_core.domain.refs import AgentSelector, EntityRef
from agent_core.domain.state import RunStatus


class ConfirmAnswer(Model):
    """Respuesta del usuario (sí/no) a una confirmación, con su token (M0 §2.8)."""
    token: str
    answer: Literal["yes", "no"]


class TurnInput(Model):
    """Entrada de un turno conversacional: texto o respuesta de confirmación (M0 §2.8)."""
    session_id: str
    text: str = ""
    channel: str
    lang: Locale | None = None
    client_turn_id: str
    confirm: ConfirmAnswer | None = None

    @model_validator(mode="after")
    def _text_or_confirm(self) -> "TurnInput":
        if not self.text.strip() and self.confirm is None:
            raise ValueError("un turno trae texto o una respuesta de confirmación")
        return self


class RunInput(Model):
    """Entrada para crear un run de tipo task (M0 §2.8)."""
    agent: AgentSelector
    subject: SubjectRef | None = None
    input: dict[str, JsonValue] | None = None
    lang: Locale | None = None
    idempotency_key: str


class Message(Model):
    """Mensaje al usuario: plantilla o generado, con su locale (M0 §2.8)."""
    kind: Literal["template", "generated"]
    text: str
    locale: Locale


class ConfirmationPrompt(Model):
    """Forma interna; la API publica `summary.text` como `action_summary`."""

    action_id: str
    token: str
    expires_at: UtcDatetime
    summary: Message


class StepUpPrompt(Model):
    """Solicitud de elevar el nivel de autenticación (M0 §2.8)."""
    required_level: AuthLevel
    reason: str


class TurnResult(Model):
    """Resultado de un turno: mensajes, estado esperado y outcome (M0 §2.8)."""
    run_id: str
    turn_id: str
    messages: list[Message]
    locale: Locale
    awaiting: Awaiting
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    status: RunStatus
    outcome: Outcome | None = None
    handoff_ref: str | None = None
    agent: EntityRef | None = None  # the agent that answered (differs from the origin after a transfer)
    trace_id: str


class RunResult(Model):
    """Resultado de crear o consultar un run (M0 §2.8)."""
    run_id: str
    session_id: str | None = None
    release: str
    output: dict[str, JsonValue] | None = None
    status: RunStatus
    outcome: Outcome | None = None
    handoff_ref: str | None = None
    first_turn: TurnResult | None = None
    trace_id: str
