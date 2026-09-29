import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.events import EVENT_EMITTERS, EVENT_TYPES, MEASURED_FIELDS, AnyEvent, EngineEvent
from agent_core.domain.json import canonical_bytes, dumps, loads, sha256_hex
from tests.m00.samples import SAMPLE_PAYLOADS, make_event, reverse_keys

EVENTS = TypeAdapter(AnyEvent)
INDEX = Path(__file__).resolve().parents[2] / "docs" / "specs" / "motor" / "00-indice.md"


def test_samples_cover_every_event_type() -> None:
    assert set(SAMPLE_PAYLOADS) == set(EVENT_TYPES)


# T-M0-04
@pytest.mark.parametrize("event_type", sorted(SAMPLE_PAYLOADS))
def test_event_canonical_hash_is_stable(event_type: str) -> None:
    raw = make_event(event_type)
    event = EVENTS.validate_python(raw)
    assert isinstance(event, EVENT_TYPES[event_type])
    reordered = EVENTS.validate_python(reverse_keys(raw))
    assert canonical_bytes(event) == canonical_bytes(reordered)
    assert canonical_bytes(raw) == canonical_bytes(reverse_keys(raw))
    assert len(sha256_hex(canonical_bytes(event))) == 64


def test_events_are_frozen_and_typed() -> None:
    event = EVENTS.validate_python(make_event("run_closed"))
    assert isinstance(event, EngineEvent)
    with pytest.raises(ValidationError):
        event.run_id = "otro"
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed", {"outcome": "resolved", "closed_by": "magia"}))


def _index_rows() -> list[tuple[str, str]]:
    text = INDEX.read_text(encoding="utf-8")
    section = text.split("## 6. Eventos y su emisor", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if line.startswith("| `"):
            events_col, emitter_col = [c.strip() for c in line.strip("|").split("|")][:2]
            rows.append((events_col, emitter_col))
    return rows


def _outside_parens(text: str) -> str:
    return re.sub(r"\([^)]*\)", "", text)


# T-M0-14
def test_emitters_match_index_table() -> None:
    primary: dict[str, set[str]] = {}
    mentioned: dict[str, set[str]] = {}
    for events_col, emitter_col in _index_rows():
        for event in re.findall(r"`([a-z_]+)`", _outside_parens(events_col)):
            if event == "handoff_created":  # outbox, no va a la cadena
                continue
            primary.setdefault(event, set()).update(re.findall(r"M\d+", _outside_parens(emitter_col)))
            mentioned.setdefault(event, set()).update(re.findall(r"M\d+", emitter_col))
    assert set(EVENT_TYPES) == set(EVENT_EMITTERS) == set(primary)
    for event, emitters in EVENT_EMITTERS.items():
        assert primary[event] <= emitters <= mentioned[event], event


# T-M0-15
def test_measured_fields_exist() -> None:
    for event_type, fields in MEASURED_FIELDS.items():
        assert event_type in EVENT_TYPES
        payload_model = EVENT_TYPES[event_type].model_fields["payload"].annotation
        assert payload_model is not None
        assert fields <= set(payload_model.model_fields), event_type
    assert MEASURED_FIELDS["turn_completed"] == frozenset({"duration_ms", "stages"})


def test_unknown_event_type_fails_closed() -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed") | {"type": "no_existe"})
    bad = make_event("run_closed")
    del bad["type"]
    with pytest.raises(ValidationError):
        EVENTS.validate_python(bad)


def test_payload_of_other_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed", SAMPLE_PAYLOADS["escalated"]))


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed") | {"pii": "x"})
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed", {**SAMPLE_PAYLOADS["run_closed"], "extra": 1}))


@pytest.mark.parametrize("field", ["prev_hash", "hash"])
@pytest.mark.parametrize("bad", ["abc", "A" * 64, "0" * 63 + "\n", "0" * 65, "٠" * 64])
def test_chain_hashes_are_sha256_hex(field: str, bad: str) -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed") | {field: bad})
    ok = EVENTS.validate_python(make_event("run_closed") | {field: "a" * 64, "seq": 0})
    assert ok.seq == 0


def test_seq_is_non_negative() -> None:
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed") | {"seq": -1})


def test_args_hash_is_sha256_hex() -> None:
    bad = {**SAMPLE_PAYLOADS["action_dispatched"], "args_hash": "no-es-hash"}
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("action_dispatched", bad))


def test_llm_usage_matches_spec_fields() -> None:
    usage = SAMPLE_PAYLOADS["response_emitted"]["llm"]
    assert {"tokens_in", "tokens_out", "cost_known"} <= set(usage)
    bad = {**SAMPLE_PAYLOADS["response_emitted"], "llm": {**usage, "tokens_in": -1}}
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("response_emitted", bad))
    bad = {**SAMPLE_PAYLOADS["response_emitted"], "llm": {**usage, "cost_usd": "-1"}}
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("response_emitted", bad))


def test_costs_reject_nan() -> None:
    bad = {**SAMPLE_PAYLOADS["decision_made"], "cost_usd": "NaN"}
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("decision_made", bad))


def test_handoff_created_payload_is_defined() -> None:
    from agent_core.domain.events import HandoffCreatedPayload

    payload = HandoffCreatedPayload.model_validate(
        {"handoff_ref": "handoff-0001", "run_id": "run-0001", "target_queue": "disputas", "priority": "high",
         "reason_code": "low_confidence", "language": "es", "reportable_attrs": {"country": "CO"}}
    )
    assert payload.language == "es"
    assert "handoff_created" not in EVENT_TYPES


def test_timestamps_are_normalized_to_utc() -> None:
    event = EVENTS.validate_python(make_event("run_closed") | {"ts": "2026-09-28T07:00:00-05:00"})
    assert event.ts.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed") | {"ts": "2026-09-28T07:00:00"})


@pytest.mark.parametrize("event_type", sorted(SAMPLE_PAYLOADS))
def test_event_canonical_hash_survives_persist_reload(event_type: str) -> None:
    event = EVENTS.validate_python(make_event(event_type))
    reloaded = EVENTS.validate_python(loads(dumps(event)))
    assert canonical_bytes(event) == canonical_bytes(reloaded)
