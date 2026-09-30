"""M9: `contracts/openapi.json` se genera con `agentcore contracts` y `--check` lo verifica (spec §10)."""

import json
from pathlib import Path

from agent_core.api.openapi import check_openapi, render_openapi, write_openapi
from agent_core.cli import main

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = {
    ("post", "/v1/runs"): "create_run",
    ("post", "/v1/sessions/{session_id}/turns"): "post_turn",
    ("get", "/v1/runs/{run_id}"): "get_run",
    ("get", "/v1/runs/{run_id}/transcript"): "get_transcript",
    ("get", "/v1/handoffs/{handoff_ref}"): "get_handoff",
    ("post", "/v1/handoffs/{handoff_ref}/resolution"): "post_resolution",
}


def test_render_is_deterministic_canonical_and_lf() -> None:
    first = render_openapi()
    assert first == render_openapi()
    assert first.endswith("\n") and "\r" not in first
    assert json.dumps(json.loads(first), indent=2, sort_keys=True, ensure_ascii=False) + "\n" == first


def test_exactly_the_routes_of_the_spec_with_their_operation_ids() -> None:
    doc = json.loads(render_openapi())
    found = {
        (method, path): op["operationId"]
        for path, item in doc["paths"].items()
        for method, op in item.items()
    }
    assert found == OPERATIONS


def test_errors_are_documented_as_problem_json_and_agent_is_a_string() -> None:
    doc = json.loads(render_openapi())
    assert "ProblemBody" in doc["components"]["schemas"]
    responses = doc["paths"]["/v1/runs"]["post"]["responses"]
    assert {"201", "401", "403", "404", "409", "422", "429"} <= set(responses)
    assert doc["components"]["schemas"]["CreateRunBody"]["properties"]["agent"]["type"] == "string"


def test_the_credentials_and_idempotency_headers_are_in_the_contract() -> None:
    doc = json.loads(render_openapi())
    names = {p["name"] for p in doc["paths"]["/v1/runs"]["post"]["parameters"]}
    assert {"authorization", "X-On-Behalf-Of", "idempotency-key"} <= names


def test_committed_openapi_is_current() -> None:
    assert check_openapi(ROOT / "contracts") == []


def test_check_detects_missing_and_drift(tmp_path: Path) -> None:
    assert check_openapi(tmp_path) == ["openapi.json"]
    write_openapi(tmp_path)
    assert check_openapi(tmp_path) == []
    (tmp_path / "openapi.json").write_text("{}\n", encoding="utf-8")
    assert check_openapi(tmp_path) == ["openapi.json"]


def test_cli_writes_and_checks_openapi(tmp_path: Path) -> None:
    assert main(["contracts", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "openapi.json").read_text(encoding="utf-8") == render_openapi()
    assert main(["contracts", "--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "openapi.json").write_text("{}\n", encoding="utf-8")
    assert main(["contracts", "--check", "--out", str(tmp_path)]) == 1
