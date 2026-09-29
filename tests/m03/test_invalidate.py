"""T-M3-08: `invalidate` cancela `proposed`/`confirmed` y no toca `executing` ni posteriores (M3 §3.6)."""

from agent_core.domain import ActionState, InvalidationReason
from testing.builders import action
from tests.m03.harness import World, base_state


def _mixed_state():  # type: ignore[no-untyped-def]
    return base_state(actions=[
        action(action_id="action-a", idempotency_key="action-a", confirm_node_id="c_a", state="proposed"),
        action(action_id="action-b", idempotency_key="action-b", confirm_node_id="c_b", state="confirmed"),
        action(action_id="action-c", idempotency_key="action-c", confirm_node_id="c_c", state="executing"),
        action(action_id="action-d", idempotency_key="action-d", confirm_node_id="c_d", state="verified"),
    ])


def test_t_m3_08_invalidate_cancels_only_pending_actions() -> None:
    w = World()
    state, events = w.manager.invalidate(_mixed_state(), InvalidationReason.interrupt, turn_id="turn-0009")
    by_id = {a.action_id: a for a in state.actions}
    for cancelled in ("action-a", "action-b"):
        assert (by_id[cancelled].state, by_id[cancelled].cancel_reason) == (
            ActionState.cancelled, InvalidationReason.interrupt)
    assert (by_id["action-c"].state, by_id["action-c"].cancel_reason) == (ActionState.executing, None)
    assert by_id["action-d"].state is ActionState.verified
    assert [(e.payload.action_id, e.payload.reason, e.turn_id) for e in events] == [
        ("action-a", InvalidationReason.interrupt, "turn-0009"),
        ("action-b", InvalidationReason.interrupt, "turn-0009"),
    ]


def test_invalidate_is_idempotent() -> None:
    w = World()
    state, _ = w.manager.invalidate(_mixed_state(), InvalidationReason.cancel)
    again, events = w.manager.invalidate(state, InvalidationReason.cancel)
    assert (again.actions, events) == (state.actions, [])
