"""Adaptador de M5 hacia el `DecisionPort` de M2 (m05 §2, m02 D1). Vive en la raíz de composición."""

from agent_core.decision import DecisionOutput, DecisionService, EventScope
from agent_core.domain import Decision, DecisionMade, EntityRef, JsonValue, Locale
from agent_core.interpreter import DecisionResult
from agent_core.views import TokenVault

NONE_CHOICE = "none"


def choice_schema(choices: list[str]) -> dict[str, JsonValue]:
    """Effective output schema of a runtime choice: the options plus `"none"` (ADR 0021, P3)."""
    return {"type": "object", "additionalProperties": False, "required": ["choice"],
            "properties": {"choice": {"type": "string", "enum": [*choices, NONE_CHOICE]}}}


class DecisionAdapter:
    """`DecisionPort` sobre `DecisionService`. Uno por turno: lleva el vault del run y el alcance de los
    eventos (`run_id`, release, sesión). Recibe solo la vista `model`."""

    def __init__(self, service: DecisionService, vault: TokenVault, scope: EventScope) -> None:
        self._service = service
        self._vault = vault
        self._scope = scope

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue],
               locale: Locale) -> DecisionResult:
        output, event = self._service.decide(model, inputs_model_view, locale, self._vault,
                                             scope=self._scope)
        return self._result(output, event)

    def decide_choice(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str],
                      locale: Locale) -> DecisionResult:
        output, event = self._service.decide_with_schema(model, choice_schema(choices), inputs_model_view,
                                                         locale, self._vault, scope=self._scope)
        return self._result(output, event)

    @staticmethod
    def _result(output: DecisionOutput, event: DecisionMade) -> DecisionResult:
        decision = Decision(decision_id=output.decision_id, value=output.value, p_cal=dict(output.p_cal),
                            provider_used=output.provider_used, model_version=output.model_version)
        return DecisionResult(decision=decision, above_threshold=dict(output.above_threshold),
                              events=[event], model_calls=output.model_calls, tokens=output.tokens,
                              cost_usd=output.cost_usd)
