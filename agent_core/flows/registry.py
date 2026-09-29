"""Registro de autoría: entidades con versiones, resolución de rangos y releases declaradas (M1 §3.11)."""

import os
import re
import stat
from bisect import insort
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from agent_core.domain import AgentSelector, EntityKind, Interrupt, RefSpec, RegistryEntity, Template
from agent_core.flows.paths import template_vars
from agent_core.flows.schema import MAX_ERRORS, error_message, parse_flow
from agent_core.flows.view import ENTITY_TYPES, best_version, parse_version
from agent_core.flows.violations import FlowSchemaError, Violation, clip, pointer_segment, sort_violations
from agent_core.flows.yaml_loader import MAX_BYTES, YamlError, load_yaml


class ReleaseAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: RefSpec
    aliases: list[str] = Field(default_factory=lambda: ["prod"], min_length=1)

    @field_validator("aliases")
    @classmethod
    def _aliases_have_selector_format(cls, aliases: list[str]) -> list[str]:
        for alias in aliases:
            try:  # mismo formato de alias que el selector de agente del request (M0)
                AgentSelector.model_validate({"id": "x", "alias": alias})
            except ValidationError:
                raise PydanticCustomError("alias_invalid", "alias inválido") from None
        return aliases


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


DIRS: Mapping[EntityKind, str] = MappingProxyType(
    {
        EntityKind.agent: "agents", EntityKind.flow: "flows", EntityKind.policy: "policies",
        EntityKind.template: "templates", EntityKind.prompt: "prompts", EntityKind.tool: "tools",
        EntityKind.decision_model: "decision_models", EntityKind.model_profile: "model_profiles",
        EntityKind.language_detection: "language_detection",
        EntityKind.injection_ruleset: "injection_rulesets",
    }
)

# Cotas del recorrido: el directorio del registro es entrada no confiable (M1 §3.11).
MAX_FILES_PER_DIR = 5_000  # entradas (de cualquier tipo) por carpeta; si se excede no se carga esa carpeta
MAX_FILES_TOTAL = 5_000  # archivos `.yaml` leídos en todo el registro
MAX_ENTRIES_TOTAL = 20_000  # entradas de cualquier tipo (archivos, carpetas, otras) examinadas en total
MAX_TREE_DEPTH = 8  # niveles de subcarpetas bajo cada carpeta de tipo
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: enlaces simbólicos y junctions de Windows
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)


def unsafe_name(name: str) -> str | None:
    """Motivo por el que un nombre de archivo o carpeta no se admite, o None. Cubre `..`, rutas absolutas,
    flujos alternos (`a.yaml:x`) y nombres reservados de Windows (`CON`, `NUL`, ...)."""
    if _BAD_CHARS.search(name):
        return "caracteres no admitidos en el nombre"
    if name.startswith(".") or name != name.strip() or name.endswith("."):
        return "nombre no admitido"
    if _RESERVED.match(name.split(".")[0].rstrip()) or _RESERVED.match(name.partition("@")[0].rstrip()):
        return "nombre reservado del sistema"
    return None


def _g0_01(path: str, message: str, flow: str | None = None) -> Violation:
    return Violation(rule="G0-01", flow=flow, path=clip(path, 240), message=clip(message, 240))


def _is_link(mode_and_attrs: os.stat_result) -> bool:
    attrs = getattr(mode_and_attrs, "st_file_attributes", 0)
    return stat.S_ISLNK(mode_and_attrs.st_mode) or bool(attrs & _REPARSE_POINT)


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.entries = 0
        self.exhausted = False


