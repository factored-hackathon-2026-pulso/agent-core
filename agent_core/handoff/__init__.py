"""M10 — escalamiento y handoff (ADR 0013): paquete estructurado, evento saliente y cierre del run."""

from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.packet import (
    ActionView,
    FactView,
    HandoffPacket,
    HandoffQuality,
    HandoffRecord,
    RequestSummary,
    Resolution,
    SlotView,
)
from agent_core.handoff.recorder import EventRecorder, append_events
from agent_core.handoff.service import HandoffService

__all__ = [
    "ActionView",
    "EventRecorder",
    "FactView",
    "HandoffPacket",
    "HandoffPreconditionError",
    "HandoffQuality",
    "HandoffRecord",
    "HandoffService",
    "RequestSummary",
    "Resolution",
    "SlotView",
    "append_events",
]
