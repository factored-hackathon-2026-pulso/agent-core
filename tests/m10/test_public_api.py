"""Interfaz pública de M10: lo que importan M4 y M9 (M10 §2)."""

import agent_core.handoff as handoff

EXPECTED = {
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
}


def test_public_api() -> None:
    assert set(handoff.__all__) == EXPECTED
    for name in EXPECTED:
        assert getattr(handoff, name) is not None
