"""Proyección pura: evento de la cadena u outbox → evento saliente público. Sin E/S, sin reloj, sin azar."""

from datetime import datetime
from typing import TypedDict

from agent_core.domain import (
    EngineEvent,
    HandoffCreatedPayload,
    HandoffResolved,
    OutboxMessage,
    RunClosed,
    RunStarted,
    RunTransferred,
)
from agent_core.outbound.models import (
    HandoffCreatedData,
    HandoffCreatedEvent,
    HandoffResolvedData,
    HandoffResolvedEvent,
    OutboundEvent,
    RunClosedData,
    RunClosedEvent,
    RunStartedData,
    RunStartedEvent,
    RunTransferredData,
    RunTransferredEvent,
)


class _Common(TypedDict):
    event_id: str
    occurred_at: datetime
    run_id: str
    session_id: str | None
    turn_id: str | None
    release_id: str


def project_engine_event(event: EngineEvent) -> OutboundEvent | None:
    """`None` para todo evento fuera de la lista cerrada. Cada rama copia campo a campo."""
    common: _Common = {
        "event_id": event.event_id, "occurred_at": event.ts, "run_id": event.run_id,
        "session_id": event.session_id, "turn_id": event.turn_id, "release_id": event.release,
    }
    if isinstance(event, RunStarted):
        p = event.payload
        return RunStartedEvent(**common, data=RunStartedData(
            agent=p.agent, mode=p.mode, principal_type=p.principal_type, locale=p.locale,
            subject_kind=p.subject_kind))
    if isinstance(event, RunClosed):
        return RunClosedEvent(**common, data=RunClosedData(
            outcome=event.payload.outcome, closed_by=event.payload.closed_by))
    if isinstance(event, RunTransferred):
        t = event.payload
        return RunTransferredEvent(**common, data=RunTransferredData(
            transfer_id=t.transfer_id, to_agent=t.to_agent, to_release_id=t.to_release_id,
            to_run_id=t.to_run_id))
    if isinstance(event, HandoffResolved):
        r = event.payload
        return HandoffResolvedEvent(**common, data=HandoffResolvedData(
            handoff_ref=r.handoff_ref, handoff_quality=r.handoff_quality, reader_type=r.reader_type))
    return None


def project_outbox_message(message: OutboxMessage) -> HandoffCreatedEvent:
    """`handoff_created` viaja por el outbox, no por la cadena: `event_id` es el `message_id`."""
    p = HandoffCreatedPayload.model_validate(message.payload)
    return HandoffCreatedEvent(
        event_id=message.message_id, occurred_at=message.created_at, run_id=message.run_id,
        data=HandoffCreatedData(handoff_ref=p.handoff_ref, target_queue=p.target_queue, priority=p.priority,
                                reason_code=p.reason_code, language=p.language))
