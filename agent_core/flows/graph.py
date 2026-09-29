"""Grafo de un flow y consultas de alcanzabilidad (M1 §3.4, §3.5)."""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from agent_core.domain import (
    DECLARABLE,
    CollectNode,
    ConfirmNode,
    EndNode,
    Flow,
    Node,
    RespondNode,
    VerifyNode,
    WriteToolNode,
    is_declarable,
)

Edge = tuple[str, str]  # (nodo origen, resultado)


@dataclass(frozen=True)
class FlowGraph:
    nodes: Mapping[str, Node]  # primera aparición de cada id
    order: tuple[str, ...]
    entry: str | None  # None solo si el flow no tiene nodos (G0-01)
    # origen → ((resultado, destino), …); solo destinos existentes
    succ: Mapping[str, tuple[tuple[str, str], ...]]

    @classmethod
    def build(cls, flow: Flow) -> "FlowGraph":
        nodes: dict[str, Node] = {}
        for node in flow.nodes:
            nodes.setdefault(node.id, node)
        succ = {
            ident: tuple(sorted((result, dst) for result, dst in node.next.items() if dst in nodes))
            for ident, node in nodes.items()
        }
        entry = flow.nodes[0].id if flow.nodes else None
        return cls(nodes=nodes, order=tuple(nodes), entry=entry, succ=succ)

    def _next(self, ident: str, without: frozenset[Edge]) -> list[str]:
        return [dst for result, dst in self.succ.get(ident, ()) if (ident, result) not in without]

    def reachable(self, sources: Iterable[str], without: frozenset[Edge] = frozenset()) -> set[str]:
        """Nodos alcanzables por caminos de longitud ≥ 1 desde `sources`, sin las aristas de `without`."""
        seen: set[str] = set()
        queue: deque[str] = deque()
        for source in sources:
            queue.extend(self._next(source, without))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self._next(current, without))
        return seen

    def from_entry(self, without: frozenset[Edge] = frozenset()) -> set[str]:
        if self.entry is None:
            return set()
        return {self.entry} | self.reachable([self.entry], without)


def is_waiting(node: Node) -> bool:
    """Nodos que esperan al principal: collect, confirm y respond con await (M1 §3.4, G0-04)."""
    if isinstance(node, CollectNode | ConfirmNode):
        return True
    return isinstance(node, RespondNode) and node.config.await_


def writes_by_confirm(flow: Flow) -> dict[str, list[WriteToolNode]]:
    grouped: dict[str, list[WriteToolNode]] = {}
    for node in flow.nodes:
        if isinstance(node, WriteToolNode):
            grouped.setdefault(node.config.action_from, []).append(node)
    return grouped


def verify_of(graph: FlowGraph, write: WriteToolNode) -> VerifyNode | None:
    """El verify enlazado por estructura: `ok` y `uncertain` van directo al mismo verify (G0-05.7)."""
    target_id = write.next.get("ok")
    target = graph.nodes.get(target_id) if target_id is not None else None
    if isinstance(target, VerifyNode) and write.next.get("uncertain") == target_id:
        return target
    return None


def flow_mode(flow: Flow) -> str | None:
    """`task` o `conversational` si todos los `end` son declarables en él; None si no hay o se mezclan."""
    ends = [node for node in flow.nodes if isinstance(node, EndNode)]
    if not ends:
        return None
    outcomes = [e.config.outcome for e in ends]
    modes = sorted(m for m in DECLARABLE if all(is_declarable(o, m) for o in outcomes))
    return modes[0] if len(modes) == 1 else None
