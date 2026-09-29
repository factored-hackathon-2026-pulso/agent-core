"""Construcción del `HandoffPacket` (M10 §3.1, paso 2). Funciones puras sobre el estado y los eventos.

Todo valor de cliente entra por `Projector.audit` (M7): el paquete persistido nunca lleva `full`."""

from collections.abc import Sequence

from agent_core.domain import (
    ActionState,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    RuleEvaluated,
    RunState,
    SubjectRef,
    ToolCalled,
)
from agent_core.handoff.packet import ActionView, FactView, HandoffPacket, RequestSummary, SlotView
from agent_core.handoff.projection import Projector
from agent_core.handoff.texts import render_summary
from agent_core.views import ViewService

TRANSCRIPT_PATH = "/v1/runs/{run_id}/transcript"
MASK = "***"


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _fact_views(state: RunState, projector: Projector) -> list[FactView]:
    return [
        FactView(fact_id=fact.fact_id, name=name, value=projector.audit(state.run_id, fact.value, name),
                 source=fact.source, ts=fact.ts)
        for name, fact in state.facts.items()
    ]


def _slot_views(state: RunState, projector: Projector) -> list[SlotView]:
    return [
        SlotView(name=name, value=projector.audit(state.run_id, slot.value, name),
                 source_turn=slot.source_turn)
        for name, slot in state.slots.items()
        if slot.status == "claimed"
    ]


def _action_views(state: RunState, projector: Projector) -> list[ActionView]:
    return [
        ActionView(action_id=a.action_id, tool=a.tool, state=a.state,
                   args=_as_dict(projector.audit(state.run_id, a.args, a.tool.id)),
                   cancel_reason=a.cancel_reason)
        for a in state.actions
    ]


def evidence_refs(state: RunState, events: Sequence[EngineEvent]) -> list[str]:
    """`call:<id>`, `policy:<id>@<v>`, `decision:<id>` y `page:<ref>`; sin duplicados, en orden."""
    refs: list[str] = []

    def add(ref: str) -> None:
        if ref not in refs:
            refs.append(ref)

    for event in events:
        if isinstance(event, ToolCalled):
            add(f"call:{event.payload.call_id}")
        elif isinstance(event, RuleEvaluated) and event.payload.policy is not None:
            add(f"policy:{event.payload.policy}")
    for decision in state.decisions.values():
        add(f"decision:{decision.decision_id}")
    for fact in state.facts.values():
        if fact.source.kind == "knowledge":
            add(f"page:{fact.source.ref}")
    return refs


def _open_questions(state: RunState, views: ViewService) -> list[str]:
    facts_full: dict[str, JsonValue] = {name: fact.value for name, fact in state.facts.items()}
    facts_full |= {name: slot.value for name, slot in state.slots.items()}
    return [MASK if views.find_clear_pii(q, facts_full) else q for q in state.open_questions]


def _summary(state: RunState, request: EscalationRequest, facts: Sequence[FactView]) -> RequestSummary:
    uncertain = sum(1 for a in state.actions if a.state is ActionState.uncertain)
    text = render_summary(
        request.reason_code, state.locale,
        flow=state.active_flow.flow.id if state.active_flow else None,
        facts=len(facts), actions=len(state.actions), uncertain=uncertain,
    )
    return RequestSummary(text=text, citations=[f.name for f in facts])


def _masked_subject(state: RunState) -> SubjectRef | None:
    return None if state.subject is None else SubjectRef(kind=state.subject.kind, ref=MASK)


def build_packet(*, state: RunState, request: EscalationRequest, handoff_ref: str,
                 events: Sequence[EngineEvent], projector: Projector, views: ViewService) -> HandoffPacket:
    facts = _fact_views(state, projector)
    return HandoffPacket(
        handoff_ref=handoff_ref, run_id=state.run_id, release=state.release, agent=state.agent,
        principal_type=state.principal.type, subject=_masked_subject(state),
        target_queue=request.target_queue, priority=request.priority, reason_code=request.reason_code,
        language=state.locale, request_summary=_summary(state, request, facts),
        verified_facts=facts, claimed_not_verified=_slot_views(state, projector),
        actions_taken=_action_views(state, projector), open_questions=_open_questions(state, views),
        evidence_refs=evidence_refs(state, events),
        transcript_ref=TRANSCRIPT_PATH.format(run_id=state.run_id),
    )


def build_minimal_packet(*, state: RunState, request: EscalationRequest, handoff_ref: str) -> HandoffPacket:
    """Paquete de respaldo (§5 del spec): `reason_code`, hechos sin valores y `transcript_ref`."""
    return HandoffPacket(
        handoff_ref=handoff_ref, run_id=state.run_id, release=state.release, agent=state.agent,
        principal_type=state.principal.type, subject=_masked_subject(state),
        target_queue=request.target_queue, priority=request.priority, reason_code=request.reason_code,
        language=state.locale, request_summary=RequestSummary(text=request.reason_code),
        verified_facts=[FactView(fact_id=f.fact_id, name=name, source=f.source, ts=f.ts)
                        for name, f in state.facts.items()],
        claimed_not_verified=[], actions_taken=[], open_questions=[], evidence_refs=[],
        transcript_ref=TRANSCRIPT_PATH.format(run_id=state.run_id), degraded_packet=True,
    )
