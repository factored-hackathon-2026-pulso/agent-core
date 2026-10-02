"""G0-26 and G0-27: where a transfer gets its target, its directory and its packet (ADR 0021, spec §6)."""

from collections.abc import Iterator

from agent_core.domain import CollectNode, DecideNode, ToolNode, TransferNode
from agent_core.flows.context import Ctx
from agent_core.flows.graph import Edge
from agent_core.flows.violations import Violation, clip

DIRECTORY_TOOL_ID = "directory/list"


def _dominates(ctx: Ctx, cut: frozenset[Edge], target: str) -> bool:
    """No path from the entry reaches `target` without crossing one of the `cut` edges."""
    return target not in ctx.graph.from_entry(cut)


def g0_26(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if not isinstance(node, TransferNode):
            continue
        decide_name = node.config.target_from.split(".")[1]
        deciders = [n.id for n in ctx.flow.nodes if isinstance(n, DecideNode)
                    and n.config.save_as == decide_name and n.config.choices_from is not None]
        # Only `chosen` yields a `choice`: `none` and `low_confidence` must not reach the transfer.
        chosen = frozenset((d, "chosen") for d in deciders)
        if not deciders or not _dominates(ctx, chosen, node.id):
            yield ctx.v("G0-26", node.id,
                        f"target_from {clip(node.config.target_from)} no sale de un decide con "
                        "choices_from que domine al transfer", "/config/target_from")
        tools = [n.id for n in ctx.flow.nodes if isinstance(n, ToolNode)
                 and n.config.save_as == node.config.directory_from and n.config.tool.id == DIRECTORY_TOOL_ID]
        every_edge = frozenset((t, result) for t in tools for result in ctx.graph.nodes[t].next)
        if not tools or not _dominates(ctx, every_edge, node.id):
            yield ctx.v("G0-26", node.id,
                        f"directory_from {clip(node.config.directory_from)!r} no es el hecho de "
                        f"un nodo tool {DIRECTORY_TOOL_ID} que domine al transfer", "/config/directory_from")


def g0_27(ctx: Ctx) -> Iterator[Violation]:
    collected = {n.config.slot for n in ctx.flow.nodes if isinstance(n, CollectNode)}
    for node in ctx.flow.nodes:
        if not isinstance(node, TransferNode):
            continue
        for i, slot in enumerate(node.config.packet.slots):
            if slot not in collected:
                yield ctx.v("G0-27", node.id,
                            f"el slot {clip(slot)!r} del paquete no lo recolecta ningún collect",
                            f"/config/packet/slots/{i}")
