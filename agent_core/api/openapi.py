"""`contracts/openapi.json` (M9 §10): el contrato HTTP, generado por `agentcore contracts`.

Determinista: claves ordenadas, sangría de 2, LF y salto de línea final. La app se arma sin dependencias
reales porque generar el contrato no ejecuta ningún handler."""

import json
from pathlib import Path
from typing import Any

from agent_core.api.app import ApiDeps, create_app

GENERATED_NOTICE = "GENERADO por `uv run agentcore contracts`; no editar a mano."
FILE_NAME = "openapi.json"


def _unwired() -> ApiDeps:
    none: Any = None
    return ApiDeps(
        verifier=none,
        authz=none,
        registry=none,
        uow_factory=none,
        counters=none,
        clock=none,
        ids=none,
        turns=none,
        handoffs=none,
        transcripts=none,
        denials=none,
        security=none,
    )


def render_openapi() -> str:
    document = create_app(_unwired()).openapi()
    document["x-generated"] = GENERATED_NOTICE
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_openapi(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / FILE_NAME).write_bytes(render_openapi().encode("utf-8"))  # bytes: sin traducir saltos en Windows


def check_openapi(out: Path) -> list[str]:
    """`["openapi.json"]` si falta o difiere de lo generado."""
    path = out / FILE_NAME
    if not path.is_file() or path.read_bytes() != render_openapi().encode("utf-8"):
        return [FILE_NAME]
    return []
