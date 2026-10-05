"""Cuerpos de request y forma publicada de las respuestas (contrato en `contracts/openapi.json`)."""

from collections.abc import Sequence
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


# Topes de los campos de texto (M9 §3.1); el body completo lo acota `BodyLimit`.
MAX_TURN_TEXT = 32_000  # techo de transporte; el límite de producto es `max_input_chars` de M6
MAX_SHORT_TEXT = 255
MAX_NOTES = 4000


class CreateRunBody(Model):
    agent: AgentText
    subject: SubjectRef | None = None
    input: dict[str, JsonValue] | None = None
    lang: Locale | None = None


class TurnBody(Model):
    text: str = Field(default="", max_length=MAX_TURN_TEXT)
    channel: str = Field(max_length=MAX_SHORT_TEXT)
    lang: Locale | None = None
    client_turn_id: str = Field(max_length=MAX_SHORT_TEXT)
    confirm: ConfirmAnswer | None = None

    @model_validator(mode="after")
    def _text_or_confirm(self) -> "TurnBody":
        if not self.text.strip() and self.confirm is None:
            raise ValueError("un turno trae texto o una respuesta de confirmación")
        return self


class ResolutionBody(Model):
    resolution_code: str = Field(max_length=MAX_SHORT_TEXT)
    handoff_quality: HandoffQuality
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


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


def session_lineage(session_id: str, runs: Sequence[RunState], trace_id: str) -> dict[str, Any]:
    """`GET /v1/sessions/{id}/lineage`: runs in order with their release and how each one started (ADR 0021).

    `from_event_hash` is an integrity datum of the transfer link and is never published. The directory hash
    is not part of `RunState`, so it is not published either (see the pending item in m09).
    """

    def origin(run: RunState) -> dict[str, Any] | None:
        if run.origin is None:
            return None
        return {
            "transfer_id": run.origin.transfer_id,
            "from_run_id": run.origin.from_run_id,
            "from_agent": str(run.origin.from_agent),
            "from_release_id": run.origin.from_release_id,
        }

    return {
        "session_id": session_id,
        "runs": [
            {
                "run_id": r.run_id,
                "agent": str(r.agent),
                "release": r.release,
                "status": r.status,
                "outcome": to_jsonable(r.outcome),
                "origin": origin(r),
            }
            for r in runs
        ],
        "trace_id": trace_id,
    }
