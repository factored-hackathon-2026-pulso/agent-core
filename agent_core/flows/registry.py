"""Registro de autoría: entidades con versiones, resolución de rangos y releases declaradas (M1 §3.11)."""

from bisect import insort
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_core.domain import EntityKind, Interrupt, RefSpec, RegistryEntity, Template
from agent_core.flows.paths import template_vars
from agent_core.flows.view import ENTITY_TYPES, best_version, parse_version


class ReleaseAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: RefSpec
    aliases: list[str] = Field(default_factory=lambda: ["prod"], min_length=1)


class ReleaseDecl(BaseModel):
    """Release de autoría (`releases/<id>.yaml`). `pin_release` la convierte en una `Release` exacta."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    agents: list[ReleaseAgent] = Field(min_length=1)
    flows: list[RefSpec] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: RefSpec
    injection_ruleset: RefSpec | None = None
    max_input_chars: PositiveInt = 4000


def kind_of(entity: RegistryEntity) -> EntityKind:
    for kind, model in ENTITY_TYPES.items():
        if isinstance(entity, model):
            return kind
    raise TypeError(f"{type(entity).__name__} no es una entidad del registro")


def derive_template_reads(template: Template) -> Template:
    """`reads` = rutas de `{{ }}` de todos los locales (M1 §3.3). Lanza ValueError si está mal formada."""
    reads: set[str] = set()
    for text in template.locales.values():
        reads |= template_vars(text)
    return template.model_copy(update={"reads": frozenset(reads)})


class AuthoringRegistry:
    """Implementa `RegistryView` sobre entidades de autoría (referencias con rangos)."""

    def __init__(self) -> None:
        self._entities: dict[EntityKind, dict[str, dict[str, RegistryEntity]]] = {}
        self._sources: dict[tuple[EntityKind, str, str], str] = {}
        self._releases: dict[str, ReleaseDecl] = {}
        # Versiones ordenadas por precedencia semver, mantenidas al agregar (sin re-ordenar por consulta).
        self._sorted: dict[tuple[EntityKind, str], list[str]] = {}

    @classmethod
    def from_entities(
        cls, entities: Iterable[RegistryEntity], releases: Iterable[ReleaseDecl] = ()
    ) -> "AuthoringRegistry":
        reg = cls()
        for entity in entities:
            reg.add(entity)
        for decl in releases:
            reg.add_release(decl)
        return reg

    def add(self, entity: RegistryEntity, source: str | None = None) -> None:
        if isinstance(entity, Template):
            entity = derive_template_reads(entity)
        kind = kind_of(entity)
        ident, version = str(getattr(entity, "id")), str(getattr(entity, "version"))  # noqa: B009
        by_version = self._entities.setdefault(kind, {}).setdefault(ident, {})
        if version not in by_version:
            insort(self._sorted.setdefault((kind, ident), []), version, key=parse_version)
        by_version[version] = entity
        if source is not None:
            self._sources[(kind, ident, version)] = source

    def add_release(self, decl: ReleaseDecl, source: str | None = None) -> None:
        self._releases[decl.id] = decl

    def versions(self, kind: EntityKind, ident: str) -> list[str]:
        return list(self._sorted.get((kind, ident), ()))

    def get_exact(self, kind: EntityKind, ident: str, version: str) -> RegistryEntity:
        return self._entities[kind][ident][version]

    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        """Nunca lanza: una referencia o rango no parseable resuelve a None."""
        best = best_version(ref.spec, self._sorted.get((kind, ref.id), ()))
        return None if best is None else self._entities[kind][ref.id][best]

    def all(self, kind: EntityKind) -> list[RegistryEntity]:
        by_id = self._entities.get(kind, {})
        return [by_id[ident][v] for ident in sorted(by_id) for v in self._sorted[(kind, ident)]]

    def releases(self) -> list[ReleaseDecl]:
        return [self._releases[rid] for rid in sorted(self._releases)]

    def release(self, release_id: str) -> ReleaseDecl | None:
        return self._releases.get(release_id)

    def source(self, kind: EntityKind, ident: str, version: str) -> str | None:
        return self._sources.get((kind, ident, version))
