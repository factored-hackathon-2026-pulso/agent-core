"""Contexto que recibe cada regla (M1 §3.4)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from agent_core.domain import DecisionModelDef, EntityKind, Flow, Prompt, RefSpec, Template, ToolDef
from agent_core.flows.graph import FlowGraph
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation


@dataclass(frozen=True)
class Ctx:
    flow: Flow
    reg: RegistryView
    graph: FlowGraph
    index: Mapping[str, int]  # id → posición de su primera aparición

    @classmethod
    def build(cls, flow: Flow, reg: RegistryView) -> "Ctx":
        index: dict[str, int] = {}
        for i, node in enumerate(flow.nodes):
            index.setdefault(node.id, i)
        return cls(flow=flow, reg=reg, graph=FlowGraph.build(flow), index=index)

    def v(self, rule: str, node_id: str | None, message: str, sub: str = "") -> Violation:
        if node_id is not None and node_id in self.index:
            path: str | None = f"/nodes/{self.index[node_id]}{sub}"
        else:
            path = sub or None
        return Violation(rule=rule, node_id=node_id, path=path, message=message)

    def tool(self, ref: RefSpec) -> ToolDef | None:
        entity = self.reg.resolve(EntityKind.tool, ref)
        return entity if isinstance(entity, ToolDef) else None

    def model(self, ref: RefSpec) -> DecisionModelDef | None:
        entity = self.reg.resolve(EntityKind.decision_model, ref)
        return entity if isinstance(entity, DecisionModelDef) else None

    def template(self, ref: RefSpec) -> Template | None:
        entity = self.reg.resolve(EntityKind.template, ref)
        return entity if isinstance(entity, Template) else None

    def prompt(self, ref: RefSpec) -> Prompt | None:
        entity = self.reg.resolve(EntityKind.prompt, ref)
        return entity if isinstance(entity, Prompt) else None


Rule = Callable[[Ctx], Iterable[Violation]]
