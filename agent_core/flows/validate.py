"""Orquestador de las reglas G0 (M1 §3.4, §4)."""

from agent_core.domain import EntityKind, Flow
from agent_core.flows.context import Ctx, Rule, clip
from agent_core.flows.registry import AuthoringRegistry
from agent_core.flows.rules.exits import g0_06
from agent_core.flows.rules.phase5 import g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16
from agent_core.flows.rules.structure import g0_02, g0_03, g0_04
from agent_core.flows.rules.writes import g0_05
from agent_core.flows.schema import schema_violations
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, sort_violations

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
    """Toda versión de todo flow del registro (la Task 13 agrega agentes y releases)."""
    found: list[Violation] = []
    for entity in reg.all(EntityKind.flow):
        if isinstance(entity, Flow):
            found += validate_flow(entity, reg)
    return sort_violations(found)
