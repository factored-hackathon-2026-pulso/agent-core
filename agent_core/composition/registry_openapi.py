"""`contracts/registry-openapi.json`: la superficie HTTP de `/v1/registry` para otros equipos.

Se genera con la extensión montada sobre un servicio sin cablear (generar no ejecuta ningún handler). Va
aparte de `openapi.json` (runtime): otra superficie, audiencia y credencial (spec del registry §8)."""

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from agent_core.domain import Principal
from agent_core.registry.http import registry_extension

GENERATED_NOTICE = "GENERADO por `uv run agentcore contracts`; no editar a mano."
FILE_NAME = "registry-openapi.json"


def _unauthenticated(_request: Request, _authorization: str | None) -> Principal:
    raise NotImplementedError("el contrato no ejecuta handlers")


def render_registry_openapi() -> str:
    app = FastAPI(title="agent-core registry", version="1.0.0",
                  description="Gobierno de artefactos: propuestas, evaluación, aprobación y publicación. "
                              "Solo principales `builder` (spec del registry §8); aprobar, publicar, "
                              "promover y revocar exigen una persona con step-up.")
    unwired: Any = None
    registry_extension(unwired)(app, _unauthenticated)
    document = app.openapi()
    document["components"].setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWS Ed25519 (m09 §3.8)"}
    for item in document["paths"].values():
        for operation in item.values():
            operation["security"] = [{"bearerAuth": []}]
            for param in operation.get("parameters", []):
                if param["in"] == "header" and param["name"] == "authorization":
                    operation["parameters"].remove(param)  # lo declara bearerAuth
                    break
    document["x-generated"] = GENERATED_NOTICE
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_registry_openapi(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / FILE_NAME).write_bytes(render_registry_openapi().encode("utf-8"))


def check_registry_openapi(out: Path) -> list[str]:
    path = out / FILE_NAME
    if not path.is_file() or path.read_bytes() != render_registry_openapi().encode("utf-8"):
        return [FILE_NAME]
    return []
