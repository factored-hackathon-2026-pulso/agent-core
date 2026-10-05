"""Dónde hay referencias en cada entidad y a qué tipo apuntan (M1 §3.4.2)."""

from dataclasses import dataclass

from agent_core.domain import (
    Agent,
    AgentNode,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EntityKind,
    Flow,
    KnowledgeNode,
    Node,
    Prompt,
    RefSpec,
    RegistryEntity,
    RespondNode,
    RuleNode,
    SuggestNode,
    ToolNode,
    VerifyNode,
    WriteToolNode,
)

Pointer = tuple[str | int, ...]
TEMPLATE_KINDS = frozenset({EntityKind.template, EntityKind.prompt})


@dataclass(frozen=True)
class RefSite:
    """Una referencia, su tipo y su ubicación en `model_dump(by_alias=True)` de la entidad."""

    kind: EntityKind
    ref: RefSpec
    pointer: Pointer
    node_id: str | None = None


def pointer_str(pointer: Pointer) -> str:
    return "/" + "/".join(str(part) for part in pointer)


def _ref_or_none(text: object) -> RefSpec | None:
    if not isinstance(text, str):
        return None
    try:
        return RefSpec.parse(text)
    except ValueError:
        return None


def node_ref_sites(node: Node, index: int) -> list[RefSite]:
    base: Pointer = ("nodes", index, "config")
    sites: list[RefSite] = []

    def add(kind: EntityKind, ref: RefSpec | None, *tail: str) -> None:
        if ref is not None:
            sites.append(RefSite(kind, ref, base + tail, node.id))

    match node:
        case DecideNode():
            add(EntityKind.decision_model, node.config.model, "model")
        case RuleNode():
            add(EntityKind.policy, node.config.policy, "policy")
        case CollectNode():
            add(EntityKind.template, node.config.prompt_ref, "prompt_ref")
            validator = node.config.validator
            if validator is not None and validator.kind == "decide":
                add(EntityKind.decision_model, _ref_or_none(validator.value), "validator", "value")
        case ToolNode():
            add(EntityKind.tool, node.config.tool, "tool")
        case WriteToolNode():
            if node.config.tool is not None:
                add(EntityKind.tool, node.config.tool, "tool")
        case ConfirmNode():
            add(EntityKind.tool, node.config.action.tool, "action", "tool")
            add(EntityKind.template, node.config.summary_template, "summary_template")
            add(EntityKind.template, node.config.reprompt_template, "reprompt_template")
        case VerifyNode():
            add(EntityKind.tool, node.config.readback, "readback")
        case RespondNode():
            add(EntityKind.template, node.config.template_ref, "template_ref")
            if node.config.generate is not None:
                add(EntityKind.prompt, node.config.generate.prompt_ref, "generate", "prompt_ref")
                add(EntityKind.template, node.config.generate.fallback_template_ref, "generate",
                    "fallback_template_ref")
        case KnowledgeNode():
            add(EntityKind.decision_model, node.config.selector, "selector")
        case AgentNode():
            for i, ref in enumerate(node.config.tools_allowed):
                sites.append(RefSite(EntityKind.tool, ref, (*base, "tools_allowed", i), node.id))
            add(EntityKind.prompt, node.config.prompt_ref, "prompt_ref")
        case SuggestNode():
            for i, ref in enumerate(node.config.tools_allowed):
                sites.append(RefSite(EntityKind.tool, ref, (*base, "tools_allowed", i), node.id))
            for i, ref in enumerate(node.config.actions_allowed):
                sites.append(RefSite(EntityKind.tool, ref, (*base, "actions_allowed", i), node.id))
            add(EntityKind.prompt, node.config.prompt_ref, "prompt_ref")
        case _:
            pass
    return sites


def flow_ref_sites(flow: Flow) -> list[RefSite]:
    return [site for index, node in enumerate(flow.nodes) for site in node_ref_sites(node, index)]


def agent_ref_sites(agent: Agent) -> list[RefSite]:
    sites = [RefSite(EntityKind.flow, agent.entry_flow, ("entry_flow",))]
    if agent.understand is not None:
        sites.append(RefSite(EntityKind.decision_model, agent.understand, ("understand",)))
    if agent.slots_model is not None:
        sites.append(RefSite(EntityKind.decision_model, agent.slots_model, ("slots_model",)))
    sites += [
        RefSite(EntityKind.tool, ref, ("tools_allowed", i)) for i, ref in enumerate(agent.tools_allowed)
    ]
    for name in type(agent.templates).model_fields:
        sites.append(RefSite(EntityKind.template, getattr(agent.templates, name), ("templates", name)))
    return sites


def entity_ref_sites(entity: RegistryEntity) -> list[RefSite]:
    if isinstance(entity, Flow):
        return flow_ref_sites(entity)
    if isinstance(entity, Agent):
        return agent_ref_sites(entity)
    if isinstance(entity, Prompt):
        return [RefSite(EntityKind.model_profile, entity.model_profile, ("model_profile",))]
    return []
