"""Vista del registro para la validación (M1 §2, §3.11)."""

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Protocol

from agent_core.domain import (
    Agent,
    DecisionModelDef,
    DomainError,
    EntityKind,
    EntityRef,
    Flow,
    InjectionRuleset,
    LanguageDetection,
    ModelProfile,
    Policy,
    Prompt,
    RefSpec,
    RegistryEntity,
    Release,
    Template,
    ToolDef,
)
from agent_core.ports import RegistryPort

ENTITY_TYPES: Mapping[EntityKind, type[RegistryEntity]] = MappingProxyType(
    {
        EntityKind.agent: Agent, EntityKind.flow: Flow, EntityKind.decision_model: DecisionModelDef,
        EntityKind.policy: Policy, EntityKind.template: Template, EntityKind.prompt: Prompt,
        EntityKind.tool: ToolDef, EntityKind.language_detection: LanguageDetection,
        EntityKind.injection_ruleset: InjectionRuleset, EntityKind.model_profile: ModelProfile,
    }
)


class RegistryView(Protocol):
    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        """La entidad a la que apunta `ref`, o None si no existe. Nunca lanza."""
        ...


# Solo ASCII, sin ceros a la izquierda y acotado: el texto del registro no es de confianza y `int()` de
# una cadena gigante lanza ValueError (o cuesta CPU). 18 dígitos sobran para una versión real.
_NUM = r"(?:0|[1-9][0-9]{0,17})"
_VERSION_RE = re.compile(rf"({_NUM})\.({_NUM})\.({_NUM})")
_SPEC_RE = re.compile(rf"([\^~]?)({_NUM})(?:\.({_NUM}))?(?:\.({_NUM}))?")


def parse_version(version: str) -> tuple[int, int, int]:
    """`X.Y.Z` exacta. Lanza únicamente `ValueError` si el texto no es una versión exacta."""
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"versión inválida: {version[:40]!r}")
    return (int(match[1]), int(match[2]), int(match[3]))


def _try_version(version: str) -> tuple[int, int, int] | None:
    try:
        return parse_version(version)
    except ValueError:
        return None


def satisfies(spec: str | None, version: str) -> bool:
    """Semántica de M1 §3.11: sin versión, exacta, `^`/`X` (mismo mayor), `~`/`X.Y` (mismo menor).

    Es total: devuelve False (nunca lanza) si `spec` o `version` no son parseables.
    """
    current = _try_version(version)
    if current is None:
        return False
    if spec is None:
        return True
    match = _SPEC_RE.fullmatch(spec)
    if match is None:
        return False
    prefix = match[1]
    parts = [int(p) for p in match.groups()[1:] if p is not None]
    if prefix:
        floor = [*parts, 0, 0][:3]
        base = (floor[0], floor[1], floor[2])
        if prefix == "^" or len(parts) == 1:
            return current[0] == base[0] and current >= base
        return current[:2] == base[:2] and current >= base
    if len(parts) == 3:
        return current == (parts[0], parts[1], parts[2])
    if len(parts) == 1:
        return current[0] == parts[0]
    return current[:2] == (parts[0], parts[1])


def best_version(spec: str | None, versions: Iterable[str]) -> str | None:
    """La versión más alta (precedencia semver) que cumple `spec`; ignora versiones no parseables."""
    parsed = [(v, t) for v in versions if (t := _try_version(v)) is not None and satisfies(spec, v)]
    return max(parsed, key=lambda pair: pair[1])[0] if parsed else None


class _ReleaseView:
    """Vista de runtime: cada id tiene la versión exacta que fija la release."""

    def __init__(self, port: RegistryPort, release: Release) -> None:
        self._port = port
        self._release = release

    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        version = self._release.entities.get(kind, {}).get(ref.id)
        if version is None or not satisfies(ref.spec, version):
            return None
        try:
            entity = self._port.get(EntityRef(id=ref.id, version=version), ENTITY_TYPES[kind])
        except (KeyError, LookupError, TypeError, DomainError):
            return None
        return entity


def release_view(port: RegistryPort, release: Release) -> RegistryView:
    return _ReleaseView(port, release)
