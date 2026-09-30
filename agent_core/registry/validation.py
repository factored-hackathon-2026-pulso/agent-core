"""Validación de la candidata (spec §5.2): M1 completo, versionado, límites y suites."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from agent_core.domain import Flow, canonical_bytes
from agent_core.flows import AuthoringRegistry, Violation, validate_registry
from agent_core.registry.candidate import Candidate, parse_semver
from agent_core.registry.entities import encode_entity, version_ref
from agent_core.registry.models import EntityDraft


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
    for suite in c.suites:
        if suite.agent_id != c.agent_id:
            out.append(Violation(rule="REG-SUITE", path=f"eval_suite:{suite.id}",
                                 message=f"la suite es del agente {suite.agent_id}, no de {c.agent_id}"))
    return out
