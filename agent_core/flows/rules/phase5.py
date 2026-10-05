"""Reglas de la fase 5: G0-07, G0-08, G0-10, G0-11, G0-13, G0-14, G0-15 y G0-16 (M1 §3.4, §3.9).

Todas son totales: nunca lanzan. Una referencia que no resuelve, un destino inexistente o un id duplicado no
se reportan aquí cuando ya los reporta G0-02 o G0-03. Cada regla recorre el flow una sola vez (el registro
se consulta a lo sumo una vez por nodo y sitio), sin recorridos de grafo por nodo.

`expr_paths` y `expr_literals` dejan de descender en `MAX_DEPTH`. Si una expresión excede ese límite, las
reglas que las usan (G0-08, G0-10) reportan una violación en vez de pasar por alto lo que no vieron.
"""

from collections.abc import Iterable, Iterator

from agent_core.domain import (
    DECLARABLE,
    AgentNode,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EndNode,
    EntityKind,
    EscalateNode,
    JsonValue,
    ModelProfile,
    RefSpec,
    RespondNode,
    RiskClass,
    RuleNode,
    StructuredMode,
    ToolNode,
    VerifyNode,
    WriteToolNode,
    is_declarable,
    unsupported_keyword,
)
from agent_core.flows.context import Ctx
from agent_core.flows.draft_schema import DRAFT_OUTPUT_SCHEMA
from agent_core.flows.graph import draft_writes, end_modes, flow_mode, is_waiting
from agent_core.flows.jsonlogic import MAX_DEPTH, exceeds_max_depth, expr_literals, expr_paths
from agent_core.flows.paths import Path, parse_path, value_paths
from agent_core.flows.violations import Violation, clip

READ_CLASSES = frozenset({RiskClass.read, RiskClass.compute})
SLOTS_FACTS = frozenset({"slots", "facts"})


def _too_deep(expr: JsonValue) -> bool:
    """True si la expresión excede `MAX_DEPTH`: `expr_paths` y `expr_literals` no verían todo."""
    return exceeds_max_depth(expr)


DEEP_MESSAGE = f"la expresión excede la profundidad máxima ({MAX_DEPTH}): no se puede comprobar completa"


def g0_07(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, AgentNode):
            for i, ref in enumerate(node.config.tools_allowed):
                tool = ctx.tool(ref)
                if tool is not None and tool.risk_class not in READ_CLASSES:
                    yield ctx.v(
                        "G0-07",
                        node.id,
                        f"el nodo agent solo usa tools read o compute; {clip(ref.id)} no lo es",
                        f"/config/tools_allowed/{i}",
                    )


def g0_08(ctx: Ctx) -> Iterator[Violation]:
    enums: set[str] = set()
    for node in ctx.flow.nodes:
        if isinstance(node, CollectNode) and node.config.validator is not None:
            value = node.config.validator.value
            if node.config.validator.kind == "enum" and isinstance(value, list):
                enums |= {v for v in value if isinstance(v, str)}
    for node in ctx.flow.nodes:
        if not isinstance(node, RuleNode) or node.config.expr is None:
            continue
        if _too_deep(node.config.expr):
            yield ctx.v("G0-08", node.id, DEEP_MESSAGE, "/config/expr")
            continue
        for where, literal in expr_literals(node.config.expr):
            if (
                literal is None
                or isinstance(literal, bool)
                or (isinstance(literal, str) and literal in enums)
            ):
                continue
            yield ctx.v(
                "G0-08",
                node.id,
                f"literal de negocio {clip(repr(literal))}: usa una policy protegida (ADR 0009)",
                f"/config/expr{clip(where, 160)}",
            )


