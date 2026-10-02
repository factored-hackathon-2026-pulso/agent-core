"""`OtelTurnTelemetry`: a live `invoke_agent` per turn; the turn trace id is the request one (U3, T-M4-22)."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.api.app import create_app
from agent_core.composition import OtelTurnTelemetry, RequestTraceIds
from agent_core.composition.serve import build_api_deps
from agent_core.ports import IdKind
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]

REQUEST = "agentcore.api.request"


class RecordingIds:
    """Wraps the world's ids and keeps every id handed out, in order."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.handed: list[tuple[IdKind, str]] = []

    def new_id(self, kind: IdKind) -> str:
        value: str = self._inner.new_id(kind)
        self.handed.append((kind, value))
        return value

    def secret_token(self) -> str:
        token: str = self._inner.secret_token()
        return token


def _client(world: EngineWorld, *, ids: Any = None) -> tuple[TestClient, TestIdentityIssuer]:
    issuer = TestIdentityIssuer(world.clock)
    ports = make_ports(world, issuer)
    if ids is not None:
        ports = replace(ports, ids=ids)
    app = create_app(build_api_deps(ports, telemetry=OtelTurnTelemetry()))
    return TestClient(app, raise_server_exceptions=False), issuer


def _bearer(issuer: TestIdentityIssuer) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.customer()}"}


def _create(client: TestClient, issuer: TestIdentityIssuer) -> dict[str, Any]:
    resp = client.post("/v1/runs", json={"agent": "atencion"},
                       headers={**_bearer(issuer), "Idempotency-Key": "k-1"})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _turn(client: TestClient, issuer: TestIdentityIssuer, session_id: str, text: str,
          client_turn_id: str) -> Any:
    return client.post(f"/v1/sessions/{session_id}/turns", headers=_bearer(issuer),
                       json={"text": text, "channel": "web", "client_turn_id": client_turn_id})


