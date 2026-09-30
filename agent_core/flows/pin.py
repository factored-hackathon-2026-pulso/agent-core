"""Publicación simulada para la demo y las pruebas (M1 §3.11). No es la unidad 2.

Fija la clausura de una release a versiones exactas. Es determinista (mismo registro y misma declaración
dan la misma `Release`, sin depender del orden de inserción), total (solo lanza `SchemaError`), segura ante
ciclos (cada `(tipo, id)` se recorre una sola vez) y lineal en el tamaño de la clausura. Se niega a fijar
una clausura con violaciones G0 en sus flows: nunca hay release parcial ni sin validar.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_core.domain import (
    DomainError,
    EntityKind,
    Flow,
    Interrupt,
    KnowledgeSnapshot,
    RefSpec,
    RegistryEntity,
    Release,
    SchemaError,
    StartFlowAction,
    iter_refspecs,
    require_exact_refs,
)
from agent_core.flows.closure import Chosen, resolve_closure
from agent_core.flows.refs import Pointer, entity_ref_sites
from agent_core.flows.registry import AuthoringRegistry
from agent_core.flows.validate import validate_flow, validate_flow_for_release
from agent_core.flows.violations import Violation, clip

MAX_REPORTED = 20  # errores que lleva el mensaje; el resto se resume en un conteo


@dataclass(frozen=True)
class PinnedRelease:
    release: Release
    entities: list[RegistryEntity]
    aliases: dict[str, list[str]]


def _set(doc: Any, pointer: Pointer, value: str) -> None:
    current = doc
    for part in pointer[:-1]:
        current = current[part]
    current[pointer[-1]] = value


def _exact(chosen: Chosen, kind: EntityKind, ref: RefSpec) -> str:
    return f"{ref.id}@{chosen[(kind, ref.id)]}"


def _pinned(entity: RegistryEntity, chosen: Chosen) -> RegistryEntity:
    sites = entity_ref_sites(entity)
    doc = entity.model_dump(mode="python", by_alias=True)
    covered: set[str] = set()
    for site in sites:
        exact = _exact(chosen, site.kind, site.ref)
        _set(doc, site.pointer, exact)
        covered.add(exact)
    pinned = type(entity).model_validate(doc) if sites else entity
    # Falla cerrada: toda referencia del modelo debe haber sido fijada por un sitio conocido. Una referencia
    # de un tipo de nodo que `entity_ref_sites` no conoce quedaría fuera de `Release.entities`.
    for ref in iter_refspecs(pinned):
        if str(ref) not in covered:
            label = f"{entity.id}@{entity.version}"
            raise SchemaError(f"referencia sin fijar en {clip(label, 120)}: {clip(str(ref), 120)}")
    return pinned


def _pinned_interrupt(interrupt: Interrupt, chosen: Chosen) -> Interrupt:
    doc = interrupt.model_dump(mode="python")
    if isinstance(interrupt.action, StartFlowAction):
        doc["action"]["flow"] = _exact(chosen, EntityKind.flow, interrupt.action.flow)
    if interrupt.signal_policy is not None:
        doc["signal_policy"] = _exact(chosen, EntityKind.policy, interrupt.signal_policy)
    return Interrupt.model_validate(doc)


def _violation_line(label: str, v: Violation) -> str:
    return f"{label} {v.rule} {v.node_id or '-'} {v.path or '-'}: {clip(v.message, 120)}"


def _fail(errors: set[str]) -> SchemaError:
    ordered = sorted(errors)
    shown = "; ".join(clip(e, 240) for e in ordered[:MAX_REPORTED])
    extra = f"; y {len(ordered) - MAX_REPORTED} más" if len(ordered) > MAX_REPORTED else ""
    return SchemaError("pin_release: " + shown + extra)


def _snapshot(reg: AuthoringRegistry, chosen: Chosen, knowledge: RefSpec | None) -> KnowledgeSnapshot | None:
    if knowledge is None:
        return None
    version = chosen.get((EntityKind.knowledge_snapshot, knowledge.id))
    entity = reg.get_exact(EntityKind.knowledge_snapshot, knowledge.id, version) if version else None
    return entity if isinstance(entity, KnowledgeSnapshot) else None


def pin_release(reg: AuthoringRegistry, release_id: str) -> PinnedRelease:
    """Lanza únicamente `SchemaError`: release inexistente, referencia sin resolver o en conflicto, o
    violaciones G0 en un flow de la clausura."""
    decl = reg.release(release_id)
    if decl is None:
        raise SchemaError(f"la release {clip(release_id, 80)!r} no existe en el registro")
    chosen, problems = resolve_closure(reg, decl)
    errors = {f"{where}: {message}" for where, message in problems}
    snapshot = _snapshot(reg, chosen, decl.knowledge)  # G0-17, G0-19 y G0-20 (M12) miran el de esta release
    for (kind, ident), version in chosen.items():
        entity = reg.get_exact(kind, ident, version)
        if isinstance(entity, Flow):
            for v in (*validate_flow(entity, reg), *validate_flow_for_release(entity, snapshot, reg)):
                errors.add(_violation_line(f"{ident}@{version}", v))
    if errors:
        raise _fail(errors)

    try:
        ordered = sorted(chosen.items(), key=lambda item: (item[0][0].value, item[0][1]))
        entities = [
            _pinned(reg.get_exact(kind, ident, version), chosen) for (kind, ident), version in ordered
        ]
        table: dict[EntityKind, dict[str, str]] = {}
        for (kind, ident), version in ordered:
            table.setdefault(kind, {})[ident] = version
        release = Release.model_validate(
            {
                "id": decl.id,
                "status": "active",
                "entities": table,
                "interrupts": [_pinned_interrupt(i, chosen) for i in decl.interrupts],
                "language_detection": _exact(chosen, EntityKind.language_detection, decl.language_detection),
                "injection_ruleset": (
                    _exact(chosen, EntityKind.injection_ruleset, decl.injection_ruleset)
                    if decl.injection_ruleset is not None else None
                ),
                "knowledge_snapshot": (
                    _exact(chosen, EntityKind.knowledge_snapshot, decl.knowledge)
                    if decl.knowledge is not None else None
                ),
                "max_input_chars": decl.max_input_chars,
            }
        )
        require_exact_refs(release)
        for entity in entities:
            require_exact_refs(entity)
    except SchemaError:
        raise
    except (DomainError, ValidationError, ValueError, KeyError, TypeError, LookupError) as exc:
        raise SchemaError(
            f"pin_release: la clausura de {clip(release_id, 80)!r} no se pudo fijar ({type(exc).__name__})"
        ) from exc
    aliases: dict[str, set[str]] = {}
    for entry in decl.agents:
        aliases.setdefault(entry.agent.id, set()).update(entry.aliases)
    return PinnedRelease(
        release=release, entities=entities,
        aliases={agent: sorted(names) for agent, names in sorted(aliases.items())},
    )
