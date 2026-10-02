import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.outcomes import Awaiting, Command, Outcome, ReasonCode, ReasonCodeStr, is_declarable

REASON = TypeAdapter(ReasonCodeStr)


# T-M0-02
@pytest.mark.parametrize("mode", ["conversational", "task"])
@pytest.mark.parametrize("outcome", [Outcome.abandoned, Outcome.escalated])
def test_engine_only_outcomes_never_declarable(mode: str, outcome: Outcome) -> None:
    assert not is_declarable(outcome, mode)


def test_declarable_by_mode() -> None:
    for outcome in (Outcome.resolved, Outcome.abstained, Outcome.cancelled, Outcome.clarify_exhausted):
        assert is_declarable(outcome, "conversational")
        assert not is_declarable(outcome, "task")
    for outcome in (Outcome.completed, Outcome.failed):
        assert is_declarable(outcome, "task")
        assert not is_declarable(outcome, "conversational")


def test_unknown_mode_declares_nothing() -> None:
    assert not is_declarable(Outcome.resolved, "batch")


def test_enum_members_match_spec() -> None:
    assert {o.value for o in Outcome} == {
        "resolved", "abstained", "cancelled", "clarify_exhausted",
        "completed", "failed", "abandoned", "escalated", "transferred",
    }  # fmt: skip
    assert {r.value for r in ReasonCode} == {
        "low_confidence", "budget_exceeded", "tool_failure", "customer_request",
        "verification_failed", "validation_failed", "release_revoked", "auth_insufficient",
    }  # fmt: skip
    assert {a.value for a in Awaiting} == {"none", "slot", "confirmation", "step_up", "input"}
    assert {c.value for c in Command} == {
        "start_flow", "continue", "affirm", "deny", "clarify",
        "cancel", "handoff", "out_of_scope", "interrupt",
    }  # fmt: skip


# T-M0-13
@pytest.mark.parametrize(
    "code",
    [
        "low_confidence",
        "release_revoked",
        "rule:umbral",
        "policy:escalamiento-disputa-monto",
        "interrupt:fraude",
    ],
)
def test_reason_code_accepts(code: str) -> None:
    assert REASON.validate_python(code) == code


@pytest.mark.parametrize(
    "code",
    [
        "",
        "whatever",
        "rule:",
        "policy:Mayus",
        "note:x",
        "interrupt: fraude",
        "rule:x\n",  # `$` de Python admite un salto de línea final; fullmatch no
        "low_confidence\n",
        "rule:x\ny",
        "rule:año",
        "rule:x٣",  # dígito no ASCII
    ],
)
def test_reason_code_rejects(code: str) -> None:
    with pytest.raises(ValidationError):
        REASON.validate_python(code)


def test_command_continue_value() -> None:
    assert Command("continue") is Command.continue_
