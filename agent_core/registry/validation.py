"""Validación de la candidata (spec §5.2): M1 completo, versionado, límites y suites."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from agent_core.domain import PLATFORM_METRIC_PREFIX, Agent, Flow, canonical_bytes
from agent_core.flows import AuthoringRegistry, Violation, validate_registry
from agent_core.registry.candidate import Candidate, parse_semver
from agent_core.registry.entities import encode_entity, version_ref
from agent_core.registry.models import RELEASE_SETTINGS, EntityDraft
from agent_core.registry.suite import EvalSuite, SuiteProblem, suite_problems


@dataclass(frozen=True)
class Limits:
    max_changes: int = 50
    max_entity_bytes: int = 262_144
    max_flow_nodes: int = 200


DEFAULT_LIMITS = Limits()


def check_draft_limits(drafts: Sequence[EntityDraft], limits: Limits) -> list[Violation]:
    """Límites que se comprueban al guardar el borrador, antes de parsear nada (spec §5.2.4)."""
    if len(drafts) > limits.max_changes:
        return [Violation(rule="REG-LIMIT", message=f"la propuesta cambia {len(drafts)} entidades; "
                                                      f"el máximo es {limits.max_changes}")]
    out: list[Violation] = []
    for d in drafts:
        size = len(canonical_bytes(d.content))
        if size > limits.max_entity_bytes:
            where = f"{d.kind[:40]}:{d.id[:80]}"
            out.append(Violation(rule="REG-LIMIT", path=where,
                                 message=f"{where} ocupa {size} bytes; "
                                         f"el máximo es {limits.max_entity_bytes}"))
    return out


def changes_interrupts(drafts: Sequence[EntityDraft]) -> bool:
    """`True` si algún borrador `release_settings` reemplaza las interrupciones (quitar un escalamiento de
    seguridad es el cambio más delicado de la release; N-07)."""
    return any(d.kind == RELEASE_SETTINGS and d.content.get("interrupts") is not None for d in drafts)


def platform_edits(drafts: Sequence[EntityDraft]) -> list[str]:
    """Paths in the draft that declare or set thresholds for a platform guardrail (evaluation spec section 7).

    Guardrails belong to the platform: no proposal edits them, neither through the agent's metrics (which
    M1 also rejects with `MT-05`) nor through the suite's thresholds."""
    found: list[str] = []
    for d in drafts:
        where = f"{d.kind[:40]}:{d.id[:80]}"
        if d.kind == "agent" and isinstance(metrics := d.content.get("metrics"), list):
            for i, item in enumerate(metrics):
                mid = item.get("id") if isinstance(item, dict) else None
                if isinstance(mid, str) and mid.startswith(PLATFORM_METRIC_PREFIX):
                    found.append(f"{where}/metrics/{i}/id")
        elif d.kind == "eval_suite" and isinstance(thresholds := d.content.get("thresholds"), dict):
            found.extend(f"{where}/thresholds/{key[:80]}" for key in sorted(thresholds)
                         if key.startswith(PLATFORM_METRIC_PREFIX))
    return found


def validate_candidate(c: Candidate, *, base_versions: Mapping[tuple[str, str], str],
                       drafted: Collection[tuple[str, str]],
                       limits: Limits = DEFAULT_LIMITS) -> list[Violation]:
    out: list[Violation] = list(validate_registry(AuthoringRegistry.from_entities(c.entities, [c.decl])))
    for entity in [*c.entities, *c.suites]:
        ref = version_ref(entity)
        where = f"{ref.kind}:{ref.id}"
        size = len(encode_entity(entity))
        if size > limits.max_entity_bytes:
            out.append(Violation(rule="REG-LIMIT", path=where,
                                 message=f"{where} ocupa {size} bytes; "
                                         f"el máximo es {limits.max_entity_bytes}"))
        if isinstance(entity, Flow) and len(entity.nodes) > limits.max_flow_nodes:
            out.append(Violation(rule="REG-LIMIT", path=where,
                                 message=f"el flow tiene {len(entity.nodes)} nodos; "
                                         f"el máximo es {limits.max_flow_nodes}"))
        if (ref.kind, ref.id) in drafted and (ref.kind, ref.id) in base_versions:
            new, old = parse_semver(ref.version), parse_semver(base_versions[(ref.kind, ref.id)])
            if new is None or old is None or new <= old:
                out.append(Violation(rule="REG-VERSION", path=where,
                                     message=f"la versión {ref.version} debe ser mayor que la vigente "
                                             f"{base_versions[(ref.kind, ref.id)]}"))
    agent = next((e for e in c.entities if isinstance(e, Agent) and e.id == c.agent_id), None)
    for suite in c.suites:
        if agent is not None:  # without the agent the candidate already failed with REG-AGENT
            out.extend(suite_violations(suite, suite_problems(agent, suite)))
    return out


def suite_violations(suite: EvalSuite, problems: Sequence[SuiteProblem]) -> list[Violation]:
    """A suite's problems as `REG-SUITE` violations (the problem code leads the message)."""
    return [Violation(rule="REG-SUITE", path=f"eval_suite:{suite.id}{p.path}",
                      message=f"{p.code.value}: {p.message}") for p in problems]
