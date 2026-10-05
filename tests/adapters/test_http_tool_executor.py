"""`HttpToolExecutor` against a fake tool service (httpx MockTransport; synthetic data, no network)."""

import json
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider

from agent_core.adapters.tools import HttpToolExecutor
from agent_core.adapters.tools.http_executor import from_env
from agent_core.domain import EntityRef, SchemaError, ToolDef
from agent_core.ports import ToolCallContext, ToolStatus
from testing.builders import advisor_with_delegation, principal
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry

BASE = "https://tools.test"
TOKEN = "tool-token-SECRETO-0001"
READ = EntityRef.parse("leer_productos@1.0.0")
WRITE = EntityRef.parse("radicar_pqr@1.0.0")
SECRET_ARG = "CONTENIDO-SENSIBLE-DEL-CLIENTE"


def _defs() -> list[ToolDef]:
    return [
        ToolDef.model_validate({"id": "leer_productos", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True, "source": "productos"}),
        ToolDef.model_validate({"id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible",
                                "min_auth_level": "step_up", "idempotent": True,
                                "readback_by": "idempotency_key", "source": "pqr"}),
    ]


class Service:
    """Records what the executor sends and answers with whatever the test scripts."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json={"status": "ok", "result": {"saldo": "10.50"}, "source": "productos"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)  # type: ignore[no-any-return]


@pytest.fixture
def service() -> Service:
    return Service()


@pytest.fixture
def executor(service: Service) -> HttpToolExecutor:
    registry = InMemoryRegistry()
    registry.add(*_defs())
    client = httpx.Client(transport=httpx.MockTransport(service))
    return HttpToolExecutor(registry, FakeIds(), BASE, TOKEN, client=client)


def _ctx(level: str = "step_up", *, advisor: bool = False) -> ToolCallContext:
    if advisor:
        who, grant = advisor_with_delegation()
        return ToolCallContext(run_id="run-1", release="rel-1", principal=who, on_behalf_of=grant,
                               subject=grant.subject, turn_id="turn-1")
    return ToolCallContext(run_id="run-1", release="rel-1",
                           principal=principal(auth={"level": level, "at": "2026-09-28T12:00:00Z"}),
                           turn_id="turn-1")


def test_a_read_posts_the_call_and_returns_the_service_result(executor: HttpToolExecutor,
                                                              service: Service) -> None:
    result = executor.execute(READ, {"limite": 3}, {"customer_id": "cust-001"}, _ctx("session"))

    assert result.status is ToolStatus.ok
    assert result.result_full == {"saldo": "10.50"} and result.source == "productos"
    request = service.requests[0]
    assert str(request.url) == f"{BASE}/v1/tools/leer_productos/execute"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert service.body["args"] == {"limite": 3}
    assert service.body["bound_params"] == {"customer_id": "cust-001"}
    assert service.body["idempotency_key"] is None
    assert service.body["context"]["call_id"] == result.call_id


def test_the_context_carries_verified_claims_and_the_delegation_never_a_token(
        executor: HttpToolExecutor, service: Service) -> None:
    executor.execute(READ, {}, {"customer_id": "cust-001"}, _ctx(advisor=True))

    context = service.body["context"]
    assert context["principal"]["type"] == "advisor" and context["principal"]["id"] == "adv-7"
    assert context["on_behalf_of"]["grant_ref"] == "grant-9"
    assert context["on_behalf_of"]["subject"] == {"kind": "customer", "ref": "cust-001"}
    assert context["run_id"] == "run-1" and context["turn_id"] == "turn-1"
    raw = service.requests[0].content.decode()
    assert "jws" not in raw.lower() and "eyJ" not in raw


def test_decimals_in_args_travel_exactly(executor: HttpToolExecutor, service: Service) -> None:
    executor.execute(WRITE, {"monto": Decimal("500.00")}, {}, _ctx(), idempotency_key="action-1")

    assert b'"monto":500.00' in service.requests[0].content
    assert service.body["idempotency_key"] == "action-1"


def test_a_write_without_idempotency_key_is_a_programming_error(executor: HttpToolExecutor) -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        executor.execute(WRITE, {}, {}, _ctx())


def test_below_the_required_level_it_asks_for_step_up_without_calling_the_service(
        executor: HttpToolExecutor, service: Service) -> None:
    result = executor.execute(WRITE, {}, {}, _ctx("session"), idempotency_key="action-1")

    assert result.status is ToolStatus.step_up_required and result.required_level == "step_up"
    assert service.requests == []


@pytest.mark.parametrize("status", ["denied", "step_up_required"])
def test_the_service_may_deny_or_ask_for_step_up(executor: HttpToolExecutor, service: Service,
                                                 status: str) -> None:
    service.answer = lambda r: httpx.Response(200, json={"status": status, "result": {"leak": 1},
                                                         "error": {"kind": "x", "message": "no"}})

    result = executor.execute(READ, {}, {}, _ctx("session"))

    assert result.status is ToolStatus(status) and result.result_full is None


def test_a_read_that_cannot_reach_the_service_is_error_or_timeout(executor: HttpToolExecutor,
                                                                  service: Service) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    service.answer = boom
    assert executor.execute(READ, {}, {}, _ctx("session")).status is ToolStatus.error

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    service.answer = slow
    assert executor.execute(READ, {}, {}, _ctx("session")).status is ToolStatus.timeout


@pytest.mark.parametrize("answer", [
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    lambda r: httpx.Response(500, json={"status": "error"}),
    lambda r: httpx.Response(200, content=b"<html>"),
    lambda r: httpx.Response(200, json={"status": "error", "error": {"message": "x"}}),
    lambda r: httpx.Response(200, json={"status": "bogus"}),
    lambda r: httpx.Response(401, json={"error": "token"}),
], ids=["connect", "timeout", "5xx", "html", "error-status", "unknown-status", "unauthorized"])
def test_a_write_that_fails_in_transit_is_uncertain_never_error(executor: HttpToolExecutor,
                                                                service: Service, answer: Any) -> None:
    service.answer = answer

    result = executor.execute(WRITE, {"a": 1}, {}, _ctx(), idempotency_key="action-1")

    assert result.status is ToolStatus.uncertain and result.result_full is None


@pytest.mark.parametrize("answer", [
    lambda r: httpx.Response(500, json={"status": "error"}),
    lambda r: httpx.Response(200, content=b"not json"),
    lambda r: httpx.Response(200, json=[1, 2]),
    lambda r: httpx.Response(200, json={"status": "uncertain"}),  # a read cannot be uncertain
    lambda r: httpx.Response(403, json={}),
], ids=["5xx", "html", "list", "uncertain-on-read", "forbidden"])
def test_a_read_with_an_unusable_answer_is_error(executor: HttpToolExecutor, service: Service,
                                                 answer: Any) -> None:
    service.answer = answer

    assert executor.execute(READ, {}, {}, _ctx("session")).status is ToolStatus.error


def test_a_replayed_write_returns_the_service_result(executor: HttpToolExecutor, service: Service) -> None:
    service.answer = lambda r: httpx.Response(200, json={"status": "ok", "result": {"radicado": "R-1"},
                                                         "source": "pqr"})
    first = executor.execute(WRITE, {"a": 1}, {}, _ctx(), idempotency_key="action-1")
    again = executor.execute(WRITE, {"a": 1}, {}, _ctx(), idempotency_key="action-1")

    assert first.result_full == again.result_full == {"radicado": "R-1"}
    assert first.call_id != again.call_id
    assert [json.loads(r.content)["idempotency_key"] for r in service.requests] == ["action-1"] * 2


def test_nothing_sensitive_reaches_the_log_or_the_error(executor: HttpToolExecutor, service: Service,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    service.answer = lambda r: httpx.Response(500, text=f"boom {SECRET_ARG} {TOKEN}")
    caplog.set_level(logging.DEBUG)

    result = executor.execute(READ, {"nota": SECRET_ARG}, {"customer_id": "cust-001"}, _ctx("session"))

    shown = caplog.text + (result.error or "")
    assert SECRET_ARG not in shown and TOKEN not in shown and "cust-001" not in shown


def test_the_definition_comes_from_the_registry(executor: HttpToolExecutor) -> None:
    assert executor.definition(WRITE).readback_by == "idempotency_key"


def test_the_factory_wants_url_and_token_together_and_valid() -> None:
    registry, ids = InMemoryRegistry(), FakeIds()
    assert from_env(registry, ids, {"AGENTCORE_TOOL_SERVICE_URL": "http://tools:8080",
                                    "AGENTCORE_TOOL_SERVICE_TOKEN": "t"}) is not None
    for env in ({}, {"AGENTCORE_TOOL_SERVICE_URL": "http://tools:8080"},
                {"AGENTCORE_TOOL_SERVICE_TOKEN": "t"},
                {"AGENTCORE_TOOL_SERVICE_URL": "ftp://x", "AGENTCORE_TOOL_SERVICE_TOKEN": "t"},
                {"AGENTCORE_TOOL_SERVICE_URL": "http://x", "AGENTCORE_TOOL_SERVICE_TOKEN": "t",
                 "AGENTCORE_TOOL_SERVICE_TIMEOUT_S": "0"}):
        with pytest.raises(SchemaError):
            from_env(registry, ids, env)


def test_a_step_up_older_than_max_auth_age_asks_again_without_calling_the_service(
        service: Service) -> None:
    registry = InMemoryRegistry()
    aged = ToolDef.model_validate({"id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible",
                                   "min_auth_level": "step_up", "max_auth_age": "PT5M", "idempotent": True,
                                   "readback_by": "idempotency_key"})
    registry.add(aged)
    executor = HttpToolExecutor(registry, FakeIds(), BASE, TOKEN,
                                client=httpx.Client(transport=httpx.MockTransport(service)))
    stepped = principal(auth={"level": "step_up", "at": "2026-09-28T12:00:00Z"})

    def at(instant: str | None) -> ToolCallContext:
        return ToolCallContext(run_id="run-1", release="rel-1", principal=stepped, turn_id="turn-1",
                               at=instant)

    old = executor.execute(WRITE, {}, {}, at("2026-09-28T12:06:00Z"), idempotency_key="action-1")
    unknown = executor.execute(WRITE, {}, {}, at(None), idempotency_key="action-1")  # fails closed
    assert old.status is ToolStatus.step_up_required and old.required_level == "step_up"
    assert unknown.status is ToolStatus.step_up_required and service.requests == []

    fresh = executor.execute(WRITE, {}, {}, at("2026-09-28T12:04:00Z"), idempotency_key="action-1")
    assert fresh.status is not ToolStatus.step_up_required and len(service.requests) == 1


@pytest.mark.parametrize(("error", "kept"), [
    ({"kind": "not_own_subject", "message": f"la cuenta {SECRET_ARG} es de otro cliente"}, "not_own_subject"),
    ({"kind": "Nombre Apellido 123", "message": "x"}, "tool_error"),
    ({"message": f"sin kind {SECRET_ARG}"}, "tool_error"),
    (f"texto plano {SECRET_ARG}", "tool_error"),
], ids=["kind", "kind-not-a-code", "no-kind", "string"])
def test_only_the_error_kind_is_kept_never_the_service_message(executor: HttpToolExecutor, service: Service,
                                                               error: object, kept: str) -> None:
    service.answer = lambda r: httpx.Response(200, json={"status": "denied", "error": error})

    result = executor.execute(READ, {}, {}, _ctx("session"))

    assert result.status is ToolStatus.denied and result.error == kept


def test_trace_context_is_propagated_to_the_service(executor: HttpToolExecutor, service: Service) -> None:
    tracer = TracerProvider().get_tracer("prueba")
    with tracer.start_as_current_span("turno") as span:
        executor.execute(READ, {}, {}, _ctx("session"))
        trace_id = format(span.get_span_context().trace_id, "032x")
    assert trace_id in service.requests[0].headers["traceparent"]
