"""Cuerpos de request y forma publicada de las respuestas (contrato en `contracts/openapi.json`)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

from agent_core.api.protocols import HandoffQuality
from agent_core.domain import (
    AgentSelector,
    ConfirmAnswer,
    JsonValue,
    Locale,
    Model,
    RunResult,
    RunState,
    SubjectRef,
    TurnResult,
    to_jsonable,
)

# `agent` viaja como texto (`id`, `id@alias` o `id@X.Y.Z`); `AgentSelector` lo acepta y lo valida.
AgentText = Annotated[AgentSelector, WithJsonSchema({"type": "string", "examples": ["atencion@prod"]})]


class CreateRunBody(Model):
    agent: AgentText
    subject: SubjectRef | None = None
    input: dict[str, JsonValue] | None = None
    lang: Locale | None = None


class TurnBody(Model):
    text: str = ""
    channel: str
    lang: Locale | None = None
    client_turn_id: str
    confirm: ConfirmAnswer | None = None

    @model_validator(mode="after")
    def _text_or_confirm(self) -> "TurnBody":
        if not self.text.strip() and self.confirm is None:
            raise ValueError("un turno trae texto o una respuesta de confirmación")
        return self


class ResolutionBody(Model):
    resolution_code: str
    handoff_quality: HandoffQuality
    notes: str | None = None


class ProblemBody(BaseModel):
    """`application/problem+json` (M9 §3.5)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    code: str
    detail: str
    trace_id: str


class _StepUp(BaseModel):
    required_level: Literal["anonymous", "session", "step_up"]
    reason: str
    simulated: bool = Field(description="El OTP de la demo es simulado y se etiqueta como tal (ADR 0010).")


def publish_turn(result: TurnResult, *, step_up_simulated: bool) -> dict[str, Any]:
    """`TurnResult` tal como sale por HTTP: `confirmation` como `action_summary` (sin `action_id`) y
    `step_up` marcado como simulado mientras no haya OTP real."""
    data: dict[str, Any] = to_jsonable(result)
    confirmation = result.confirmation
    data["confirmation"] = (
        None
        if confirmation is None
        else {
            "action_summary": confirmation.summary.text,
            "token": confirmation.token,
            "expires_at": to_jsonable(confirmation.expires_at),
        }
    )
    step_up = result.step_up
    data["step_up"] = (
        None
        if step_up is None
        else {
            "required_level": step_up.required_level.value,
            "reason": step_up.reason,
            "simulated": step_up_simulated,
        }
    )
    return data


def publish_run(result: RunResult, *, step_up_simulated: bool) -> dict[str, Any]:
    data: dict[str, Any] = to_jsonable(result)
    if result.first_turn is not None:
        data["first_turn"] = publish_turn(result.first_turn, step_up_simulated=step_up_simulated)
    return data


def run_summary(run: RunState, trace_id: str) -> dict[str, Any]:
    """`GET /v1/runs/{run_id}`: estado resumido, sin slots, hechos, acciones ni principal."""
    return {
        "run_id": run.run_id,
        "status": run.status,
        "outcome": to_jsonable(run.outcome),
        "locale": run.locale,
        "awaiting": to_jsonable(run.awaiting),
        "handoff_ref": run.handoff_ref,
        "trace_id": trace_id,
    }
