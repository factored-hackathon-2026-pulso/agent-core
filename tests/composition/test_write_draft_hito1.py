"""Hito 1 de la spec write-draft: un flow con escrituras `draft` crea una propuesta y escribe un borrador de
punta a punta (motor real de M2 + M3, `BuilderToolExecutor` real, registry en memoria), sin `confirm`."""

from typing import Any

from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
from agent_core.domain import ActionState
from agent_core.interpreter import Stop
from testing.builders import principal
from tests.m02.harness import World as EngineWorld
from tests.m02.harness import flow
from tests.registry.helpers import bot, prompt_draft
from tests.registry.service_world import World as RegistryWorld

NODES: list[dict[str, Any]] = [
    {"id": "crear", "type": "tool",
     "config": {"draft": True, "tool": "registry/create_proposal@1.0.0",
                "args": {"agent_id": "atencion", "origin": "builder_chat", "title": "mejorar radicado"},
                "save_as": "propuesta"},
     "next": {"ok": "verificar_crear", "uncertain": "verificar_crear", "denied": "esc"}},
    {"id": "verificar_crear", "type": "verify",
     "config": {"readback": "registry/get_write@1.0.0", "by": "idempotency_key",
                "predicate": {"==": [{"var": "readback.op"}, "create_proposal"]}, "save_as": "crear_ok"},
     "next": {"verified": "guardar", "failed": "esc"}},
    {"id": "guardar", "type": "tool",
     "config": {"draft": True, "tool": "registry/put_draft@1.0.0",
                "args": {"proposal_id": "facts.propuesta.value.proposal_id",
                         "expected_rev": "facts.propuesta.value.rev",
                         "changes": [prompt_draft().model_dump(mode="json")]},
                "save_as": "borrador"},
     "next": {"ok": "verificar_guardar", "uncertain": "verificar_guardar", "denied": "esc"}},
    {"id": "verificar_guardar", "type": "verify",
     "config": {"readback": "registry/get_write@1.0.0", "by": "idempotency_key",
                "predicate": {"==": [{"var": "readback.op"}, "put_draft"]}, "save_as": "guardar_ok"},
     "next": {"verified": "fin", "failed": "esc"}},
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def test_a_draft_flow_creates_a_proposal_and_writes_a_draft_end_to_end() -> None:
    registry = RegistryWorld()
    engine = EngineWorld()
    engine.add(*BUILDER_TOOL_DEFS.values())
    executor = BuilderToolExecutor(registry.service, bot(), engine.ids)
    supervisor = principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                           attrs={"actor": "human"})
    f = flow(*NODES)
    done = engine.step(engine.persist(engine.state(f, principal=supervisor)), tools=executor)

    assert done.stop is Stop.terminal and done.end_outcome is not None
    assert [a.state for a in done.state.actions] == [ActionState.verified, ActionState.verified]
    assert all(a.confirm_node_id is None and a.write_node_id for a in done.state.actions)  # sin confirm
    created = done.state.facts["propuesta"].value
    assert isinstance(created, dict)
    detail = registry.service.get_proposal(str(created["proposal_id"]))
    assert [(c.kind, c.id) for c in detail.changes] == [("prompt", "p/resumen_radicado")]
    assert (detail.proposal.rev, detail.proposal.origin.value) == (1, "builder_chat")
    persisted = [e.type for e in engine.store.events["run-0001"]]
    assert persisted.count("action_dispatched") == 2 and "action_confirmed" not in persisted
    second = done.state.actions[1]
    record = registry.service.get_write(second.idempotency_key)  # la clave es el action_id
    assert record is not None
    assert (record.op, record.run_id, record.on_behalf_of) == ("put_draft", "run-0001", "builder:ana")
