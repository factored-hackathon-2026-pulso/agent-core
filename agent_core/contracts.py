"""Generación de `contracts/` (ADR 0002): JSON Schema de tipos públicos, eventos y nodos + VERSION.

Determinista: claves ordenadas, sangría de 2, LF y salto de línea final, sin datos del entorno. Solo
gestiona `contracts/VERSION` y `contracts/schemas/`; otros archivos (p. ej. `openapi.json`, M9) no se tocan.
"""

import enum
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, TypeAdapter

from agent_core import domain as d
from agent_core import ports as p

GENERATED_NOTICE = "GENERADO por `uv run agentcore contracts`; no editar a mano."
_INFRA_BASES = frozenset({"Model", "MutableModel"})
_SCHEMAS = "schemas"


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
    return files


def _existing(out: Path) -> set[str]:
    """Archivos gestionados presentes: todo lo que cuelga de `schemas/` (cualquier nombre) y `VERSION`."""
    found: set[str] = set()
    schemas = out / _SCHEMAS
    if schemas.is_dir():
        found |= {f"{_SCHEMAS}/{q.name}" for q in schemas.iterdir()}
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
