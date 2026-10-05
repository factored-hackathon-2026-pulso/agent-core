"""G0-29: `capture_start` solo en los `collect` con los que arranca el flow (M1 §3.4, M2 D16).

Total: nunca lanza. El texto que arranca el flow se ofrece al `collect` de entrada y, mientras cada uno lo
acepte por `ok`, al siguiente `collect` con `capture_start`; en cualquier otro lugar la marca no haría nada y
se rechaza para que no engañe a quien escribe el flow."""

from collections.abc import Iterator

from agent_core.domain import CollectNode
from agent_core.flows.context import Ctx
from agent_core.flows.violations import Violation

RULE = "G0-29"


def g0_29(ctx: Ctx) -> Iterator[Violation]:
    nodes = {n.id: n for n in ctx.flow.nodes}
    chain: set[str] = set()
    current = ctx.flow.nodes[0] if ctx.flow.nodes else None
    while isinstance(current, CollectNode) and current.config.capture_start and current.id not in chain:
        chain.add(current.id)
        nxt = current.next.get("ok")
        current = nodes.get(nxt) if nxt is not None else None
    for node in ctx.flow.nodes:
        if isinstance(node, CollectNode) and node.config.capture_start and node.id not in chain:
            yield ctx.v(RULE, node.id,
                        "capture_start solo vale en el collect de entrada o en los que lo siguen por ok",
                        "/config/capture_start")
