"""G0-28: lo que un nodo `suggest` exige del flow que lo contiene (ADR 0026, m01 §3.13).

- Solo en flows de modo task: su salida es `RunResult.suggestions`, que una conversación no tiene.
- Un flow con `suggest` no escala ni transfiere (`escalate`, `transfer`): solo recomienda.
- Un flow con `suggest` no escribe: el copiloto recomienda y no actúa (ADR 0019 §7); un `confirm` o una
  escritura draft en el mismo flow haría que la lista pudiera afirmar algo que nadie verificó.
- La rama `suggested` va directo a un `end`: la lista vive solo en el resultado del turno que cierra el run
  (no en el estado), así que nada puede quedar entre el nodo y el cierre. (Un flow task tampoco tiene nodos
  que esperan, G0-16, así que hoy un run con `suggest` siempre cierra en un turno; esta cláusula lo fija.)
- `actions_allowed` son escrituras (nunca una lectura) y no repiten `tools_allowed`: el nodo no ejecuta
  ninguna, pero la lista de lo que puede *preparar* es aparte de la de lo que puede *recomendar leer*.
- Con `escalate`, toda ruta desde la entrada pasa por la rama `true` de una `rule`: el modelo no crea ni
  modifica la recomendación de escalar, la decide una política (ADR 0026 §2).
"""

from collections.abc import Iterator

from agent_core.domain import (
    ConfirmNode,
    EndNode,
    EscalateNode,
    RuleNode,
    SuggestNode,
    TransferNode,
    WriteToolNode,
)
from agent_core.flows.context import Ctx
from agent_core.flows.graph import flow_mode
from agent_core.flows.rules.phase5 import READ_CLASSES
from agent_core.flows.violations import Violation, clip


def g0_28(ctx: Ctx) -> Iterator[Violation]:
    suggests = [n for n in ctx.flow.nodes if isinstance(n, SuggestNode)]
    if not suggests:
        return
    first = suggests[0].id
    if flow_mode(ctx.flow) == "conversational":
        yield ctx.v("G0-28", first, "un nodo suggest solo va en un flow de modo task")
    for node in ctx.flow.nodes:
        if isinstance(node, ConfirmNode | WriteToolNode):
            yield ctx.v("G0-28", node.id, "un flow con nodo suggest no escribe: el copiloto solo recomienda")
    for node in ctx.flow.nodes:
        if isinstance(node, EscalateNode | TransferNode):
            yield ctx.v("G0-28", node.id, "un flow con nodo suggest no escala ni transfiere: solo recomienda")
    rule_true = frozenset((n.id, "true") for n in ctx.flow.nodes if isinstance(n, RuleNode))
    unguarded = ctx.graph.from_entry(rule_true)
    for node in suggests:
        target = node.next.get("suggested")
        reached = ctx.graph.nodes.get(target) if target is not None else None
        if reached is not None and not isinstance(reached, EndNode):
            yield ctx.v("G0-28", node.id, "la rama suggested va directo a un end: la lista solo viaja en el "
                        "turno que termina el run (no se guarda en el estado)", "/next/suggested")
        yield from _actions(ctx, node)
        if node.config.escalate is not None and node.id in unguarded:
            yield ctx.v("G0-28", node.id,
                        "escalate solo se alcanza por la rama true de una rule: el modelo no crea la "
                        "recomendación de escalar", "/config/escalate")


def _actions(ctx: Ctx, node: SuggestNode) -> Iterator[Violation]:
    readable = {tool.id for ref in node.config.tools_allowed if (tool := ctx.tool(ref)) is not None}
    for i, ref in enumerate(node.config.actions_allowed):
        tool = ctx.tool(ref)
        if tool is None:
            continue  # G0-02
        sub = f"/config/actions_allowed/{i}"
        if tool.risk_class in READ_CLASSES:
            yield ctx.v("G0-28", node.id, f"{clip(ref.id)} es una lectura: una action es una escritura", sub)
        elif tool.id in readable:
            yield ctx.v("G0-28", node.id, f"{clip(ref.id)} está en tools_allowed y en actions_allowed", sub)
