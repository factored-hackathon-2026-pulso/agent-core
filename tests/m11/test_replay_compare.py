"""T-M11-10: los campos de medición no cuentan; cualquier otro cambio del mismo evento sí."""

from decimal import Decimal

from agent_core.audit.replay.compare import first_divergence, normalize
from agent_core.domain import MEASURED_FIELDS
from tests.m11.helpers import event


def with_payload(event_type: str, **changes: object) -> object:
    base = event(event_type)
    return base.model_copy(update={"payload": base.payload.model_copy(update=changes)})  # type: ignore[attr-defined]


def test_t_m11_10_measured_fields_are_ignored() -> None:
    cases = {
        "decision_made": {"latency_ms": 999},
        "tool_called": {"latency_ms": 999},
        "turn_completed": {"duration_ms": 12345},
    }
    for event_type, change in cases.items():
        a, b = event(event_type), with_payload(event_type, **change)
        assert first_divergence([a], [b]) is None  # type: ignore[list-item]


def test_response_emitted_llm_is_ignored_but_validator_is_not() -> None:
    a = event("response_emitted")
    llm = a.payload.llm.model_copy(update={"latency_ms": 1, "cost_usd": Decimal("9.9")})  # type: ignore[attr-defined]
    assert first_divergence([a], [with_payload("response_emitted", llm=llm)]) is None  # type: ignore[list-item]
    bad = a.payload.validator.model_copy(update={"ok": False})  # type: ignore[attr-defined]
    div = first_divergence([a], [with_payload("response_emitted", validator=bad)])  # type: ignore[list-item]
    assert div is not None


def test_t_m11_10_change_in_any_other_field_of_the_same_event_diverges() -> None:
    a = event("turn_completed")
    changed = with_payload("turn_completed", degraded=True, duration_ms=1)  # mide distinto Y cambia degraded
    assert first_divergence([a], [changed]) is not None  # type: ignore[list-item]


def test_envelope_fields_are_ignored() -> None:
    a = event("run_closed")
    b = a.model_copy(update={"event_id": "otro", "seq": 7, "prev_hash": "a" * 64, "hash": "b" * 64,
                             "ts": a.ts.replace(year=2030)})
    assert first_divergence([a], [b]) is None


def test_release_and_run_are_not_ignored() -> None:
    a = event("run_closed")
    assert first_divergence([a], [a.model_copy(update={"release": "otra"})]) is not None


def test_reports_first_divergence_with_recorded_seq_and_values() -> None:
    from agent_core.audit.chain import chain_events

    raw = [event("run_started"), event("rule_evaluated", n=2), event("run_closed", n=3)]
    recorded = chain_events("run-0001", raw, None)
    flipped = recorded[1].payload.model_copy(update={"result": True})  # type: ignore[attr-defined]
    replayed = [recorded[0], recorded[1].model_copy(update={"payload": flipped}), recorded[2]]
    div = first_divergence(recorded, replayed)
    assert div is not None and div.event_seq == 1
    assert div.expected["payload"]["result"] is False and div.actual["payload"]["result"] is True  # type: ignore[index]


def test_length_mismatch_diverges_at_the_missing_or_extra_event() -> None:
    from agent_core.audit.chain import chain_events

    recorded = chain_events("run-0001", [event("run_started"), event("run_closed", n=2)], None)
    short = first_divergence(recorded, recorded[:1])
    assert short is not None and short.event_seq == 1 and short.actual is None
    extra = first_divergence(recorded[:1], recorded)
    assert extra is not None and extra.event_seq == 1 and extra.expected is None


def test_normalize_drops_only_declared_fields() -> None:
    n = normalize(event("tool_called"))
    assert "latency_ms" not in n["payload"] and "call_id" in n["payload"]  # type: ignore[operator]
    assert not ({"event_id", "seq", "prev_hash", "hash", "ts"} & set(n))
    assert set(MEASURED_FIELDS) >= {"tool_called"}
