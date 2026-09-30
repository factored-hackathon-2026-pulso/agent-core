"""Adaptador de M5 hacia el `DecisionPort` de M2 (m05 §2, m02 D1). Vive en la raíz de composición."""

from agent_core.decision import DecisionService, EventScope
from agent_core.domain import Decision, EntityRef, JsonValue, Locale
from agent_core.interpreter import DecisionResult
from agent_core.views import TokenVault


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
        decision = Decision(decision_id=output.decision_id, value=output.value, p_cal=dict(output.p_cal),
                            provider_used=output.provider_used, model_version=output.model_version)
        return DecisionResult(decision=decision, above_threshold=dict(output.above_threshold),
                              events=[event], model_calls=output.model_calls, tokens=output.tokens,
                              cost_usd=output.cost_usd)
