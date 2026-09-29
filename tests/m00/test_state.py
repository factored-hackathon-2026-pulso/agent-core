from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.identity import Principal
from agent_core.domain.json import dumps, loads
from agent_core.domain.outcomes import Outcome
from agent_core.domain.shared import Fingerprint, OutboxMessage, ToolStatus, TranscriptEntry
from agent_core.domain.state import Action, RunState
from agent_core.domain.turn import RunResult, TurnInput, TurnResult
from testing.builders import NOW, action, advisor_with_delegation, full_run_state, run_state


# T-M0-03
def test_run_state_round_trip_is_lossless() -> None:
    state = full_run_state()
    restored = RunState.model_validate(loads(dumps(state)))
    assert restored == state
    fact_value = restored.facts["monto_usd"].value
    assert isinstance(fact_value, dict)
    assert fact_value["amount"] == Decimal("120.50")
    assert isinstance(fact_value["amount"], Decimal)
    assert restored.actions[0].args["monto"] == Decimal("500.00")
    assert restored.budgets_used.run_cost == Decimal("0.0123")


# T-M0-09
def _invalid(**over: Any) -> None:
    with pytest.raises(ValidationError):
        RunState.model_validate(run_state().model_dump() | over)


def test_open_run_has_no_outcome_nor_closed_at() -> None:
    _invalid(outcome="resolved")
    _invalid(closed_at=NOW)


def test_closed_run_rules() -> None:
    closed = {"status": "closed", "outcome": "resolved", "closed_at": NOW, "inactive_after": None}
    RunState.model_validate(run_state().model_dump() | closed)
    _invalid(**(closed | {"inactive_after": NOW}))
    _invalid(**(closed | {"outcome": None}))
    _invalid(**(closed | {"outcome": "escalated"}))


def test_escalated_run_rules() -> None:
    escalated = {
        "status": "escalated",
        "outcome": "escalated",
        "closed_at": NOW,
        "inactive_after": None,
        "handoff_ref": "handoff-0001",
    }
    RunState.model_validate(run_state().model_dump() | escalated)
    _invalid(**(escalated | {"handoff_ref": None}))
    _invalid(**(escalated | {"outcome": "resolved"}))


def test_awaiting_needs_node_except_pending_offer() -> None:
    _invalid(awaiting="slot")
    RunState.model_validate(
        run_state().model_dump() | {"awaiting": "slot", "awaiting_node_id": "pedir_cargo"}
    )
    offer = {"awaiting": "input", "pending_offer": "bloquear-tarjeta", "active_flow": None}
    RunState.model_validate(run_state().model_dump() | offer)


def test_at_most_one_proposed_action_per_confirm() -> None:
    first = action(action_id="action-0001").model_dump()
    second = action(action_id="action-0002").model_dump()
    _invalid(actions=[first, second])


def test_task_run_has_no_session() -> None:
    _invalid(mode="task")  # run_state() trae session_id


# T-M0-10 (modelos de estado)
def test_naive_datetimes_rejected_in_state() -> None:
    naive = NOW.replace(tzinfo=None)
    _invalid(created_at=naive)
    with pytest.raises(ValidationError):
        action(created_at=naive)


def test_turn_input_needs_text_or_confirm() -> None:
    with pytest.raises(ValidationError):
        TurnInput(session_id="s", text="  ", channel="web", client_turn_id="c-1")
    TurnInput.model_validate(
        {
            "session_id": "s",
            "channel": "web",
            "client_turn_id": "c-1",
            "confirm": {"token": "t", "answer": "yes"},
        }
    )


def test_token_expiry_is_timezone_aware() -> None:
    assert action().token_exp - NOW == timedelta(minutes=5)


# Endurecimiento (fallar cerrado, sin secretos ni crudos en el estado)
def test_state_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RunState.model_validate(run_state().model_dump() | {"confirmation_token": "raw"})
    with pytest.raises(ValidationError):
        action(confirmation_token="raw")


def test_action_and_state_carry_no_raw_secret_fields() -> None:
    names = set(Action.model_fields) | set(RunState.model_fields) | set(Principal.model_fields)
    assert not {n for n in names if "password" in n or "secret" in n or n in {"token", "confirmation_token"}}
    assert "confirmation_token_hash" in Action.model_fields


