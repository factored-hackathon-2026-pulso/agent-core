"""Generación de `contracts/` (ADR 0002): JSON Schema de tipos públicos, eventos y nodos + VERSION.

Determinista: claves ordenadas, sangría de 2, LF y salto de línea final, sin datos del entorno. Solo
gestiona `contracts/VERSION`, `contracts/schemas/`, `contracts/events/` (eventos salientes) y
`contracts/registry/` (cuerpos y modelos del registry); otros archivos
(p. ej. `openapi.json`, M9) no se tocan.
"""

import enum
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, TypeAdapter

from agent_core import domain as d
from agent_core import outbound as o
from agent_core import ports as p
from agent_core.registry import models as registry_models
from agent_core.registry.http import REQUEST_BODIES
from agent_core.registry.suite import EvalSuite

GENERATED_NOTICE = "GENERADO por `uv run agentcore contracts`; no editar a mano."
_INFRA_BASES = frozenset({"Model", "MutableModel"})
_SCHEMAS = "schemas"
_EVENTS = "events"
_REGISTRY = "registry"


def _is_schema_type(obj: object) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseModel | enum.Enum)


def _collect() -> dict[str, Any]:
    """Todo modelo/enum exportado por `domain` y `ports`, más los alias discriminados públicos."""
    found: dict[str, Any] = {"AnyEvent": d.AnyEvent, "Node": d.Node}
    for module in (d, p):
        for name in module.__all__:
            obj = getattr(module, name)
            if _is_schema_type(obj) and name not in _INFRA_BASES:
                found[name] = obj
    return dict(sorted(found.items()))


PUBLIC_TYPES: Mapping[str, Any] = MappingProxyType(_collect())


def _registry_types() -> tuple[dict[str, Any], dict[str, Any]]:
    """(entradas, salidas) del registry (N-01): cuerpos de request, `EvalSuite`, `EntityDraft` y
    `VersionDocs`; y el resto de los modelos de `registry.models`, que son las respuestas."""
    inputs: dict[str, Any] = {"EvalSuite": EvalSuite, **REQUEST_BODIES}
    outputs: dict[str, Any] = {}
    for name, obj in vars(registry_models).items():
        if _is_schema_type(obj) and obj.__module__ == registry_models.__name__ and name != "RegModel":
            (inputs if name in {"EntityDraft", "VersionDocs", "ReleaseSettings"} else outputs)[name] = obj
    return dict(sorted(inputs.items())), dict(sorted(outputs.items()))


def _encode(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_contracts() -> dict[str, str]:
    """Ruta relativa (con `/`) → contenido. `mode="validation"` y alias de cable (`await`, no `await_`)."""
    files: dict[str, str] = {}
    for name, tp in PUBLIC_TYPES.items():
        schema = TypeAdapter(tp).json_schema(mode="validation", by_alias=True)
        schema["$comment"] = GENERATED_NOTICE
        files[f"{_SCHEMAS}/{name}.json"] = _encode(schema)
    files["VERSION"] = d.SCHEMA_VERSION + "\n"
    files.update(_render_events())
    files.update(_render_registry())
    return files


def _render_registry() -> dict[str, str]:
    inputs, outputs = _registry_types()
    files: dict[str, str] = {}
    for mode, group in (("validation", inputs), ("serialization", outputs)):
        for name, tp in group.items():
            schema = TypeAdapter(tp).json_schema(mode=mode, by_alias=True)  # type: ignore[arg-type]
            schema["$comment"] = GENERATED_NOTICE
            files[f"{_REGISTRY}/{name}.json"] = _encode(schema)
    return files


def _render_events() -> dict[str, str]:
    """Contrato de eventos salientes (`agent_core.outbound`, fuera de M0): esquema de la unión y catálogo."""
    schema = TypeAdapter(o.OutboundEvent).json_schema(mode="validation", by_alias=True)
    schema["$comment"] = GENERATED_NOTICE
    catalog = {
        "$comment": GENERATED_NOTICE,
        "spec_version": o.SPEC_VERSION,
        "schema": "OutboundEvent.json",
        "types": list(o.PUBLIC_TYPES),
    }
    return {f"{_EVENTS}/OutboundEvent.json": _encode(schema), f"{_EVENTS}/catalog.json": _encode(catalog)}


def _existing(out: Path) -> set[str]:
    """Archivos gestionados presentes: todo lo que cuelga de `schemas/` (cualquier nombre) y `VERSION`."""
    found: set[str] = set()
    schemas = out / _SCHEMAS
    if schemas.is_dir():
        found |= {f"{_SCHEMAS}/{q.name}" for q in schemas.iterdir()}
    for folder in (_EVENTS, _REGISTRY):
        directory = out / folder
        if directory.is_dir():
            found |= {f"{folder}/{q.name}" for q in directory.iterdir()}
    if (out / "VERSION").exists():
        found.add("VERSION")
    return found


def _target(out: Path, rel: str) -> Path:
    path = (out / rel).resolve()
    if not path.is_relative_to(out.resolve()):  # defensa en profundidad: los nombres ya son identificadores
        raise ValueError(f"ruta fuera de {out}: {rel}")
    return path


def write_contracts(out: Path) -> None:
    files = render_contracts()
    for stale in _existing(out) - set(files):
        stale_path = _target(out, stale)
        if stale_path.is_file():
            stale_path.unlink()
    for rel, content in files.items():
        path = _target(out, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))  # bytes: sin traducción de saltos de línea en Windows


def check_contracts(out: Path) -> list[str]:
    """Rutas que difieren, faltan o sobran respecto de lo generado."""
    files = render_contracts()
    diffs = sorted(_existing(out) - set(files))
    for rel, content in files.items():
        path = out / rel
        if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
            diffs.append(rel)
    return sorted(diffs)
