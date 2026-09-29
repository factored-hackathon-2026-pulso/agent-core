"""Orquestador de las reglas G0 (M1 §3.4, §4)."""

from agent_core.domain import Agent, EntityKind, Flow
from agent_core.flows.agent import release_flows, validate_agent, validate_flow_for_agent
from agent_core.flows.context import Ctx, Rule
from agent_core.flows.registry import AuthoringRegistry
from agent_core.flows.rules.exits import g0_06
from agent_core.flows.rules.phase5 import g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16
from agent_core.flows.rules.structure import g0_02, g0_03, g0_04
from agent_core.flows.rules.writes import g0_05
from agent_core.flows.schema import schema_violations
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, clip, sort_violations

# Reglas que necesitan un esquema sin G0-01. Agregar una regla = agregarla aquí (M1 §9).
FLOW_RULES: tuple[Rule, ...] = (
    g0_03, g0_04, g0_05, g0_06, g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16,
)


def validate_flow(flow: Flow, reg: RegistryView) -> list[Violation]:
    """Gate G0 de un flow aislado. Determinista y total: nunca lanza por un flow mal formado."""
    label = clip(f"{flow.id}@{flow.version}", 160)
    ctx = Ctx.build(flow, reg)
    found: list[Violation] = [*schema_violations(flow), *g0_02(ctx)]
    if not any(v.rule == "G0-01" for v in found):
        for rule in FLOW_RULES:
            found.extend(rule(ctx))
    return sort_violations(v if v.flow is not None else v.model_copy(update={"flow": label}) for v in found)


def validate_registry(reg: AuthoringRegistry) -> list[Violation]:
    """Flows, agentes y, por release, cada agente con sus flows (M1 §3.12).

    Total y determinista. Cada flow se valida una vez (`validate_flow`) y cada par (agente, flow) una vez,
    sin importar cuántas releases lo repitan: el costo es lineal en el tamaño del registro.
    """
    found: list[Violation] = []
    for entity in reg.all(EntityKind.flow):
        if isinstance(entity, Flow):
            found += validate_flow(entity, reg)
    for entity in reg.all(EntityKind.agent):
        if isinstance(entity, Agent):
            found += validate_agent(entity, reg)
    checked: set[tuple[str, str, str, str]] = set()
    for decl in reg.releases():
        where = clip(f"releases/{decl.id}.yaml", 240)
        for entry in decl.agents:
            agent = reg.resolve(EntityKind.agent, entry.agent)
            if not isinstance(agent, Agent):
                found.append(Violation(rule="G0-02", path=where,
                                       message=clip(f"agent {entry.agent} no existe en el registro", 240)))
                continue
            flows, missing = release_flows(reg, decl, agent)
            for ref in missing:
                text = clip(f"flow {ref} no existe en el registro", 240)
                found.append(Violation(rule="G0-02", path=where, message=text))
            for flow in flows:
                key = (agent.id, agent.version, flow.id, flow.version)
                if key not in checked:
                    checked.add(key)
                    found += validate_flow_for_agent(flow, agent, reg)
    return sort_violations(found)
