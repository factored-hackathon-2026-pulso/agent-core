"""Clausura de una release de autoría: qué entidades y versiones exactas resuelve (M1 §3.11, §3.12).

Es la resolución que comparten `pin_release` (la falla con `SchemaError`) y `validate_registry` (la reporta
como violaciones): así el CLI nunca sale con 0 sobre una release que `pin_release` rechazaría. No valida
flows y no importa ni `validate` ni `pin`, de modo que no hay ciclo entre ellos. Determinista y total: no
lanza, cada `(tipo, id)` se recorre una sola vez y el costo es lineal en el tamaño de la clausura.
"""

from collections import deque

from pydantic import ValidationError

from agent_core.domain import AgentSelector, EntityKind, RefSpec, StartFlowAction
from agent_core.flows.refs import entity_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
from agent_core.flows.view import best_version, parse_version
from agent_core.flows.violations import clip

Chosen = dict[tuple[EntityKind, str], str]
Problem = tuple[str, str]  # (dónde, mensaje)
MAX_PROBLEMS = 200  # cota de problemas devueltos; el resto se resume en uno solo


def resolve_closure(
    reg: AuthoringRegistry, decl: ReleaseDecl, *, skip_covered: bool = False
) -> tuple[Chosen, list[Problem]]:
    """Versión elegida por `(tipo, id)` y los problemas de la clausura (ordenados, sin repetidos).

    Con `skip_covered` se omiten los "no resuelve" que `validate_registry` ya reporta por otra vía: los
    agentes, flows e interrupciones `start_flow` de la release y las referencias dentro de cada entidad
    (G0-02 de `validate_flow` y `validate_agent`). Los conflictos de versión y las referencias propias de
    la release (idioma, ruleset, política de señal, conocimiento) siempre se reportan.
    """
    chosen: Chosen = {}
    problems: set[Problem] = set()
    pending: deque[tuple[EntityKind, str, str]] = deque()

    def want(kind: EntityKind, ref: RefSpec, where: str, covered: bool = False) -> None:
        best = best_version(ref.spec, reg.versions(kind, ref.id))
        if best is None:
            if covered and skip_covered:
                return
            problems.add((clip(where, 240), clip(f"{kind.value} {clip(str(ref), 120)} no resuelve", 240)))
            return
        previous = chosen.get((kind, ref.id))
        if previous is None:
            chosen[(kind, ref.id)] = best
            pending.append((kind, ref.id, best))
        elif previous != best:
            low, high = sorted((best, previous), key=parse_version)
            text = f"{kind.value} {clip(ref.id, 80)} resuelve a {low} y a {high}"
            problems.add((clip(where, 240), clip(text, 240)))

    for i, entry in enumerate(decl.agents):
        want(EntityKind.agent, entry.agent, f"agents/{i}", covered=True)
    for i, ref in enumerate(decl.flows):
        want(EntityKind.flow, ref, f"flows/{i}", covered=True)
    for i, interrupt in enumerate(decl.interrupts):
        if isinstance(interrupt.action, StartFlowAction):
            want(EntityKind.flow, interrupt.action.flow, f"interrupts/{i}/action/flow", covered=True)
        if interrupt.signal_policy is not None:
            want(EntityKind.policy, interrupt.signal_policy, f"interrupts/{i}/signal_policy")
    for i, entry in enumerate(decl.agents):
        for alias in entry.aliases:
            try:  # mismo formato de alias que el selector de agente del request (M0)
                AgentSelector.model_validate({"id": entry.agent.id, "alias": alias})
            except ValidationError:
                problems.add((f"agents/{i}/aliases", f"alias inválido {clip(alias, 40)!r}"))
    want(EntityKind.language_detection, decl.language_detection, "language_detection")
    if decl.injection_ruleset is not None:
        want(EntityKind.injection_ruleset, decl.injection_ruleset, "injection_ruleset")
    if decl.knowledge is not None:
        want(EntityKind.knowledge_snapshot, decl.knowledge, "knowledge")
    while pending:
        kind, ident, version = pending.popleft()
        label = f"{kind.value} {clip(ident, 80)}@{clip(version, 40)}"
        try:
            sites = entity_ref_sites(reg.get_exact(kind, ident, version))
        except (ValueError, TypeError, LookupError):
            problems.add((label, "no se pudieron recorrer sus referencias"))
            continue
        for site in sites:
            want(site.kind, site.ref, f"{label} {pointer_str(site.pointer)}", covered=True)
    ordered = sorted(problems)
    if len(ordered) > MAX_PROBLEMS:
        omitted = len(ordered) - MAX_PROBLEMS
        ordered = [*ordered[:MAX_PROBLEMS], ("releases", f"se omitieron {omitted} problemas más")]
    return chosen, ordered


def closure_problems(
    reg: AuthoringRegistry, decl: ReleaseDecl, *, skip_covered: bool = False
) -> list[Problem]:
    """Problemas de la clausura de la release (referencias sin resolver, conflictos de versión, alias)."""
    return resolve_closure(reg, decl, skip_covered=skip_covered)[1]
