"""Candidata de una propuesta (spec §5.1, §3.4, §3.5): base + borrador, cascada de versiones, pin de M1."""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from agent_core.domain import (
    EntityKind,
    Interrupt,
    Policy,
    RefSpec,
    RegistryEntity,
    Release,
    SchemaError,
    StartFlowAction,
    canonical_bytes,
    sha256_hex,
)
from agent_core.flows import (
    AuthoringRegistry,
    FlowSchemaError,
    ReleaseDecl,
    Violation,
    entity_ref_sites,
    kind_of,
    parse_flow,
    pin_release,
)
from agent_core.registry.entities import content_hash, model_for, version_ref
from agent_core.registry.errors import RegistryError
from agent_core.registry.models import (
    RELEASE_SETTINGS,
    EntityDraft,
    ReleaseSettings,
    VersionDocs,
    VersionRef,
)
from agent_core.registry.suite import EvalSuite

CANDIDATE_RELEASE_ID = "candidate"
CANDIDATE_ALIAS = "staging"
MAX_SCHEMA_ERRORS = 3  # errores de esquema que se reportan por entidad del borrador
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
Key = tuple[EntityKind, str]
Published = Callable[[VersionRef], str | None]  # content_hash guardado de una versión, o None


def parse_semver(version: str) -> tuple[int, int, int] | None:
    m = _SEMVER.fullmatch(version)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def release_hash(release: Release) -> str:
    return sha256_hex(canonical_bytes(release.model_dump(mode="json", exclude={"id", "status"})))


@dataclass(frozen=True)
class Candidate:
    agent_id: str
    release: Release
    decl: ReleaseDecl
    entities: tuple[RegistryEntity, ...]
    suites: tuple[EvalSuite, ...]
    new_versions: tuple[VersionRef, ...]
    auto_bumped: tuple[VersionRef, ...]
    docs: Mapping[VersionRef, VersionDocs]
    content_hashes: Mapping[VersionRef, str]
    release_hash: str
    candidate_hash: str

    @property
    def agent_version(self) -> str:
        return self.release.entities[EntityKind.agent][self.agent_id]


