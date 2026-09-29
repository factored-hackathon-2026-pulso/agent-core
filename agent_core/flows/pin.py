"""Publicación simulada para la demo y las pruebas (M1 §3.11). No es la unidad 2.

Fija la clausura de una release a versiones exactas. Es determinista (mismo registro y misma declaración
dan la misma `Release`, sin depender del orden de inserción), total (solo lanza `SchemaError`), segura ante
ciclos (cada `(tipo, id)` se recorre una sola vez) y lineal en el tamaño de la clausura. Se niega a fijar
una clausura con violaciones G0 en sus flows: nunca hay release parcial ni sin validar.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent_core.domain import (
    AgentSelector,
    DomainError,
    EntityKind,
    Flow,
    Interrupt,
    RefSpec,
    RegistryEntity,
    Release,
    SchemaError,
    StartFlowAction,
    iter_refspecs,
    require_exact_refs,
)
from agent_core.flows.refs import Pointer, entity_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry
from agent_core.flows.validate import validate_flow
from agent_core.flows.view import best_version
from agent_core.flows.violations import Violation, clip

Chosen = dict[tuple[EntityKind, str], str]
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


def pin_release(reg: AuthoringRegistry, release_id: str) -> PinnedRelease:
    """Lanza únicamente `SchemaError`: release inexistente, referencia sin resolver o en conflicto, o
    violaciones G0 en un flow de la clausura."""
    decl = reg.release(release_id)
    if decl is None:
        raise SchemaError(f"la release {clip(release_id, 80)!r} no existe en el registro")
    chosen: Chosen = {}
    errors: set[str] = set()
    pending: deque[tuple[EntityKind, str, str]] = deque()

    def want(kind: EntityKind, ref: RefSpec, where: str) -> None:
        best = best_version(ref.spec, reg.versions(kind, ref.id))
        if best is None:
            errors.add(f"{where}: {kind.value} {ref} no resuelve")
            return
        previous = chosen.get((kind, ref.id))
        if previous is None:
            chosen[(kind, ref.id)] = best
            pending.append((kind, ref.id, best))
        elif previous != best:
            low, high = sorted((best, previous), key=lambda v: tuple(int(p) for p in v.split(".")))
            errors.add(f"{where}: {kind.value} {ref.id} resuelve a {low} y a {high}")

    for i, entry in enumerate(decl.agents):
        want(EntityKind.agent, entry.agent, f"agents/{i}")
    for i, ref in enumerate(decl.flows):
        want(EntityKind.flow, ref, f"flows/{i}")
    for i, interrupt in enumerate(decl.interrupts):
        if isinstance(interrupt.action, StartFlowAction):
            want(EntityKind.flow, interrupt.action.flow, f"interrupts/{i}/action/flow")
        if interrupt.signal_policy is not None:
            want(EntityKind.policy, interrupt.signal_policy, f"interrupts/{i}/signal_policy")
    for i, entry in enumerate(decl.agents):
        for alias in entry.aliases:
            try:  # mismo formato de alias que el selector de agente del request (M0)
                AgentSelector.model_validate({"id": entry.agent.id, "alias": alias})
            except ValidationError:
                errors.add(f"agents/{i}/aliases: alias inválido {clip(alias, 40)!r}")
    want(EntityKind.language_detection, decl.language_detection, "language_detection")
    if decl.injection_ruleset is not None:
        want(EntityKind.injection_ruleset, decl.injection_ruleset, "injection_ruleset")
    while pending:
        kind, ident, version = pending.popleft()
        entity = reg.get_exact(kind, ident, version)
        for site in entity_ref_sites(entity):
            want(site.kind, site.ref, f"{kind.value} {ident}@{version} {pointer_str(site.pointer)}")
        if isinstance(entity, Flow):
            for v in validate_flow(entity, reg):
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