def _scan(root: Path, folder: str, recursive: bool, budget: _Budget) -> tuple[list[Path], list[Violation]]:
    """Archivos `.yaml` bajo `<raíz>/<carpeta>` en orden determinista, sin seguir enlaces."""
    found: list[Path] = []
    violations: list[Violation] = []
    top = root / folder
    try:
        top_stat = top.lstat()
    except OSError:
        return found, violations  # carpeta ausente: es opcional
    if _is_link(top_stat):
        return found, [_g0_01(folder, "enlaces simbólicos no admitidos")]
    if not stat.S_ISDIR(top_stat.st_mode):
        return found, violations
    stack: list[tuple[Path, int]] = [(top, 0)]
    while stack and not budget.exhausted:
        current, depth = stack.pop()
        rel_dir = current.relative_to(root).as_posix()
        try:
            with os.scandir(current) as it:
                names: list[str] = []
                for entry in it:
                    names.append(entry.name)
                    if len(names) > MAX_FILES_PER_DIR:
                        break
        except OSError:
            violations.append(_g0_01(rel_dir, "carpeta ilegible"))
            continue
        if len(names) > MAX_FILES_PER_DIR:
            msg = f"más de {MAX_FILES_PER_DIR} entradas en la carpeta; no se carga"
            violations.append(_g0_01(rel_dir, msg))
            continue
        if budget.entries + len(names) > MAX_ENTRIES_TOTAL:
            msg = f"más de {MAX_ENTRIES_TOTAL} entradas en total; se detiene la carga"
            violations.append(_g0_01(rel_dir, msg))
            budget.exhausted = True
            break
        budget.entries += len(names)
        subdirs: list[tuple[Path, int]] = []
        for name in sorted(names):
            path = current / name
            rel = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
            except OSError:
                violations.append(_g0_01(rel, "entrada ilegible"))
                continue
            if _is_link(info):
                violations.append(_g0_01(rel, "enlaces simbólicos no admitidos"))
                continue
            if stat.S_ISDIR(info.st_mode):
                if unsafe_name(name) is not None:
                    violations.append(_g0_01(rel, f"carpeta con {unsafe_name(name)}"))
                elif not recursive:
                    continue
                elif depth + 1 > MAX_TREE_DEPTH:
                    violations.append(_g0_01(rel, f"anidamiento mayor a {MAX_TREE_DEPTH} niveles"))
                else:
                    subdirs.append((path, depth + 1))
                continue
            if not name.endswith(".yaml"):
                continue  # otras extensiones se ignoran (§3.11)
            if reason := unsafe_name(name):
                violations.append(_g0_01(rel, reason))
            elif not stat.S_ISREG(info.st_mode):
                violations.append(_g0_01(rel, "solo se admiten archivos regulares"))
            elif budget.files >= MAX_FILES_TOTAL:
                msg = f"más de {MAX_FILES_TOTAL} archivos en total; se detiene la carga"
                violations.append(_g0_01(rel, msg))
                budget.exhausted = True
                break
            else:
                budget.files += 1
                found.append(path)
        stack.extend(reversed(subdirs))
    return sorted(found, key=lambda p: p.relative_to(root).as_posix()), violations


def _read_yaml(root: Path, root_resolved: Path, path: Path, rel: str) -> tuple[object, Violation | None]:
    """Lee un archivo del registro con tope de tamaño (por `stat`, antes de leerlo) y confinado a la raíz."""
    try:
        if not path.resolve().is_relative_to(root_resolved):
            return None, _g0_01(rel, "la ruta sale de la raíz del registro")
        if path.lstat().st_size > MAX_BYTES:
            return None, _g0_01(rel, "archivo mayor a 1 MiB")
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        return load_yaml(raw), None
    except YamlError as exc:
        return None, _g0_01(rel, f"YAML inválido: {clip(str(exc).splitlines()[0] if str(exc) else '', 120)}")
    except (OSError, ValueError):
        return None, _g0_01(rel, "archivo ilegible")


def _template_data(data: Mapping[str, object]) -> tuple[Mapping[str, object], str | None]:
    """Deriva `reads` de `{{ }}` y compara con lo declarado (M1 §3.3)."""
    locales = data.get("locales")
    if not isinstance(locales, dict):
        return data, None
    derived: set[str] = set()
    for locale, text in sorted(locales.items()):
        if isinstance(text, str):
            try:
                derived |= template_vars(text)
            except ValueError as exc:
                return data, f"locale {clip(locale, 40)}: {clip(exc, 120)}"
    declared = data.get("reads")
    if declared is not None and (not isinstance(declared, list) or set(map(str, declared)) != derived):
        return data, f"reads declarado distinto del derivado de la plantilla: {sorted(derived)}"
    return {**data, "reads": sorted(derived)}, None


def _prompt_problem(data: Mapping[str, object]) -> str | None:
    locales = data.get("locales")
    if isinstance(locales, dict):
        for locale, text in sorted(locales.items()):
            if isinstance(text, str) and ("{{" in text or "}}" in text):
                return f"locale {clip(locale, 40)}: un prompt no lleva variables (ADR 0016)"
    return None