def _parsed(texts: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for text in texts:
        try:
            path = parse_path(text)
        except ValueError:
            continue  # G0-01
        if path is not None:
            paths.append(path)
    return paths


def _template_paths(ctx: Ctx, ref: RefSpec | None) -> list[Path]:
    if ref is None:
        return []
    template = ctx.template(ref)
    return _parsed(sorted(template.reads)) if template is not None else []


class _Site:
    """Un lugar donde el flow lee rutas (tabla M1 §3.2)."""

    __slots__ = ("allowed", "paths", "problem", "sub", "whole_ok")

    def __init__(
        self,
        sub: str,
        paths: list[Path],
        allowed: frozenset[str],
        whole_ok: bool = False,
        problem: bool = False,
    ) -> None:
        self.sub = sub
        self.paths = paths
        self.allowed = allowed
        self.whole_ok = whole_ok
        self.problem = problem


def _expr_site(sub: str, expr: JsonValue, allowed: frozenset[str]) -> _Site:
    return _Site(sub, expr_paths(expr), allowed, problem=_too_deep(expr))


def _read_sites(ctx: Ctx, node: object) -> Iterator[_Site]:
    if isinstance(node, DecideNode) and node.config.choices_from is not None:
        yield _Site("/config/choices_from", _parsed([node.config.choices_from]), frozenset({"facts"}))
    if isinstance(node, ToolNode):
        tool = ctx.tool(node.config.tool)
        if tool is None:
            return  # G0-02
        allowed = SLOTS_FACTS | {"decisions"} if tool.risk_class == RiskClass.compute else SLOTS_FACTS
        yield _Site("/config/args", value_paths(dict(node.config.args), strict=False), allowed)
    elif isinstance(node, WriteToolNode) and node.config.draft:
        yield _Site("/config/args", value_paths(dict(node.config.args), strict=False), SLOTS_FACTS)
    elif isinstance(node, ConfirmNode):
        yield _Site(
            "/config/action/args", value_paths(dict(node.config.action.args), strict=False), SLOTS_FACTS
        )
        yield _Site(
            "/config/summary_template", _template_paths(ctx, node.config.summary_template), SLOTS_FACTS
        )
        yield _Site(
            "/config/reprompt_template", _template_paths(ctx, node.config.reprompt_template), SLOTS_FACTS
        )
    elif isinstance(node, RuleNode) and node.config.expr is not None:
        yield _expr_site("/config/expr", node.config.expr, SLOTS_FACTS)
    elif isinstance(node, VerifyNode):
        yield _expr_site("/config/predicate", node.config.predicate, SLOTS_FACTS | {"readback"})
        if node.config.by.startswith("fact:"):
            yield _Site("/config/by", _parsed([node.config.by.removeprefix("fact:")]), frozenset({"facts"}))
    elif isinstance(node, EscalateNode) and node.config.priority_expr is not None:
        yield _expr_site("/config/priority_expr", node.config.priority_expr, SLOTS_FACTS)
    elif isinstance(node, EndNode) and node.config.output_map:
        yield _Site(
            "/config/output_map", _parsed(v for _, v in sorted(node.config.output_map.items())), SLOTS_FACTS
        )
    elif isinstance(node, DecideNode | AgentNode) and node.config.input_view:
        yield _Site("/config/input_view", _parsed(node.config.input_view), SLOTS_FACTS)
    elif isinstance(node, CollectNode):
        yield _Site("/config/prompt_ref", _template_paths(ctx, node.config.prompt_ref), SLOTS_FACTS)
    elif isinstance(node, RespondNode):
        yield _Site("/config/template_ref", _template_paths(ctx, node.config.template_ref), SLOTS_FACTS)
        generate = node.config.generate
        if generate is not None:
            yield _Site(
                "/config/generate/allowed_facts", _parsed(generate.allowed_facts), frozenset({"facts"}), True
            )
            yield _Site(
                "/config/generate/fallback_template_ref",
                _template_paths(ctx, generate.fallback_template_ref),
                SLOTS_FACTS,
            )


def slot_reads(ctx: Ctx) -> dict[str, list[str]]:
    """Slots que el flow lee (nombre -> ids de nodo que los leen)."""
    found: dict[str, list[str]] = {}
    for node in ctx.flow.nodes:
        for site in _read_sites(ctx, node):
            for path in site.paths:
                if path.ns == "slots" and path.name is not None:
                    found.setdefault(path.name, []).append(node.id)
    return found


def g0_10(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        for site in _read_sites(ctx, node):
            if site.problem:
                yield ctx.v("G0-10", node.id, DEEP_MESSAGE, site.sub)
            for path in site.paths:
                if path.ns not in site.allowed:
                    yield ctx.v("G0-10", node.id, f"{clip(path.raw)} no se puede leer aquí", site.sub)
                elif path.whole_fact and not site.whole_ok:
                    yield ctx.v("G0-10", node.id, f"{clip(path.raw)}: falta .value", site.sub)


def g0_11(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, DecideNode):
            model = ctx.model(node.config.model)
            if model is None:
                continue  # G0-02
            properties = model.output_schema.get("properties")
            if node.config.choices_from is None and (
                not isinstance(properties, dict) or node.config.branch_on not in properties
            ):
                continue  # G0-03(e) ya reporta un branch_on que no es propiedad del esquema
            if node.config.branch_on not in model.calibrated_fields:
                yield ctx.v(
                    "G0-11",
                    node.id,
                    f"branch_on {clip(repr(node.config.branch_on))} no está en calibrated_fields",
                    "/config/branch_on",
                )


def g0_13(ctx: Ctx) -> Iterator[Violation]:
    actions = {n.id for n in ctx.flow.nodes if isinstance(n, ConfirmNode)} | {
        w.id for w in draft_writes(ctx.flow)}  # una escritura draft no tiene confirm: su acción es el nodo
    for node in ctx.flow.nodes:
        if isinstance(node, RespondNode):
            for i, claim in enumerate(node.config.claims):
                if claim not in actions:
                    yield ctx.v(
                        "G0-13",
                        node.id,
                        f"claims lista {clip(repr(claim))}, que no es un confirm ni una escritura draft",
                        f"/config/claims/{i}",
                    )


def g0_14(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, EndNode) and not any(is_declarable(node.config.outcome, m) for m in DECLARABLE):
            yield ctx.v(
                "G0-14",
                node.id,
                f"el outcome {node.config.outcome.value} no es declarable: lo asigna el motor",
                "/config/outcome",
            )
    if len(end_modes(ctx.flow)) > 1:
        yield ctx.v("G0-14", None, "los end del flow mezclan outcomes de modo conversacional y de modo task")


def _prompt_sites(node: object) -> Iterator[tuple[RefSpec, str]]:
    """Los prompts que un nodo envía a un modelo: `respond.generate` y `agent`."""
    if isinstance(node, RespondNode) and node.config.generate is not None:
        yield node.config.generate.prompt_ref, "/config/generate/prompt_ref"
    elif isinstance(node, AgentNode):
        yield node.config.prompt_ref, "/config/prompt_ref"


def g0_15(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        for ref, sub in _prompt_sites(node):
            prompt = ctx.prompt(ref)
            if prompt is not None and ctx.reg.resolve(EntityKind.model_profile, prompt.model_profile) is None:
                yield ctx.v(
                    "G0-15",
                    node.id,
                    f"el prompt {clip(prompt.id)} referencia el model_profile "
                    f"{clip(prompt.model_profile.id)}, que no existe",
                    sub,
                )


def g0_25(ctx: Ctx) -> Iterator[Violation]:
    """El prompt de un nodo `agent` va en modo `prompted`: el paso del agente tiene propiedades opcionales y
    el modo `native` estricto de OpenAI lo rechaza (gateway §3.8)."""
    for node in ctx.flow.nodes:
        if not isinstance(node, AgentNode):
            continue
        prompt = ctx.prompt(node.config.prompt_ref)
        if prompt is None:
            continue
        profile = ctx.reg.resolve(EntityKind.model_profile, prompt.model_profile)
        if isinstance(profile, ModelProfile) and profile.structured is not StructuredMode.prompted:
            yield ctx.v(
                "G0-25",
                node.id,
                f"el model_profile {clip(prompt.model_profile.id)} del prompt del agente debe ser "
                "structured: prompted",
                "/config/prompt_ref",
            )


def g0_16(ctx: Ctx) -> Iterator[Violation]:
    if flow_mode(ctx.flow) != "task":
        return
    for node in ctx.flow.nodes:
        if is_waiting(node):
            yield ctx.v("G0-16", node.id, "un flow de modo task no tiene nodos que esperan al principal")


# The only places where an `agent` output may be read: what is shown to the person, or what feeds a model
# (the `input_view` of a `decide`, which validates its own output, or of another `agent`, whose output falls
# under this same rule). None of them decides or writes (ADR 0019).
_AGENT_OUTPUT_SITES = frozenset({
    "/config/template_ref",
    "/config/generate/allowed_facts",
    "/config/generate/fallback_template_ref",
    "/config/input_view",
})


def g0_24(ctx: Ctx) -> Iterator[Violation]:
    """Toda tool de un nodo `agent` se documenta para el modelo: `description` y un `args_schema` válido."""
    for node in ctx.flow.nodes:
        if not isinstance(node, AgentNode):
            continue
        for i, ref in enumerate(node.config.tools_allowed):
            tool = ctx.tool(ref)
            if tool is None:
                continue
            sub = f"/config/tools_allowed/{i}"
            if not (tool.description or "").strip() or tool.args_schema is None:
                text = f"la tool {clip(ref.id)} de un nodo agent necesita description y args_schema"
                yield ctx.v("G0-24", node.id, text, sub)
                continue
            problem = unsupported_keyword(tool.args_schema)
            if problem is not None:
                yield ctx.v("G0-24", node.id, f"args_schema de {clip(ref.id)}: {clip(problem)}", sub)


def g0_22(ctx: Ctx) -> Iterator[Violation]:
    """Lo que el modelo genera no alimenta decisiones ni escrituras (m01 §3.13).

    Excepción acotada (ADR 0019 §1, enmienda del 2026-09-30): el `args` de una escritura `draft` puede leer la
    salida de un nodo `agent` cuyo `output_schema` es `DRAFT_OUTPUT_SCHEMA`."""
    produced: dict[str, bool] = {}  # hecho → ¿puede leerlo una escritura draft? (todos sus productores)
    for n in ctx.flow.nodes:
        if isinstance(n, AgentNode):
            fits = n.config.output_schema == DRAFT_OUTPUT_SCHEMA
            produced[n.config.save_as] = produced.get(n.config.save_as, True) and fits
    if not produced:
        return
    for n in ctx.flow.nodes:  # el resultado de una escritura draft alimentada por un agente sigue siendo suyo
        if isinstance(n, WriteToolNode) and n.config.draft:
            reads = {p.name for p in value_paths(dict(n.config.args), strict=False) if p.ns == "facts"}
            if reads & produced.keys():
                produced[n.config.save_as] = False
    for node in ctx.flow.nodes:
        draft_args = isinstance(node, WriteToolNode) and node.config.draft
        for site in _read_sites(ctx, node):
            if site.sub in _AGENT_OUTPUT_SITES:
                continue
            for path in site.paths:
                if path.ns == "facts" and path.name in produced:
                    only_changes = path.rest == ("value", "changes")  # el resto del args lo fija el flow
                    if draft_args and site.sub == "/config/args" and produced[path.name] and only_changes:
                        continue
                    yield ctx.v(
                        "G0-22",
                        node.id,
                        f"{clip(path.raw)} es salida de un nodo agent: solo puede leerla un respond o el "
                        "input_view de un decide o de un agent",
                        site.sub,
                    )