def _named(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _trace_hex(span: ReadableSpan) -> str:
    assert span.context is not None
    return format(span.context.trace_id, "032x")


def test_each_turn_opens_one_invoke_agent_with_closed_attributes(otel: InMemorySpanExporter) -> None:
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    world.start()
    world.understands("continue")
    world.matches()
    world.turn("no reconozco un cargo")
    spans = _named(otel, tel.INVOKE_AGENT)
    assert [dict(s.attributes or {})["agentcore.entry"] for s in spans] == ["start_run", "turn"]
    for s in spans:
        attrs = dict(s.attributes or {})
        assert set(attrs) <= tel.ALLOWED_ATTRIBUTES
        assert attrs["agentcore.agent"] == "atencion@1.0.0" and attrs["gen_ai.agent.name"] == "atencion"
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["agentcore.principal_type"] == "customer" and attrs["agentcore.locale"] == "es"
        assert {"run_id", "turn_id", "session_id", "agentcore.release"} <= set(attrs)
        assert attrs["run_id"] == world.driver.run_id and attrs["agentcore.release"] == world.release.id
    assert len({dict(s.attributes or {})["turn_id"] for s in spans}) == 2


def test_the_turn_trace_id_is_the_request_trace_with_otel(otel: InMemorySpanExporter) -> None:  # U3
    client, issuer = _client(EngineWorld())
    body = _create(client, issuer)
    (request,) = _named(otel, REQUEST)
    (invoke,) = _named(otel, tel.INVOKE_AGENT)
    assert request.context is not None and invoke.context is not None
    trace_hex = _trace_hex(request)
    assert body["trace_id"] == trace_hex and body["first_turn"]["trace_id"] == trace_hex
    assert invoke.context.trace_id == request.context.trace_id
    assert invoke.parent is not None and invoke.parent.span_id == request.context.span_id


def test_a_turn_through_the_threadpool_is_a_child_of_its_request(otel: InMemorySpanExporter) -> None:
    world = EngineWorld()
    client, issuer = _client(world)
    session_id = _create(client, issuer)["session_id"]
    otel.clear()
    world.understands("continue")
    world.matches()
    resp = _turn(client, issuer, session_id, "no reconozco un cargo", "c-1")
    assert resp.status_code == 200, resp.text
    (request,) = _named(otel, REQUEST)
    (invoke,) = _named(otel, tel.INVOKE_AGENT)
    assert resp.json()["trace_id"] == _trace_hex(request) == _trace_hex(invoke)
    assert invoke.parent is not None and request.context is not None
    assert invoke.parent.span_id == request.context.span_id
    assert dict(invoke.attributes or {})["agentcore.entry"] == "turn"


def test_the_turn_trace_id_is_the_request_fallback_without_otel() -> None:  # U3
    world = EngineWorld()
    ids = RecordingIds(world.ids)
    client, issuer = _client(world, ids=ids)
    body = _create(client, issuer)
    fallback = ids.handed[0]  # the middleware asks first, before any engine id
    assert fallback[0] is IdKind.event
    assert body["trace_id"] == body["first_turn"]["trace_id"] == fallback[1]
    assert not fallback[1].startswith("trace-")


def test_a_problem_after_the_engine_carries_the_same_trace_id(otel: InMemorySpanExporter) -> None:
    world = EngineWorld()
    client, issuer = _client(world)
    session_id = _create(client, issuer)["session_id"]
    world.clock.advance(timedelta(days=1))  # the next turn finds the run expired: it closes it, then 410
    otel.clear()
    expired = _turn(client, issuer, session_id, "hola", "c-1")
    assert expired.status_code == 410, expired.text
    (request,) = _named(otel, REQUEST)
    (invoke,) = _named(otel, tel.INVOKE_AGENT)
    assert expired.json()["trace_id"] == _trace_hex(request) == _trace_hex(invoke)
    attrs = dict(invoke.attributes or {})
    assert attrs["agentcore.problem_code"] == "run_closed" and attrs["error.type"] == "EngineError"
    otel.clear()
    closed = _turn(client, issuer, session_id, "hola", "c-2")  # the run is closed: no turn, no invoke_agent
    assert closed.status_code == 410
    (request,) = _named(otel, REQUEST)
    assert closed.json()["trace_id"] == _trace_hex(request) and _named(otel, tel.INVOKE_AGENT) == []


def test_a_deduplicated_retry_returns_the_original_trace_id(otel: InMemorySpanExporter) -> None:  # F16
    world = EngineWorld()
    client, issuer = _client(world)
    session_id = _create(client, issuer)["session_id"]
    world.understands("continue")
    world.matches()
    otel.clear()
    first = _turn(client, issuer, session_id, "no reconozco un cargo", "c-1")
    (first_request,) = _named(otel, REQUEST)
    otel.clear()
    again = _turn(client, issuer, session_id, "no reconozco un cargo", "c-1")
    (again_request,) = _named(otel, REQUEST)
    assert first.status_code == again.status_code == 200
    assert _trace_hex(first_request) != _trace_hex(again_request)
    assert again.json()["trace_id"] == first.json()["trace_id"] == _trace_hex(first_request)
    assert _named(otel, tel.INVOKE_AGENT) == []  # a duplicate opens no turn


def test_outside_a_request_the_trace_id_is_derived_from_the_turn() -> None:  # F8 (3)
    assert RequestTraceIds().current("turn-0001") == "trace-turn-0001"
    with tel.bind_trace_id("abc"):
        assert RequestTraceIds().current("turn-0001") == "abc"
    assert RequestTraceIds().current("turn-0001") == "trace-turn-0001"


def test_an_active_otel_trace_wins_over_the_bound_fallback(otel: InMemorySpanExporter) -> None:
    with tel.bind_trace_id("abc"), tel.tracer("test").start_as_current_span("outer") as outer:
        expected = format(outer.get_span_context().trace_id, "032x")
        assert RequestTraceIds().current("turn-0001") == expected == tel.current_trace_id()


def test_the_engine_world_without_telemetry_records_nothing(otel: InMemorySpanExporter) -> None:
    world = EngineWorld()
    world.start()
    assert _named(otel, tel.INVOKE_AGENT) == []


def test_the_otel_turn_telemetry_takes_no_clock_and_no_ids() -> None:  # Review Focus 1
    import inspect

    assert list(inspect.signature(OtelTurnTelemetry).parameters) == []


@pytest.mark.parametrize("entry", ["start_run", "turn"])
def test_without_a_provider_the_turn_still_binds_the_log_correlation(entry: str) -> None:
    """No exporter (the `serve` default without OTEL_EXPORTER_OTLP_ENDPOINT): spans are no-ops, but `bind`
    still correlates what runs inside the turn (here, the transcript write of step 13)."""
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    seen: list[dict[str, str]] = []
    append = world.transcript.append

    def spying(entry_: Any) -> str:
        seen.append(dict(tel.correlation()))
        return append(entry_)

    world.transcript.append = spying  # type: ignore[method-assign]
    world.start()
    if entry == "turn":
        seen.clear()
        world.understands("continue")
        world.matches()
        world.turn("no reconozco un cargo")
    assert seen and all(c.get("run_id") == world.driver.run_id for c in seen)
    assert all(c.get("agentcore.agent") == "atencion@1.0.0" and "turn_id" in c for c in seen)
    assert tel.correlation() == {}  # nothing leaks out of the turn
