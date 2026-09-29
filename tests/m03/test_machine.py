"""T-M3-10: la tabla de transiciones es dato (M3 §3.1) y cualquier otra transición lanza IllegalTransition."""

import itertools

import pytest

from agent_core.actions.machine import TRANSITIONS, VERIFIABLE, Trigger, next_state
from agent_core.domain import ActionState, IllegalTransition

S = ActionState
EXPECTED = {
    (S.proposed, Trigger.confirm): S.confirmed,
    (S.proposed, Trigger.cancel): S.cancelled,
    (S.confirmed, Trigger.dispatch): S.executing,
    (S.confirmed, Trigger.cancel): S.cancelled,
    (S.executing, Trigger.tool_ok): S.executed,
    (S.executing, Trigger.tool_uncertain): S.uncertain,
    (S.executing, Trigger.tool_denied): S.denied,
    (S.executing, Trigger.tool_step_up): S.confirmed,
    (S.executing, Trigger.verified): S.verified,
    (S.executing, Trigger.failed): S.failed,
    (S.executed, Trigger.verified): S.verified,
    (S.executed, Trigger.failed): S.failed,
    (S.uncertain, Trigger.verified): S.verified,
    (S.uncertain, Trigger.failed): S.failed,
}
ILLEGAL = [pair for pair in itertools.product(ActionState, Trigger) if pair not in EXPECTED]


def test_table_matches_spec() -> None:
    assert dict(TRANSITIONS) == EXPECTED


@pytest.mark.parametrize(("current", "trigger"), sorted(EXPECTED, key=str))
def test_allowed_transitions(current: ActionState, trigger: Trigger) -> None:
    assert next_state(current, trigger) is EXPECTED[(current, trigger)]


@pytest.mark.parametrize(("current", "trigger"), ILLEGAL)
def test_t_m3_10_illegal_transition_raises(current: ActionState, trigger: Trigger) -> None:
    with pytest.raises(IllegalTransition):
        next_state(current, trigger)


def test_terminal_states_have_no_exit() -> None:
    for terminal in (S.verified, S.failed, S.cancelled, S.denied):
        assert not [pair for pair in TRANSITIONS if pair[0] is terminal]


def test_denied_is_not_verifiable() -> None:
    assert frozenset({S.executed, S.uncertain, S.executing}) == VERIFIABLE


def test_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        TRANSITIONS[(S.proposed, Trigger.dispatch)] = S.executing  # type: ignore[index]
