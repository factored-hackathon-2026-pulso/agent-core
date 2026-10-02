"""Proyección de los eventos del registry a eventos salientes públicos.

Spec: docs/specs/2026-10-02-eventos-salientes-design.md.

Pura. `seq` es la posición del evento en `reg_events` (la pone quien lee); el `event_id` es `reg-<seq>`.
El actor, el origen y el hash del candidato no salen: son personas o detalle interno."""

from agent_core.outbound import (
    OutboundEvent,
    ReleaseData,
    ReleasePromotedEvent,
    ReleasePublishedEvent,
    ReleaseRevokedEvent,
)
from agent_core.registry.models import RegistryEvent


def project_registry_event(event: RegistryEvent, seq: int) -> OutboundEvent | None:
    if event.release_id is None:
        return None
    event_id, occurred_at = f"reg-{seq}", event.at
    data = ReleaseData(release_id=event.release_id, proposal_id=event.proposal_id)
    if event.type == "published":
        return ReleasePublishedEvent(event_id=event_id, occurred_at=occurred_at, data=data)
    if event.type == "promoted":
        return ReleasePromotedEvent(event_id=event_id, occurred_at=occurred_at, data=data)
    if event.type == "revoked":
        return ReleaseRevokedEvent(event_id=event_id, occurred_at=occurred_at, data=data)
    return None
