"""`contracts/registry-openapi.json`: la superficie HTTP del registry para otros equipos."""

import json
from pathlib import Path
from typing import Any

from agent_core.composition.registry_openapi import check_registry_openapi, render_registry_openapi

DOC: dict[str, Any] = json.loads(render_registry_openapi())
OPERATIONS = [(path, method) for path, item in DOC["paths"].items() for method in item]


def test_the_committed_file_is_current() -> None:
    assert check_registry_openapi(Path("contracts")) == []


def test_only_registry_routes_are_published() -> None:
    assert DOC["paths"] and all(p.startswith("/v1/registry/") for p in DOC["paths"])


def test_every_operation_requires_the_bearer_scheme() -> None:
    assert DOC["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert all(DOC["paths"][p][m].get("security") == [{"bearerAuth": []}] for p, m in OPERATIONS)


def test_every_operation_documents_a_success_schema_and_problem_errors() -> None:
    for path, method in OPERATIONS:
        responses = DOC["paths"][path][method]["responses"]
        ok = [r for code, r in responses.items() if code.startswith("2")]
        assert ok and all("application/json" in r.get("content", {}) for r in ok), (path, method)
        for code in ("401", "403"):
            assert "application/problem+json" in responses[code]["content"], (path, method, code)


def test_writes_declare_the_idempotency_key_header() -> None:
    def names(path: str, method: str) -> dict[str, Any]:
        return {p["name"]: p for p in DOC["paths"][path][method].get("parameters", [])}

    for path, method in [("/v1/registry/proposals", "post"), ("/v1/registry/proposals/{pid}/draft", "put"),
                         ("/v1/registry/proposals/{pid}/freeze", "post"),
                         ("/v1/registry/proposals/{pid}/reopen", "post"),
                         ("/v1/registry/proposals/{pid}/evaluate", "post")]:
        header = names(path, method)["idempotency-key"]
        branches = header["schema"].get("anyOf", [header["schema"]])
        assert not header.get("required"), (path, method)
        assert any(b.get("maxLength") == 255 for b in branches), (path, method)
    publish = names("/v1/registry/proposals/{pid}/publish", "post")["idempotency-key"]
    assert publish["required"] is True


def test_generation_is_deterministic() -> None:
    assert render_registry_openapi() == render_registry_openapi()
