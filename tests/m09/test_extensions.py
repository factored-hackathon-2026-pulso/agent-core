"""Extensiones de `create_app`: montan rutas propias reusando la autenticación de M9."""

from dataclasses import replace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.api.app import Authenticate, create_app
from tests.m09.conftest import api_deps


def test_extension_receives_app_and_authenticator() -> None:
    seen: list[str] = []

    def ext(app: FastAPI, authenticate: Authenticate) -> None:
        @app.get("/v1/ext/whoami")
        def whoami(request: Request) -> dict[str, str | None]:
            principal = authenticate(request, request.headers.get("authorization"))
            seen.append(principal.id or "")
            return {"id": principal.id}

    deps, credential = api_deps()
    client = TestClient(create_app(replace(deps, extensions=(ext,))), raise_server_exceptions=False)
    assert client.get("/v1/ext/whoami", headers={"authorization": credential}).json()["id"] == seen[0]
    assert client.get("/v1/ext/whoami").status_code == 401


def test_no_extensions_by_default() -> None:
    deps, _ = api_deps()
    assert deps.extensions == ()