class CandidateError(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__(f"{len(violations)} violaciones")
        self.violations = violations


def _v(rule: str, message: str, path: str | None = None) -> Violation:
    return Violation(rule=rule, path=path, message=message[:500])


def _key_text(kind: str, ident: str) -> str:
    return f"{kind}:{ident}"


def _sorted_keys(keys: Iterable[Key]) -> list[Key]:
    return sorted(keys, key=lambda k: (k[0].value, k[1]))


def _schema_message(exc: ValidationError) -> str:
    """Resumen del error de esquema sin los valores de entrada (pueden traer datos de clientes)."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    parts = ["/".join(str(p) for p in err["loc"]) + ": " + err["msg"] for err in errors[:MAX_SCHEMA_ERRORS]]
    return "el contenido no cumple el esquema: " + "; ".join(parts)


def _parse_drafts(drafts: Sequence[EntityDraft]) -> tuple[dict[Key, RegistryEntity], list[EvalSuite],
                                                           dict[str, VersionDocs], list[Violation]]:
    entities: dict[Key, RegistryEntity] = {}
    suites: list[EvalSuite] = []
    docs: dict[str, VersionDocs] = {}
    problems: list[Violation] = []
    for d in drafts:
        where = _key_text(d.kind, d.id)
        if d.kind == EntityKind.knowledge_snapshot.value:
            problems.append(_v("REG-KNOWLEDGE", "en esta entrega las propuestas no cambian el conocimiento",
                               where))
            continue
        try:
            model = model_for(d.kind)
            parsed: Any = parse_flow(d.content) if d.kind == EntityKind.flow.value else \
                model.model_validate(d.content)
        except RegistryError:
            problems.append(_v("REG-KIND", f"tipo de entidad desconocido: {d.kind[:40]}", where))
            continue
        except FlowSchemaError as exc:
            problems.extend(v.model_copy(update={"path": v.path or where}) for v in exc.violations)
            continue
        except ValidationError as exc:
            problems.append(_v("REG-SCHEMA", _schema_message(exc), where))
            continue
        except ValueError:
            problems.append(_v("REG-SCHEMA", "el contenido no cumple el esquema", where))
            continue
        if where in docs:
            problems.append(_v("REG-DUPLICATE", "la entidad aparece dos veces en el borrador", where))
            continue
        docs[where] = d.docs
        if isinstance(parsed, EvalSuite):
            suites.append(parsed)
        else:
            entities[(kind_of(parsed), parsed.id)] = parsed
    return entities, suites, docs, problems


def _parse_settings(drafts: Sequence[EntityDraft]) -> tuple[ReleaseSettings, list[Violation]]:
    """El borrador reservado `release_settings` (N-07): a lo sumo uno; sin él, todo se hereda de la base."""
    found = [d for d in drafts if d.kind == RELEASE_SETTINGS]
    if not found:
        return ReleaseSettings(), []
    if len(found) > 1:
        return ReleaseSettings(), [_v("REG-DUPLICATE", "release_settings aparece dos veces en el borrador",
                                      RELEASE_SETTINGS)]
    try:
        return ReleaseSettings.model_validate(found[0].content), []
    except ValidationError as exc:
        return ReleaseSettings(), [_v("REG-SCHEMA", _schema_message(exc), RELEASE_SETTINGS)]


def _set(doc: Any, pointer: tuple[str | int, ...], value: str) -> None:
    for part in pointer[:-1]:
        doc = doc[part]
    doc[pointer[-1]] = value


def _retarget(entity: RegistryEntity,
              merged: Mapping[Key, RegistryEntity]) -> tuple[RegistryEntity, list[str]]:
    """Apunta cada referencia exacta a la versión que tiene su destino en `merged`.

    Los rangos no se tocan: los resuelve `pin_release` contra `merged`."""
    changes: list[str] = []
    dump = entity.model_dump(mode="json", by_alias=True)
    for site in entity_ref_sites(entity):
        target = merged.get((site.kind, site.ref.id))
        if target is None or not site.ref.is_exact or site.ref.spec == target.version:
            continue
        _set(dump, site.pointer, f"{site.ref.id}@{target.version}")
        changes.append(f"{site.ref.id}@{site.ref.spec} → {site.ref.id}@{target.version}")
    if not changes:
        return entity, []
    return type(entity).model_validate(dump), changes


def _next_free(ref: VersionRef, taken: Published) -> str:
    """Siguiente patch que no esté publicado."""
    semver = parse_semver(ref.version)
    if semver is None:
        raise CandidateError([_v("REG-VERSION", f"versión no exacta en la base: {ref}", str(ref))])
    major, minor, patch = semver
    while True:
        patch += 1
        candidate = VersionRef(kind=ref.kind, id=ref.id, version=f"{major}.{minor}.{patch}")
        if taken(candidate) is None:
            return candidate.version


def _cascade(drafted: Mapping[Key, RegistryEntity], merged: dict[Key, RegistryEntity],
             published_hash: Published) -> tuple[dict[Key, str], dict[Key, list[str]]]:
    """Sube patch a cada entidad no editada que referencia con versión exacta algo que cambió (§3.4).

    Punto fijo: cada clave recibe su versión nueva una sola vez; las pasadas siguientes solo re-apuntan
    referencias. Termina porque las versiones asignadas no vuelven a cambiar."""
    auto: dict[Key, str] = {}
    notes: dict[Key, list[str]] = {}
    changed = True
    while changed:
        changed = False
        for key in _sorted_keys(merged):
            if key in drafted:
                continue
            retargeted, changes = _retarget(merged[key], merged)
            if not changes:
                continue
            if key not in auto:
                auto[key] = _next_free(version_ref(merged[key]), published_hash)
            notes.setdefault(key, []).extend(changes)
            dump = retargeted.model_dump(mode="json", by_alias=True)
            dump["version"] = auto[key]
            merged[key] = type(retargeted).model_validate(dump)
            changed = True
    return auto, notes


def _retarget_ref(ref: RefSpec, kind: EntityKind, merged: Mapping[Key, RegistryEntity]) -> RefSpec:
    target = merged.get((kind, ref.id))
    if target is None or not ref.is_exact:
        return ref
    return RefSpec(id=ref.id, spec=target.version)


def _same_action(a: Interrupt, b: Interrupt) -> bool:
    if isinstance(a.action, StartFlowAction) and isinstance(b.action, StartFlowAction):
        return a.action.flow.id == b.action.flow.id
    return a.action == b.action


def locked_interrupt_violations(base: Release | None, wanted: Sequence[Interrupt] | None) -> list[Violation]:
    """REG-LOCKED: `wanted` reemplaza las interrupciones de la base, pero las `locked` de la base tienen que
    seguir, con la misma acción, al menos la misma prioridad y `locked`. `wanted = None` hereda la base."""
    if base is None or wanted is None:
        return []
    kept = {i.id: i for i in wanted}
    found: list[Violation] = []
    for locked in (i for i in base.interrupts if i.locked):
        now = kept.get(locked.id)
        if now is None:
            found.append(_v("REG-LOCKED", f"la interrupción {locked.id[:80]} es de plataforma: no se quita",
                            RELEASE_SETTINGS))
        elif not now.locked or now.priority < locked.priority or not _same_action(now, locked):
            found.append(_v("REG-LOCKED", f"la interrupción {locked.id[:80]} es de plataforma: no se le baja "
                            "la prioridad, no se le cambia la acción ni se le quita el bloqueo",
                            RELEASE_SETTINGS))
    return found


def locked_policy_violations(base: Sequence[Policy], candidate: Sequence[Policy]) -> list[Violation]:
    """REG-LOCKED for policies: every `locked` policy of the base must stay in the candidate's closure, still
    `locked` and with the same expression and owner. Weakening cannot be decided from an arbitrary expression,
    so any change is refused; the base closure only (a new agent has none)."""
    now = {p.id: p for p in candidate}
    found: list[Violation] = []
    for locked in (p for p in base if p.locked):
        kept = now.get(locked.id)
        where = _key_text("policy", locked.id)
        if kept is None:
            found.append(_v("REG-LOCKED", f"policy {locked.id[:80]} is a platform guardrail: it cannot be "
                            "removed from the release", where))
        elif not kept.locked or kept.expr != locked.expr or kept.owner != locked.owner:
            found.append(_v("REG-LOCKED", f"policy {locked.id[:80]} is a platform guardrail: it cannot be "
                            "changed or unlocked", where))
    return found


def _interrupts(interrupts: Sequence[Interrupt], merged: Mapping[Key, RegistryEntity]) -> list[Interrupt]:
    """Interrupciones (las de la base o las de `release_settings`) apuntando a las versiones de flows y
    policies de la candidata."""
    result: list[Interrupt] = []
    for interrupt in interrupts:
        update: dict[str, Any] = {}
        if isinstance(interrupt.action, StartFlowAction):
            flow = _retarget_ref(interrupt.action.flow, EntityKind.flow, merged)
            update["action"] = interrupt.action.model_copy(update={"flow": flow})
        if interrupt.signal_policy is not None:
            update["signal_policy"] = _retarget_ref(interrupt.signal_policy, EntityKind.policy, merged)
        result.append(interrupt.model_copy(update=update))
    return result


def _decl(agent_id: str, base: Release | None, merged: Mapping[Key, RegistryEntity],
          settings: ReleaseSettings, donor: Release | None = None) -> ReleaseDecl:
    """Release declarada de la candidata: los agentes de la base más `agent_id`, limitados a los que hay en
    `merged`, y todos los flows de `merged`. Lanza `CandidateError` si falta el agente."""
    def ref(kind: EntityKind, ident: str, fallback: str | None = None) -> str:
        # Una referencia sin destino en `merged` queda tal cual y `pin_release` la reporta como REG-PIN.
        target = merged.get((kind, ident))
        return f"{ident}@{target.version}" if target is not None else fallback or ident

    present = {i for (k, i) in merged if k is EntityKind.agent}
    if agent_id not in present:
        raise CandidateError([_v("REG-AGENT", f"la candidata no contiene al agente {agent_id[:80]}")])
    wanted = set(base.entities.get(EntityKind.agent, {})) if base is not None else set()
    settled = base if base is not None else donor  # fuente de los ajustes; el donante no aporta agentes
    agents = sorted((wanted | {agent_id}) & present)
    flows = sorted(i for (k, i) in merged if k is EntityKind.flow)
    langs = sorted(i for (k, i) in merged if k is EntityKind.language_detection)
    if settings.language_detection is not None:  # sin destino en `merged`, `pin_release` lo reporta (REG-PIN)
        lang = ref(EntityKind.language_detection, settings.language_detection)
    elif settled is not None:
        lang = ref(EntityKind.language_detection, settled.language_detection.id,
                   str(settled.language_detection))
    else:  # sin base, la del borrador; si no trae ninguna, `pin_release` lo reporta como REG-PIN
        lang = ref(EntityKind.language_detection, langs[0]) if langs else "sin-deteccion@1.0.0"
    wanted_interrupts = settings.interrupts if settings.interrupts is not None else (
        settled.interrupts if settled is not None else [])
    data: dict[str, Any] = {
        "id": CANDIDATE_RELEASE_ID,
        "agents": [{"agent": ref(EntityKind.agent, a), "aliases": [CANDIDATE_ALIAS]} for a in agents],
        "flows": [ref(EntityKind.flow, f) for f in flows],
        "interrupts": _interrupts(wanted_interrupts, merged),
        "language_detection": lang,
        "max_input_chars": settings.max_input_chars or (
            settled.max_input_chars if settled is not None else 4000),
    }
    if settings.injection_ruleset is not None:
        data["injection_ruleset"] = ref(EntityKind.injection_ruleset, settings.injection_ruleset)
    elif settled is not None and settled.injection_ruleset is not None:
        data["injection_ruleset"] = ref(EntityKind.injection_ruleset, settled.injection_ruleset.id,
                                        str(settled.injection_ruleset))
    if base is not None and base.knowledge_snapshot is not None:
        data["knowledge"] = str(base.knowledge_snapshot)
    return ReleaseDecl.model_validate(data)


def build_candidate(*, agent_id: str, base: Release | None, base_entities: Sequence[RegistryEntity],
                    drafts: Sequence[EntityDraft], published_hash: Published,
                    donor: Release | None = None) -> Candidate:
    settings, settings_problems = _parse_settings(drafts)
    if settings.inherit_from is not None and base is not None:
        settings_problems.append(_v("REG-SCHEMA", "inherit_from solo vale para un agente nuevo (sin base)",
                                    RELEASE_SETTINGS))
    drafted, suites, draft_docs, problems = _parse_drafts([d for d in drafts if d.kind != RELEASE_SETTINGS])
    problems.extend(settings_problems)
    problems.extend(locked_interrupt_violations(base or donor, settings.interrupts))
    if problems:
        raise CandidateError(problems)

    merged: dict[Key, RegistryEntity] = {(kind_of(e), e.id): e for e in base_entities}
    merged.update(drafted)
    auto, notes = _cascade(drafted, merged, published_hash)

    decl = _decl(agent_id, base, merged, settings, donor)
    try:
        pinned = pin_release(AuthoringRegistry.from_entities(merged.values(), [decl]), CANDIDATE_RELEASE_ID)
    except SchemaError as exc:
        raise CandidateError([_v("REG-PIN", str(exc))]) from None

    problems.extend(locked_policy_violations([e for e in base_entities if isinstance(e, Policy)],
                                             [e for e in pinned.entities if isinstance(e, Policy)]))
    closure = {(kind_of(e), e.id) for e in pinned.entities}
    for key in _sorted_keys(set(drafted) - closure):
        problems.append(_v("REG-UNREFERENCED", "la entidad del borrador no queda en la release: nada la "
                           "referencia", _key_text(key[0].value, key[1])))

    # Solo el contenido que aporta la candidata (borrador y cascada) puede chocar con una versión publicada;
    # las entidades de la base salen del registry tal cual.
    own = set(drafted) | set(auto)
    hashes: dict[VersionRef, str] = {}
    new: list[VersionRef] = []
    docs: dict[VersionRef, VersionDocs] = {}
    for entity in [*pinned.entities, *suites]:
        ref, digest = version_ref(entity), content_hash(entity)
        hashes[ref] = digest
        stored = published_hash(ref)
        if stored is None:
            new.append(ref)
        elif stored != digest and (isinstance(entity, EvalSuite) or (kind_of(entity), entity.id) in own):
            problems.append(_v("REG-VERSION-TAKEN",
                               f"{ref} ya está publicada con otro contenido; usa otra versión", str(ref)))
        key_text = _key_text(ref.kind, ref.id)
        if key_text in draft_docs:
            docs[ref] = draft_docs[key_text]
    if problems:
        raise CandidateError(problems)

    auto_refs: list[VersionRef] = []
    for key in _sorted_keys(auto):
        ref = VersionRef(kind=key[0].value, id=key[1], version=auto[key])
        if ref in hashes:
            auto_refs.append(ref)
            docs[ref] = VersionDocs(description=f"Versión derivada de {key[1]}",
                                    rationale="Actualiza referencias a versiones nuevas de la propuesta",
                                    changelog="; ".join(dict.fromkeys(notes[key]))[:8000])
    r_hash = release_hash(pinned.release)
    c_hash = sha256_hex(canonical_bytes({
        "release_hash": r_hash,
        "versions": sorted([str(ref), digest] for ref, digest in hashes.items()),
    }))
    return Candidate(
        agent_id=agent_id, release=pinned.release, decl=decl, entities=tuple(pinned.entities),
        suites=tuple(suites), new_versions=tuple(sorted(new, key=str)), auto_bumped=tuple(auto_refs),
        docs=MappingProxyType(docs), content_hashes=MappingProxyType(hashes),
        release_hash=r_hash, candidate_hash=c_hash)
