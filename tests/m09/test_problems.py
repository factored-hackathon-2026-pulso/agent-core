"""M9 F1: errores `application/problem+json` con `trace_id` (T-M9-13, spec §3.5)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent_core.api.problems import PROBLEM_TITLES, install_error_handlers
from agent_core.api.tracing import install_tracing
from agent_core.domain import PROBLEM_STATUS, CredentialsInvalid, DomainError, EngineError, ProblemCode
from testing.fakes.ids import FakeIds

PROBLEM_KEYS = {"type", "title", "status", "code", "detail", "trace_id"}


class _Body(BaseModel):
    n: int


def _client() -> TestClient:
    app = FastAPI()
    install_tracing(app, FakeIds())
    install_error_handlers(app)

    @app.get("/engine/{code}")
    def engine(code: ProblemCode) -> None:
        raise EngineError(code, "detalle publico")

    @app.get("/creds")
    def creds() -> None:
        raise CredentialsInvalid("firma rota: token-super-secreto")

    @app.get("/domain")
    def domain() -> None:
        raise DomainError("interno: tabla runs")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secreto-interno-xyz")

    @app.post("/body")
    def body(b: _Body) -> None:
        return None

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app, raise_server_exceptions=False)


def _assert_problem(resp, status: int, code: str) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert set(body) == PROBLEM_KEYS
    assert body["status"] == status
    assert body["code"] == code
    assert body["trace_id"]
    return body


@pytest.mark.parametrize("code", list(ProblemCode))
def test_engine_error_maps_every_code(code: ProblemCode) -> None:
    resp = _client().get(f"/engine/{code.value}")
    body = _assert_problem(resp, PROBLEM_STATUS[code], code.value)
    assert body["title"] == PROBLEM_TITLES[code]
    if code is not ProblemCode.internal_error:
        assert body["detail"] == "detalle publico"


def test_every_code_has_a_title() -> None:
    assert set(PROBLEM_TITLES) == set(ProblemCode)


def test_internal_error_from_engine_hides_detail() -> None:
    body = _assert_problem(_client().get("/engine/internal_error"), 500, "internal_error")
    assert body["detail"] == ""


def test_credentials_invalid_never_echoes_the_message() -> None:
    resp = _client().get("/creds")
    body = _assert_problem(resp, 401, "credentials_invalid")
    assert "token-super-secreto" not in resp.text
    assert body["detail"] == ""


def test_unexpected_errors_are_500_without_internals() -> None:
    for path in ("/boom", "/domain"):
        resp = _client().get(path)
        body = _assert_problem(resp, 500, "internal_error")
        assert body["detail"] == ""
        assert "secreto-interno" not in resp.text
        assert "tabla runs" not in resp.text


def test_invalid_body_is_422_without_echoing_input() -> None:
    resp = _client().post("/body", json={"n": "valor-sensible-123"})
    body = _assert_problem(resp, 422, "invalid_request")
    assert "valor-sensible-123" not in resp.text
    assert "n" in body["detail"]


def test_unknown_route_is_404_problem() -> None:
    _assert_problem(_client().get("/no-existe"), 404, "not_found")


def test_trace_id_is_unique_per_request_and_uses_the_id_source() -> None:
    client = _client()
    first = client.get("/no-existe").json()["trace_id"]
    second = client.get("/no-existe").json()["trace_id"]
    assert first != second
    assert first == "event-0001"