def _model_violations(rel: str, exc: ValidationError) -> list[Violation]:
    """Una violación por error de Pydantic (máx. MAX_ERRORS), con mensaje fijo y sin valores de entrada."""
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    found = [
        _g0_01(
            f"{rel}#/" + "/".join(pointer_segment(str(p)) for p in err["loc"]) if err["loc"] else rel,
            error_message(err["type"]),
        )
        for err in errors[:MAX_ERRORS]
    ]
    if len(errors) > MAX_ERRORS:
        found.append(_g0_01(rel, f"se omitieron {len(errors) - MAX_ERRORS} errores más"))
    return found


def _load_file(
    reg: AuthoringRegistry, root: Path, root_resolved: Path, kind: EntityKind, path: Path
) -> list[Violation]:
    rel = path.relative_to(root).as_posix()
    stem = path.relative_to(root / DIRS[kind]).as_posix().removesuffix(".yaml")
    ident, sep, version = stem.rpartition("@")
    label = clip(f"{ident}@{version}", 160) if kind == EntityKind.flow and sep else None
    if not sep or not ident or not version:
        return [_g0_01(rel, "el nombre del archivo debe ser <id>@<versión>.yaml", label)]
    data, problem_violation = _read_yaml(root, root_resolved, path, rel)
    if problem_violation is not None:
        return [problem_violation.model_copy(update={"flow": label})]
    if not isinstance(data, dict):
        return [_g0_01(rel, "el archivo debe contener un objeto", label)]
    if data.get("id") != ident or data.get("version") != version:
        expected = f"{clip(ident, 80)}@{clip(version, 40)}"
        return [_g0_01(rel, f"id y version deben coincidir con el nombre del archivo ({expected})", label)]
    if kind == EntityKind.flow:
        try:
            reg.add(parse_flow(data, source=rel), rel)
        except FlowSchemaError as exc:
            return exc.violations
        return []
    payload: Mapping[str, object] = data
    if kind == EntityKind.template:
        payload, problem = _template_data(payload)
        if problem:
            return [_g0_01(rel, problem)]
    if kind == EntityKind.prompt and (problem := _prompt_problem(payload)):
        return [_g0_01(rel, problem)]
    try:
        entity = ENTITY_TYPES[kind].model_validate(payload)
    except ValidationError as exc:
        return _model_violations(rel, exc)
    reg.add(entity, rel)
    return []


def _load_release(reg: AuthoringRegistry, root: Path, root_resolved: Path, path: Path) -> list[Violation]:
    rel = path.relative_to(root).as_posix()
    data, problem = _read_yaml(root, root_resolved, path, rel)
    if problem is not None:
        return [problem]
    try:
        decl = ReleaseDecl.model_validate(data)
    except ValidationError as exc:
        return _model_violations(rel, exc)
    if decl.id != path.name.removesuffix(".yaml"):
        return [_g0_01(rel, "el id de la release debe coincidir con el nombre del archivo")]
    reg.add_release(decl, rel)
    return []


def load_registry(root: Path) -> tuple[AuthoringRegistry, list[Violation]]:
    """Carga `<raíz>/<tipo>/<id>@<versión>.yaml` y `releases/<id>.yaml` (M1 §3.11). Ningún archivo aborta.

    No sigue enlaces, acota tamaño, cantidad y profundidad, y no ejecuta ni importa nada: las entidades salen
    solo de la validación Pydantic de los datos cargados.
    """
    reg = AuthoringRegistry()
    violations: list[Violation] = []
    budget = _Budget()
    root_resolved = root.resolve()
    jobs = [(kind, folder, True) for kind, folder in DIRS.items()] + [(None, "releases", False)]
    for kind, folder, recursive in jobs:
        files, found = _scan(root, folder, recursive, budget)
        violations += found
        for path in files:
            try:
                if kind is None:
                    violations += _load_release(reg, root, root_resolved, path)
                else:
                    violations += _load_file(reg, root, root_resolved, kind, path)
            except Exception:  # un archivo malo nunca aborta la carga
                violations.append(_g0_01(path.relative_to(root).as_posix(), "archivo no procesable"))
    return reg, sort_violations(violations)
