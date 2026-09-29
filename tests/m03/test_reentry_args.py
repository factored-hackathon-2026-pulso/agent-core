"""Reentrada con token vigente y `resolved_args` distintos (M3 §11 Abierto (a)).

Fija el comportamiento ACTUAL: `propose` no compara args; devuelve la misma acción sin tocar los args
congelados. Si se decide cancelar y congelar una nueva (`args_changed`), esta prueba debe cambiar.
"""

from agent_core.domain import canonical_bytes, sha256_hex
from tests.m03.harness import ARGS, CONFIRM, WRITE, World, persisted_proposal


def test_reentry_with_live_token_ignores_changed_args_today() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    changed = {**ARGS, "__cambiado__": "otro"}
    assert changed != ARGS
    state2, prompt2, _ = w.manager.propose(state, CONFIRM, changed, WRITE, w.ctx())
    assert prompt2.action_id == prompt.action_id
    [action] = state2.actions
    assert action.args == ARGS
    assert action.args_hash == sha256_hex(canonical_bytes(ARGS))