def test_invalid_status_and_enums_rejected() -> None:
    _invalid(status="paused")
    _invalid(awaiting="maybe")
    _invalid(mode="batch")
    with pytest.raises(ValidationError):
        action(state="done")
    with pytest.raises(ValidationError):
        action(cancel_reason="whim")


def test_probability_and_counters_are_bounded_below() -> None:
    decision = {
        "decision_id": "d",
        "value": {},
        "p_cal": {"x": 1.5},
        "provider_used": "p",
        "model_version": "v",
    }
    _invalid(decisions={"d": decision})
    _invalid(decisions={"d": decision | {"p_cal": {"x": float("nan")}}})
    _invalid(state_version=-1)
    _invalid(turn_count=-1)
    _invalid(degraded_turns=[-1])
    _invalid(node_attempts={"n": -1})


def test_fingerprint_algorithm_is_fixed() -> None:
    Fingerprint(alg="HMAC-SHA256", kid="k", value="v")
    with pytest.raises(ValidationError):
        Fingerprint.model_validate({"alg": "SHA256", "kid": "k", "value": "v"})


def test_shared_types_forbid_extra_and_naive_dates() -> None:
    with pytest.raises(ValidationError):
        OutboxMessage.model_validate(
            {
                "message_id": "m",
                "type": "handoff_created",
                "run_id": "r",
                "payload": {},
                "created_at": NOW.replace(tzinfo=None),
            }
        )
    with pytest.raises(ValidationError):
        TranscriptEntry.model_validate({"run_id": "r", "turn_id": "t", "role": "system", "text_model": "x"})


def test_tool_status_members_match_spec() -> None:
    assert {s.value for s in ToolStatus} == {
        "ok",
        "error",
        "timeout",
        "denied",
        "uncertain",
        "step_up_required",
    }


def test_turn_result_and_run_result_shapes() -> None:
    message = {"kind": "template", "text": "hola", "locale": "es"}
    result = TurnResult.model_validate(
        {
            "run_id": "r",
            "turn_id": "t",
            "messages": [message],
            "locale": "es",
            "awaiting": "none",
            "status": "open",
            "trace_id": "tr",
        }
    )
    RunResult.model_validate(
        {"run_id": "r", "release": "rel", "status": "open", "trace_id": "tr", "first_turn": result}
    )
    with pytest.raises(ValidationError):
        TurnResult.model_validate(result.model_dump() | {"status": "paused"})
    with pytest.raises(ValidationError):
        TurnResult.model_validate(result.model_dump() | {"locale": "ES"})


def test_builders_are_synthetic_and_valid() -> None:
    advisor, obo = advisor_with_delegation()
    assert obo.grantee == advisor.key
    assert full_run_state().principal.type.value == "advisor"


# model_copy(update=...) es la vía documentada (índice §5): debe revalidar la coherencia
def test_model_copy_update_revalidates_coherence() -> None:
    state = run_state()
    with pytest.raises(ValidationError):
        state.model_copy(update={"outcome": Outcome.resolved})
    with pytest.raises(ValidationError):
        state.model_copy(update={"status": "escalated"})
    with pytest.raises(ValueError, match="desconocidos"):
        state.model_copy(update={"unknown_field": 1})
    assert state.outcome is None
    assert state.status == "open"


def test_model_copy_update_coherent_and_original_unchanged() -> None:
    state = run_state()
    closed = state.model_copy(
        update={"status": "closed", "outcome": Outcome.resolved, "closed_at": NOW, "inactive_after": None}
    )
    assert closed.status == "closed"
    assert closed.outcome is Outcome.resolved
    assert state.status == "open"
    assert state.outcome is None
    assert state.model_copy() == state
    bumped = state.model_copy(update={"state_version": 1})
    assert bumped.state_version == 1
    assert state.state_version == 0


def test_run_state_canonical_hash_survives_persist_reload() -> None:
    from agent_core.domain.json import canonical_bytes

    state = full_run_state()
    state = state.model_copy(
        update={"facts": {**state.facts, "n": {"fact_id": "n", "value": {"cantidad": Decimal("500")},
                                                "source": {"kind": "compute", "ref": "x@1"}, "ts": NOW}}}
    )
    restored = RunState.model_validate(loads(dumps(state)))
    assert canonical_bytes(state) == canonical_bytes(restored)
