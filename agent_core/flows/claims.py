"""Reclamos de éxito declarados y derivados (M1 §3.6). M2 usa esta misma función en runtime."""

from collections import deque
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from agent_core.domain import (
    ConfirmNode,
    DecideNode,
    EndNode,
    EntityKind,
    Flow,
    Node,
    Prompt,
    RefSpec,
    RespondNode,
    Template,
    ToolNode,
    VerifyNode,
    WriteToolNode,
)
from agent_core.flows.graph import FlowGraph, verify_of, writes_by_confirm
from agent_core.flows.paths import Path, parse_path, value_paths
from agent_core.flows.view import RegistryView

Name = tuple[str, str]  # ("facts" | "decisions", nombre)
Propagation = Mapping[Name, tuple[Name, ...]]  # entrada → salidas de los productores que la leen


def _names(paths: Iterable[Path]) -> set[Name]:
    return {(p.ns, p.name) for p in paths if p.ns in ("facts", "decisions") and p.name is not None}


def _parsed(texts: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for text in texts:
        try:
            path = parse_path(text)
        except ValueError:
            continue
        if path is not None:
            paths.append(path)
    return paths


def _output(node: Node) -> Name | None:
    if isinstance(node, ToolNode | WriteToolNode | VerifyNode):
        return ("facts", node.config.save_as)
    if isinstance(node, DecideNode):
        return ("decisions", node.config.save_as)
    return None


def _inputs(node: Node) -> set[Name]:
    if isinstance(node, ToolNode):
        return _names(value_paths(dict(node.config.args), strict=False))
    if isinstance(node, VerifyNode) and node.config.by.startswith("fact:"):
        return _names(_parsed([node.config.by.removeprefix("fact:")]))
    if isinstance(node, DecideNode):
        return _names(_parsed(node.config.input_view or []))
    return set()


def _propagation(flow: Flow) -> Propagation:
    """Grafo de productores, una sola vez por flow: entrada → salidas que dependen de ella."""
    edges: dict[Name, set[Name]] = {}
    for node in flow.nodes:
        out = _output(node)
        if out is None:
            continue
        for name in _inputs(node):
            edges.setdefault(name, set()).add(out)
    return {name: tuple(sorted(outs)) for name, outs in edges.items()}


def _origin(seeds: Iterable[Name], propagation: Propagation) -> frozenset[Name]:
    """Cierre transitivo de `seeds` por el grafo de productores (worklist, O(V+E) por confirm)."""
    seen: set[Name] = set(seeds)
    queue: deque[Name] = deque(sorted(seen))
    while queue:
        for out in propagation.get(queue.popleft(), ()):
            if out not in seen:
                seen.add(out)
                queue.append(out)
    return frozenset(seen)


def _seeds(graph: FlowGraph, writes: list[WriteToolNode]) -> set[Name]:
    seeds: set[Name] = set()
    for write in writes:
        seeds.add(("facts", write.config.save_as))
        verify = verify_of(graph, write)
        if verify is not None:
            seeds.add(("facts", verify.config.save_as))
    return seeds


def _template_reads(reg: RegistryView, ref: object) -> list[str]:
    if not isinstance(ref, RefSpec):
        return []
    entity = reg.resolve(EntityKind.template, ref)
    return sorted(entity.reads) if isinstance(entity, Template) else []


def _reads(node: Node, reg: RegistryView) -> set[Name] | None:
    """Lo que lee un lector, o None si el nodo no es lector."""
    if isinstance(node, RespondNode):
        texts = _template_reads(reg, node.config.template_ref)
        generate = node.config.generate
        if generate is not None:
            texts += generate.allowed_facts
            texts += _template_reads(reg, generate.fallback_template_ref)
            prompt = reg.resolve(EntityKind.prompt, generate.prompt_ref)
            if isinstance(prompt, Prompt):
                texts += sorted(prompt.reads)
        return _names(_parsed(texts))
    if isinstance(node, EndNode) and node.config.output_map:
        return _names(_parsed(node.config.output_map.values()))
    return None


def derive_claims(flow: Flow, reg: RegistryView) -> Mapping[str, frozenset[str]]:
    """Lector (respond, end con output_map) → ids de confirm cuyo éxito afirma. Conservador y total."""
    graph = FlowGraph.build(flow)
    writes = writes_by_confirm(flow)
    propagation = _propagation(flow)
    origins: dict[str, frozenset[Name]] = {}
    for node in flow.nodes:
        if isinstance(node, ConfirmNode) and node.id not in origins:
            origins[node.id] = _origin(_seeds(graph, writes.get(node.id, [])), propagation)
    result: dict[str, frozenset[str]] = {}
    for node in flow.nodes:
        if node.id in result:
            continue
        reads = _reads(node, reg)
        if reads is None:
            continue
        declared = set(node.config.claims) if isinstance(node, RespondNode) else set()
        derived = {confirm for confirm, names in origins.items() if not names.isdisjoint(reads)}
        result[node.id] = frozenset(declared | derived)
    return MappingProxyType(result)
