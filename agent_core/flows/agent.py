"""Chequeos por agente: G0-12 (locales), AG-01 (modo) y referencias del agente (M1 §3.8)."""

from agent_core.domain import (
    Agent,
    CollectNode,
    EntityKind,
    Flow,
    PrincipalType,
    Prompt,
    RiskClass,
    StartFlowAction,
    Template,
    ToolDef,
    TransferNode,
)
from agent_core.flows.context import Ctx
from agent_core.flows.graph import flow_mode
from agent_core.flows.metrics import validate_agent_metrics
from agent_core.flows.refs import TEMPLATE_KINDS, agent_ref_sites, flow_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
from agent_core.flows.rules.phase5 import slot_reads
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, clip, sort_violations


def _agent_label(agent: Agent) -> str:
    return clip(f"{agent.id}@{agent.version}", 160)


def _missing_locales(entity: object, agent: Agent) -> list[str]:
    if not isinstance(entity, Template | Prompt):
        return []
    return sorted(set(agent.supported_locales) - set(entity.locales))


def _locales_message(ref: object, missing: list[str], agent: Agent) -> str:
    shown = ", ".join(clip(m, 20) for m in missing[:10])
    return clip(f"{ref} no tiene los locales {shown} del agente {_agent_label(agent)}", 240)


def _uses_write_draft(flow: Flow, reg: RegistryView) -> bool:
    """True si algún nodo del flow referencia una tool `write_draft` (escritura draft o confirm)."""
    for site in flow_ref_sites(flow):
        if site.kind is not EntityKind.tool:
            continue
        tool = reg.resolve(site.kind, site.ref)
        if isinstance(tool, ToolDef) and tool.risk_class is RiskClass.write_draft:
            return True
    return False


def _unwritable_slot_reads(flow: Flow, agent: Agent, reg: RegistryView, label: str) -> list[Violation]:
    """AG-04: un flow lee `slots.X` y nadie lo puede dejar `validated`. Solo lo escriben un nodo `collect`, el
    `input_schema` de un agente task y el contrato `accepts` de una transferencia; el resto (p. ej. el `input`
    de un task sin schema) entra `claimed` y el motor lo lee como ausente."""
    writable = {n.config.slot for n in flow.nodes if isinstance(n, CollectNode)}
    writable |= set(agent.input_schema or {})
    writable |= set(agent.accepts.slots) if agent.accepts is not None else set()
    found: list[Violation] = []
    for name, nodes in sorted(slot_reads(Ctx.build(flow, reg)).items()):
        if name not in writable:
            found.append(Violation(
                rule="AG-04", flow=label, node_id=nodes[0],
                message=clip(f"el flow lee slots.{name} y {_agent_label(agent)} no tiene cómo dejarlo "
                             "validado (un nodo collect, input_schema o accepts)", 240)))
    return found


def validate_flow_for_agent(flow: Flow, agent: Agent, reg: RegistryView) -> list[Violation]:
    """AG-01 y G0-12 de un flow frente a un agente. Total: una referencia sin resolver se omite (G0-02)."""
    label = clip(f"{flow.id}@{flow.version}", 160)
    found: list[Violation] = []
    mode = flow_mode(flow)
    if mode is not None and mode != agent.mode:
        text = f"el flow es de modo {mode} y el agente {_agent_label(agent)} es {agent.mode}"
        found.append(Violation(rule="AG-01", flow=label, message=text))
    if agent.mode != "conversational" and any(isinstance(n, TransferNode) for n in flow.nodes):
        found.append(Violation(rule="AG-03", flow=label,
                               message=f"el agente {_agent_label(agent)} es {agent.mode}: transfer solo en "
                               "agentes conversacionales"))
    if _uses_write_draft(flow, reg):
        outside = sorted({p.value for p in agent.invocable_by} - {PrincipalType.builder.value})
        if outside:
            who = ", ".join(outside)
            text = f"el flow usa una tool write_draft y {_agent_label(agent)} es invocable por {who}"
            found.append(Violation(rule="AG-02", flow=label, message=clip(text, 240)))
        if agent.subject_kinds:
            text = f"el flow usa una tool write_draft y {_agent_label(agent)} declara subject_kinds"
            found.append(Violation(rule="AG-02", flow=label, message=clip(text, 240)))
    found += _unwritable_slot_reads(flow, agent, reg, label)
    for site in flow_ref_sites(flow):
        if site.kind not in TEMPLATE_KINDS:
            continue
        missing = _missing_locales(reg.resolve(site.kind, site.ref), agent)
        if missing:
            found.append(Violation(rule="G0-12", flow=label, node_id=site.node_id,
                                   path=pointer_str(site.pointer),
                                   message=_locales_message(site.ref, missing, agent)))
    return sort_violations(found)


def validate_agent(agent: Agent, reg: RegistryView) -> list[Violation]:
    """G0-02 sobre las referencias del agente, G0-12 sobre sus plantillas del motor y MT-01 a MT-06
    sobre sus métricas."""
    found: list[Violation] = []
    source = None
    if isinstance(reg, AuthoringRegistry):
        source = reg.source(EntityKind.agent, agent.id, agent.version)
    where = clip(source or f"agents/{agent.id}@{agent.version}.yaml", 200)
    for site in agent_ref_sites(agent):
        path = f"{where}#{pointer_str(site.pointer)}"
        entity = reg.resolve(site.kind, site.ref)
        if entity is None:
            text = clip(f"{site.kind.value} {site.ref} no existe en el registro", 240)
            found.append(Violation(rule="G0-02", path=path, message=text))
        elif site.kind == EntityKind.template and (missing := _missing_locales(entity, agent)):
            text = _locales_message(site.ref, missing, agent)
            found.append(Violation(rule="G0-12", path=path, message=text))
    found += validate_agent_metrics(agent, where, reg)
    if agent.accepts is not None:
        missing = [name for name, value in (("routing", agent.routing), ("understand", agent.understand))
                   if value is None]
        if missing or agent.mode != "conversational":
            what = ", ".join(missing) if missing else "modo conversacional"
            found.append(Violation(rule="AG-03", path=where,
                                   message=clip(f"un agente con accepts necesita {what}", 240)))
    return sort_violations(found)


def release_flows(reg: AuthoringRegistry, decl: ReleaseDecl, agent: Agent) -> tuple[list[Flow], list[str]]:
    """Flows que usa el agente en la release: entry_flow ∪ release.flows ∪ interrupciones start_flow.

    Un `entry_flow` que no resuelve se omite (no aparece en `missing`): lo reporta `validate_agent`.

    No sigue flows dentro de flows: el conjunto es finito y el costo lineal en las referencias de la release.
    """
    # Un entry_flow sin resolver ya lo reporta `validate_agent`: no se repite por cada release.
    entry = [agent.entry_flow] if isinstance(reg.resolve(EntityKind.flow, agent.entry_flow), Flow) else []
    refs = [*entry, *decl.flows,
            *(i.action.flow for i in decl.interrupts if isinstance(i.action, StartFlowAction))]
    flows: dict[tuple[str, str], Flow] = {}
    missing: set[str] = set()
    for ref in refs:
        entity = reg.resolve(EntityKind.flow, ref)
        if isinstance(entity, Flow):
            flows.setdefault((entity.id, entity.version), entity)
        else:
            missing.add(clip(str(ref), 120))
    return [flows[key] for key in sorted(flows)], sorted(missing)
