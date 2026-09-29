"""Chequeos por agente: G0-12 (locales), AG-01 (modo) y referencias del agente (M1 §3.8)."""

from agent_core.domain import Agent, EntityKind, Flow, Prompt, StartFlowAction, Template
from agent_core.flows.graph import flow_mode
from agent_core.flows.refs import TEMPLATE_KINDS, agent_ref_sites, flow_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
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


def validate_flow_for_agent(flow: Flow, agent: Agent, reg: RegistryView) -> list[Violation]:
    """AG-01 y G0-12 de un flow frente a un agente. Total: una referencia sin resolver se omite (G0-02)."""
    label = clip(f"{flow.id}@{flow.version}", 160)
    found: list[Violation] = []
    mode = flow_mode(flow)
    if mode is not None and mode != agent.mode:
        text = f"el flow es de modo {mode} y el agente {_agent_label(agent)} es {agent.mode}"
        found.append(Violation(rule="AG-01", flow=label, message=text))
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
    """G0-02 sobre las referencias del agente y G0-12 sobre sus plantillas del motor."""
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
