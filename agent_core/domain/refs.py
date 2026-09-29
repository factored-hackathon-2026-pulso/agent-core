"""Referencias a entidades (M0 §2.2).

`RefSpec` es de autoría (admite rangos o ninguna versión). `EntityRef` es de runtime (siempre exacta).
El motor nunca resuelve rangos: el registro exige referencias exactas al cargar una release
(`require_exact_refs`).

Todos los patrones son solo ASCII y se aplican con `fullmatch` (no `$`, que admite un salto de línea final).
"""

import re
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints, model_validator

from agent_core.domain.base import (
    EXACT_VERSION_PATTERN,
    ID_PATTERN,
    NUM_PATTERN,
    EntityId,
    ExactVersion,
    Model,
)
from agent_core.domain.errors import InvalidRuntimeRef

_ID = ID_PATTERN
_NUM = NUM_PATTERN
_EXACT = EXACT_VERSION_PATTERN
_SPEC = rf"(?:{_EXACT}|[\^~]{_NUM}(?:\.{_NUM}){{0,2}}|{_NUM}(?:\.{_NUM})?)"
_ALIAS = r"[a-z][a-z0-9_-]*"
_REF_RE = re.compile(rf"(?P<id>{_ID})(?:@(?P<spec>[^@]+))?")
_SPEC_RE = re.compile(_SPEC)
_EXACT_RE = re.compile(_EXACT)
_ALIAS_RE = re.compile(_ALIAS)

Alias = Annotated[str, StringConstraints(pattern=rf"^{_ALIAS}$")]
# El patrón queda en el JSON Schema; la validación es la misma que con `fullmatch`.
VersionSpec = Annotated[str, StringConstraints(pattern=rf"^{_SPEC}$")]


class EntityKind(StrEnum):
    """Clases de entidad del registro (M0 §2.4)."""
    agent = "agent"
    flow = "flow"
    decision_model = "decision_model"
    policy = "policy"
    template = "template"
    prompt = "prompt"
    tool = "tool"
    language_detection = "language_detection"
    injection_ruleset = "injection_ruleset"
    model_profile = "model_profile"


def _split(text: str) -> tuple[str, str | None]:
    match = _REF_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"referencia inválida: {text!r}")
    return match["id"], match["spec"]


class EntityRef(Model):
    """Referencia exacta de runtime: `id@X.Y.Z`."""

    id: EntityId
    version: ExactVersion

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, _, version = data.partition("@")
            return {"id": ident, "version": version}
        return data

    @classmethod
    def parse(cls, text: str) -> "EntityRef":
        """Lanza `InvalidRuntimeRef` si la referencia no es exacta."""
        match = _REF_RE.fullmatch(text)
        spec = match["spec"] if match else None
        if match is None or spec is None or _EXACT_RE.fullmatch(spec) is None:
            raise InvalidRuntimeRef(f"referencia no exacta en runtime: {text!r}")
        return cls(id=match["id"], version=spec)

    def __str__(self) -> str:
        return f"{self.id}@{self.version}"


class RefSpec(Model):
    """Referencia de autoría: exacta, rango (`^1`, `~1.2`, `1`) o sin versión."""

    id: EntityId
    spec: VersionSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, spec = _split(data)
            return {"id": ident, "spec": spec}
        return data

    @classmethod
    def parse(cls, text: str) -> "RefSpec":
        return cls.model_validate(text)

    @property
    def is_exact(self) -> bool:
        return self.spec is not None and _EXACT_RE.fullmatch(self.spec) is not None

    def require_exact(self) -> EntityRef:
        if not self.is_exact or self.spec is None:
            raise InvalidRuntimeRef(f"referencia no exacta en runtime: {self}")
        return EntityRef(id=self.id, version=self.spec)

    def __str__(self) -> str:
        return self.id if self.spec is None else f"{self.id}@{self.spec}"


class AgentSelector(Model):
    """Selector de agente del request: `id`, `id@alias` o `id@X.Y.Z`. Sin nada, alias `prod`."""

    id: EntityId
    alias: Alias | None = None
    version: ExactVersion | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, sep, tail = data.partition("@")
            if not sep:
                return {"id": ident, "alias": "prod"}
            if _EXACT_RE.fullmatch(tail):
                return {"id": ident, "version": tail}
            if _ALIAS_RE.fullmatch(tail):
                return {"id": ident, "alias": tail}
            raise ValueError(f"selector de agente inválido: {data!r}")
        if isinstance(data, dict) and data.get("alias") is None and data.get("version") is None:
            return {**data, "alias": "prod"}
        return data

    @model_validator(mode="after")
    def _one_of(self) -> "AgentSelector":
        if (self.alias is None) == (self.version is None):
            raise ValueError("indica alias o versión, no ambos")
        return self

    @classmethod
    def parse(cls, text: str) -> "AgentSelector":
        return cls.model_validate(text)


def _children(value: object) -> Iterator[object] | None:
    if isinstance(value, BaseModel):
        return (getattr(value, name) for name in type(value).model_fields)
    if isinstance(value, Mapping):
        return iter(value.values())
    if isinstance(value, list | tuple | set | frozenset):
        return iter(value)
    return None


def iter_refspecs(value: object) -> Iterator[RefSpec]:
    """Recorre modelos, mapas y secuencias y devuelve cada `RefSpec` en orden de campos.

    Iterativo (sin límite de recursión) y tolerante a ciclos: un contenedor que ya está en el camino actual no
    se vuelve a recorrer.
    """
    stack: list[tuple[Iterator[object], int]] = []
    on_path: set[int] = set()

    def visit(item: object) -> RefSpec | None:
        if isinstance(item, RefSpec):
            return item
        children = _children(item)
        if children is not None and id(item) not in on_path:
            on_path.add(id(item))
            stack.append((children, id(item)))
        return None

    found = visit(value)
    if found is not None:
        yield found
    while stack:
        children, ident = stack[-1]
        for child in children:
            found = visit(child)
            if found is not None:
                yield found
            break
        else:
            stack.pop()
            on_path.discard(ident)


def require_exact_refs(value: object) -> None:
    """Lanza `InvalidRuntimeRef` ante la primera referencia no exacta (M0 §2.2, §13.11)."""
    for ref in iter_refspecs(value):
        ref.require_exact()
