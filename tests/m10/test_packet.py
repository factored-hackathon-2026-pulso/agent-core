"""Tipos del handoff (M10 §2): round-trip por JSON y sin campos extra."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent_core.domain import FactSource, to_jsonable
from agent_core.handoff.packet import (
    ActionView,
    FactView,
    HandoffPacket,
    HandoffRecord,
    RequestSummary,
    Resolution,
    SlotView,
)

TS = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _packet(**over: object) -> HandoffPacket:
    base: dict[str, object] = {
        "handoff_ref": "handoff-0001", "run_id": "run-0001", "release": "rel-1", "agent": "atencion@1.0.0",
        "principal_type": "customer", "subject": {"kind": "customer", "ref": "***"},
        "target_queue": "disputas", "priority": "high", "reason_code": "customer_request", "language": "es",
        "request_summary": RequestSummary(text="resumen", citations=["cargo"]),
        "verified_facts": [FactView(fact_id="fact-0001", name="cargo", value={"amount": Decimal("120.50")},
                                    source=FactSource(kind="tool", ref="get_charge@1.0.0"), ts=TS)],
        "claimed_not_verified": [SlotView(name="documento", value="***", source_turn=1)],
        "actions_taken": [ActionView(action_id="action-0001", tool="radicar_pqr@1.0.0", state="verified",
                                     args={"monto": Decimal("500.00")})],
        "open_questions": [], "evidence_refs": ["call:call-0001"],
        "transcript_ref": "/v1/runs/run-0001/transcript",
    }
    return HandoffPacket.model_validate(base | over)


def test_record_round_trips_through_json_keeping_decimals() -> None:
    record = HandoffRecord(packet=_packet())
    restored = HandoffRecord.model_validate(to_jsonable(record))
    assert restored == record
    assert restored.packet.verified_facts[0].value == {"amount": Decimal("120.50")}
    assert restored.resolution is None


def test_packet_rejects_extra_fields_and_invalid_reason_code() -> None:
    with pytest.raises(ValidationError):
        _packet(transcript="no se incrusta")
    with pytest.raises(ValidationError):
        _packet(reason_code="inventado")
    assert _packet(reason_code="policy:escalamiento-disputa-monto").reason_code.startswith("policy:")


def test_degraded_flag_defaults_to_false() -> None:
    assert _packet().degraded_packet is False
    assert _packet(degraded_packet=True).degraded_packet is True


def test_resolution_quality_is_closed_set() -> None:
    ok = Resolution(resolution_code="resuelto", handoff_quality="useful", notes=None, resolved_at=TS,
                    reader_type="advisor", reader_id="adv-7")
    assert ok.handoff_quality == "useful"
    with pytest.raises(ValidationError):
        Resolution(resolution_code="x", handoff_quality="great", notes=None, resolved_at=TS,
                   reader_type="advisor", reader_id="adv-7")
