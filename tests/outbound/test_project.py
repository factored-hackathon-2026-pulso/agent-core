"""Contrato de eventos salientes v1 (docs/specs/2026-10-02-eventos-salientes-design.md)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.audit.replay.fixture import load_fixture_file
from agent_core.domain import (
    EngineEvent,
    HandoffResolved,
    HandoffResolvedPayload,
    OutboxMessage,
    PrincipalType,
    canonical_bytes,
    to_jsonable,
)
from agent_core.outbound import (
    PUBLIC_TYPES,
    SPEC_VERSION,
    OutboundEvent,
    project_engine_event,
    project_outbox_message,
)

FIXTURES = sorted(Path("tests/fixtures").glob("runs*/*.yaml"))
REASON = "policy:escalamiento-disputa-monto"
EXCLUDED_KEYS = {
    "reportable_attrs", "reason", "resolution_code", "notes", "origin", "packet_fp", "directory",
    "directory_hash", "candidates", "subject", "subject_ref", "payload",
}


def _chain(path: Path) -> list[EngineEvent]:
    return list(load_fixture_file(path).events)


def _all_projected() -> list[OutboundEvent]:
    out = [p for f in FIXTURES for e in _chain(f) if (p := project_engine_event(e)) is not None]
    assert out, "los fixtures deben producir eventos públicos"
    return out


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_the_public_list_is_closed() -> None:
    assert PUBLIC_TYPES == (
        "run.started", "run.closed", "run.transferred", "handoff.created", "handoff.resolved",
        "release.published", "release.promoted", "release.revoked")
    assert SPEC_VERSION == 1


def test_only_public_chain_events_are_projected() -> None:
    for f in FIXTURES:
        for e in _chain(f):
            p = project_engine_event(e)
            public = {"run_started", "run_closed", "run_transferred", "handoff_resolved"}
            assert (p is not None) == (e.type in public)


def test_a_resolved_run_projects_start_then_close() -> None:
    types = [p.type for e in _chain(Path("tests/fixtures/runs/resuelto.yaml"))
             if (p := project_engine_event(e))]
    assert types == ["run.started", "run.closed"]


def test_the_event_id_is_the_chain_event_id_and_time_comes_from_the_event() -> None:
    for e in _chain(Path("tests/fixtures/runs/resuelto.yaml")):
        p = project_engine_event(e)
        if p is not None:
            got = (p.event_id, p.occurred_at, p.run_id, p.release_id)
            assert got == (e.event_id, e.ts, e.run_id, e.release)
            assert p.spec_version == 1 and p.source == "engine"


def test_excluded_fields_never_appear_anywhere() -> None:
    for p in _all_projected():
        assert not (_keys(to_jsonable(p)) & EXCLUDED_KEYS), p.type


def test_no_pii_token_or_subject_reference_leaks() -> None:
    blob = json.dumps([to_jsonable(p) for p in _all_projected()], ensure_ascii=False)
    assert "⟦" not in blob and "cust-" not in blob and "CO" not in json.loads(blob)[0]["data"].values()


def test_run_started_keeps_only_the_subject_kind() -> None:
    started = next(p for p in _all_projected() if p.type == "run.started")
    assert set(to_jsonable(started)["data"]) == {"agent", "mode", "principal_type", "locale", "subject_kind"}


def test_run_transferred_exposes_only_the_link() -> None:
    moved = next(p for p in _all_projected() if p.type == "run.transferred")
    assert set(to_jsonable(moved)["data"]) == {"transfer_id", "to_agent", "to_release_id", "to_run_id"}


def test_handoff_resolved_drops_the_resolution_code() -> None:
    chain = _chain(Path("tests/fixtures/runs/resuelto.yaml"))
    base = chain[0]
    resolved = HandoffResolved(
        event_id="event-9", run_id=base.run_id, release=base.release, ts=base.ts,
        payload=HandoffResolvedPayload(handoff_ref="handoff-1", resolution_code="paid_by_agent",
                                       handoff_quality="useful", reader_type=PrincipalType.advisor))
    p = project_engine_event(resolved)
    assert p is not None and p.type == "handoff.resolved"
    assert to_jsonable(p)["data"] == {"handoff_ref": "handoff-1", "handoff_quality": "useful",
                                      "reader_type": "advisor"}


def _outbox() -> OutboxMessage:
    return OutboxMessage.model_validate({
        "message_id": "msg-1", "type": "handoff_created", "run_id": "run-1",
        "created_at": "2026-09-28T12:00:00Z",
        "payload": {"handoff_ref": "handoff-1", "run_id": "run-1", "target_queue": "disputes",
                    "priority": "high", "reason_code": REASON, "language": "es",
                    "reportable_attrs": {"country": "CO"}}})


def test_handoff_created_comes_from_the_outbox_without_reportable_attrs() -> None:
    p = project_outbox_message(_outbox())
    assert p.type == "handoff.created" and p.event_id == "msg-1" and p.run_id == "run-1"
    assert to_jsonable(p)["data"] == {"handoff_ref": "handoff-1", "target_queue": "disputes",
                                      "priority": "high", "reason_code": REASON, "language": "es"}
    assert p.release_id is None


def test_projection_is_deterministic() -> None:
    first = [canonical_bytes(to_jsonable(p)) for p in _all_projected()]
    assert first == [canonical_bytes(to_jsonable(p)) for p in _all_projected()]


def test_the_envelope_rejects_unknown_fields() -> None:
    wire = to_jsonable(project_outbox_message(_outbox()))
    with pytest.raises(ValidationError):
        type(project_outbox_message(_outbox())).model_validate({**wire, "extra": 1})
    with pytest.raises(ValidationError):
        cls = type(project_outbox_message(_outbox()))
        cls.model_validate({**wire, "data": {**wire["data"], "notes": "x"}})
