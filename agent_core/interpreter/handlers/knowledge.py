"""`knowledge` (M2 §3.3, M12 §3.1): delega en `KnowledgeService` y sigue por `ok`, `not_found` o `denied`.

M12 es el único que escribe `RunState.pages` y el único que emite `knowledge_read`; aquí solo se arma su
contexto con lo que ya tiene el `StepContext`. Leer páginas fijas no usa el modelo: el modo degradado no lo
impide."""

from agent_core.domain import IllegalTransition, KnowledgeNode, RunState
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.handlers.base import NodeResult
from agent_core.knowledge import KnowledgeContext


def handle_knowledge(node: KnowledgeNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    if ctx.knowledge is None:
        raise IllegalTransition(f"el nodo knowledge {node.id} necesita un KnowledgeService en el StepContext")
    context = KnowledgeContext(release=ctx.release, clock=ctx.clock, ids=ctx.ids, views=ctx.views,
                               vault=ctx.vault, turn_id=ctx.turn_id)
    new_state, result, events = ctx.knowledge.read(node, state, context)
    return NodeResult(new_state, result_key=result, events=events)
