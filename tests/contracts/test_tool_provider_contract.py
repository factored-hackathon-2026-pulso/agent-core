"""`HttpToolExecutor` against the tool-provider contract (pinned copy of `tool-service/contracts`, 1.0.0).

The contract is shared by every service that exposes tools to agent-core. The pinned files are copied from the
tool-service repo (`contracts/tool-provider.openapi.json` and `tool-provider-version.txt`); to take a new
version, copy both files here and run this test: it names what the client has to follow.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from agent_core.adapters.tools import HttpToolExecutor
from agent_core.domain import EntityRef, ToolDef
from agent_core.ports import ToolCallContext, ToolStatus
from testing.builders import advisor_with_delegation, principal
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry

HERE = Path(__file__).parent
DOC = json.loads((HERE / "tool-provider-openapi.json").read_text(encoding="utf-8"))
VERSION = (HERE / "tool-provider-contract-version.txt").read_text(encoding="utf-8").strip()
READ = EntityRef.parse("leer_productos@1.0.0")
WRITE = EntityRef.parse("radicar_pqr@1.0.0")


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": DOC["components"]})


def _defs() -> list[ToolDef]:
    return [
        ToolDef.model_validate({"id": "leer_productos", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True,
                                "source": "customer_products"}),
        ToolDef.model_validate({"id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible",
                                "min_auth_level": "step_up", "idempotent": True,
                                "readback_by": "idempotency_key", "source": "customer_cases"}),
    ]


class Provider:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: httpx.Response = httpx.Response(200, json={"status": "ok", "result": []})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def executor(provider: Provider) -> HttpToolExecutor:
    registry = InMemoryRegistry()
    registry.add(*_defs())
    client = httpx.Client(transport=httpx.MockTransport(provider))
    return HttpToolExecutor(registry, FakeIds(), "https://tools.test", "t", client=client)


def _ctx(level: str = "step_up", *, advisor: bool = False) -> ToolCallContext:
    if advisor:
        who, grant = advisor_with_delegation()
        return ToolCallContext(run_id="run-1", release="rel-1", principal=who, on_behalf_of=grant,
                               subject=grant.subject, turn_id="turn-1")
    return ToolCallContext(run_id="run-1", release="rel-1",
                           principal=principal(auth={"level": level, "at": "2026-09-28T12:00:00Z"}))


def test_the_pinned_version_matches_the_document() -> None:
    assert DOC["info"]["version"] == VERSION
    assert set(DOC["paths"]) == {"/v1/tools/{tool_id}/execute", "/v1/tools"}


def test_the_status_enum_is_exactly_the_engines() -> None:
    assert set(DOC["components"]["schemas"]["Status"]["enum"]) == {s.value for s in ToolStatus}


@pytest.mark.parametrize(("case", "args"), [
    ("customer read", {}),
    ("customer write", {"transaction_id": "TX-4", "descripcion": "x"}),
    ("advisor with delegation", {}),
])
def test_every_request_the_client_builds_validates_against_the_request_schema(
        executor: HttpToolExecutor, provider: Provider, case: str, args: dict[str, Any]) -> None:
    if case == "customer write":
        executor.execute(WRITE, args, {"subject_ref": "cust-001"}, _ctx(), idempotency_key="action-1")
    else:
        executor.execute(READ, args, {"subject_ref": "cust-001"}, _ctx("session", advisor="advisor" in case))

    body = json.loads(provider.requests[0].content)
    assert not list(_validator("ExecuteRequest").iter_errors(body)), case
    assert body["context"]["call_id"]


@pytest.mark.parametrize(("status", "tool"), [
    ("ok", READ), ("denied", READ), ("error", READ), ("timeout", READ), ("step_up_required", READ),
    ("ok", WRITE), ("denied", WRITE), ("uncertain", WRITE), ("step_up_required", WRITE),
])
def test_every_contract_answer_maps_to_the_same_engine_status(
        executor: HttpToolExecutor, provider: Provider, status: str, tool: EntityRef) -> None:
    answer: dict[str, Any] = {"status": status, "result": {"x": 1} if status == "ok" else None,
                              "source": "customer_products"}
    if status not in ("ok", "step_up_required"):
        answer["error"] = {"kind": "k", "message": "m"}
    assert not list(_validator("ExecuteResponse").iter_errors(answer))  # the sample is a valid contract answer
    provider.answer = httpx.Response(200, json=answer)

    key = "action-1" if tool is WRITE else None
    result = executor.execute(tool, {}, {}, _ctx(), idempotency_key=key)

    assert result.status is ToolStatus(status)
    assert result.source == "customer_products"


def test_the_contracts_http_statuses_are_handled_without_raising(executor: HttpToolExecutor,
                                                                provider: Provider) -> None:
    unknown = {"status": "error", "error": {"kind": "unknown_tool", "message": "tool desconocida"}}
    broken = {"status": "error", "error": {"kind": "bad_request", "message": "cuerpo inválido"}}
    assert not list(_validator("ExecuteResponse").iter_errors(unknown))
    assert not list(_validator("ExecuteResponse").iter_errors(broken))

    for response in (httpx.Response(401, json={"detail": "unauthorized"}), httpx.Response(404, json=unknown),
                     httpx.Response(422, json=broken)):
        provider.answer = response
        assert executor.execute(READ, {}, {}, _ctx("session")).status is ToolStatus.error
        write = executor.execute(WRITE, {"a": 1}, {}, _ctx(), idempotency_key="k")
        assert write.status is ToolStatus.uncertain
