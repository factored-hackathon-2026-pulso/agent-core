"""Formato de `eval_suite` (spec §6.1): escenarios sintéticos ligados a un agente."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from agent_core.domain import EntityId, ExactVersion, JsonValue, Outcome, ToolStatus

Fraction = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_M):
    op: Literal["start", "turn", "confirm"]
    text: str | None = Field(default=None, max_length=4000)
    answer: Literal["yes", "no"] | None = None
    lang: str | None = None
    auth: Literal["anonymous", "session", "step_up"] = "step_up"

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if self.op == "turn" and not self.text:
            raise ValueError("un paso `turn` necesita `text`")
        if self.op == "confirm" and self.answer is None:
            raise ValueError("un paso `confirm` necesita `answer`")
        return self


class ToolReply(_M):
    status: ToolStatus = ToolStatus("ok")
    result: JsonValue = None
    error: str | None = None


class SandboxSeed(_M):
    tools: dict[str, list[ToolReply]] = Field(default_factory=dict)


class Expect(_M):
    outcome: Outcome | None = None
    actions_verified: list[str] = Field(default_factory=list)
    escalated: bool | None = None


class ScenarioPrincipal(_M):
    id: str = Field(min_length=1)
    attrs: dict[str, str] = Field(default_factory=dict)


class Scenario(_M):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    principal: ScenarioPrincipal
    steps: list[Step] = Field(min_length=1, max_length=50)
    seed: SandboxSeed = Field(default_factory=SandboxSeed)
    sensitive_values: list[str] = Field(default_factory=list)
    expect: Expect

    @model_validator(mode="after")
    def _starts(self) -> "Scenario":
        if self.steps[0].op != "start" or any(s.op == "start" for s in self.steps[1:]):
            raise ValueError("un escenario empieza con un único paso `start`")
        return self


class EvalSuite(_M):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    repetitions: PositiveInt = Field(default=3, le=10)
    noise_margin: Fraction = Decimal("0.05")
    floor: Fraction = Decimal("0.7")
    scenarios: list[Scenario] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalSuite":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de escenario repetidos")
        return self
