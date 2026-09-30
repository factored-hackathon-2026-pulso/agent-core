"""Adaptadores locales de M4 hacia módulos reales (Fase B)."""

from agent_core.decision import DecisionConfigError, EventScope, UnderstandContext, UnderstandService
from agent_core.domain import EntityKind, RefSpec
from agent_core.ports import TranscriptStore
from agent_core.turn.ports import UnderstandOutcome, UnderstandRequest
from agent_core.turn.refs import pinned_ref


class DecisionUnderstand:
    """`UnderstandPort` sobre `UnderstandService` de M5 (m04 §14).

    Arma el `UnderstandContext` del turno: `model_ref`, `flows` e `interrupts` salen de la release fijada;
    `recent_turns` de las últimas `recent_turns` entradas del transcript (vista `model`, sin borradores
    rechazados); el `TokenVault` del run, del `StepContext` del turno. `slots_model` es la configuración del
    despliegue para la 2.ª llamada de slots (`llm_structured`); sin él M5 no la hace."""

    def __init__(self, service: UnderstandService, transcript: TranscriptStore, *, recent_turns: int,
                 slots_model: RefSpec | None = None) -> None:
        self._service = service
        self._transcript = transcript
        self._recent_turns = recent_turns
        self._slots_model = slots_model

    def run(self, request: UnderstandRequest) -> UnderstandOutcome:
        agent, release, state = request.agent, request.release, request.state
        if agent.understand is None:
            raise DecisionConfigError(f"el agente {agent.id} no declara `understand`")
        recent = [e.text_model for e in self._transcript.recent_turns(state.run_id, self._recent_turns)
                  if e.role != "rejected_draft"]
        context = UnderstandContext(
            model_ref=pinned_ref(release, EntityKind.decision_model, agent.understand),
            flows=sorted(release.entities.get(EntityKind.flow, {})),
            interrupts=[interrupt.id for interrupt in release.interrupts],
            current_node=request.current_node,
            confirm_pending=request.awaiting_confirmation,
            recent_turns=recent,
            token_vault=request.step.vault,
            scope=EventScope(run_id=state.run_id, release=release.id, turn_id=request.turn_id,
                             session_id=state.session_id),
            slots_model_ref=(pinned_ref(release, EntityKind.decision_model, self._slots_model)
                             if self._slots_model is not None else None),
        )
        result, events = self._service.run(request.text_model, context, request.locale)
        return UnderstandOutcome(
            command=result.command, flow=result.flow, interrupt=result.interrupt,
            additional_flows=list(result.additional_flows), slots=dict(result.slots),
            above_threshold=dict(result.above_threshold), decision_id=result.decision_id,
            events=list(events), cost_usd=result.cost_usd, model_calls=result.model_calls,
            tokens=result.tokens)
