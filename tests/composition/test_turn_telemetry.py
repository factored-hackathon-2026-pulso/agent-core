"""`OtelTurnTelemetry`: a live `invoke_agent` per turn; the turn trace id is the request one (U3, T-M4-22)."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.api.app import create_app
from agent_core.audit import dump_fixture
from agent_core.composition import OtelTurnTelemetry, RequestTraceIds
from agent_core.composition.serve import build_api_deps
from agent_core.composition.telemetry import derived_spans
from agent_core.domain import DecisionMade, EngineEvent, JsonValue, RuleEvaluated, ToolCalled
from agent_core.ports import IdKind
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from testing.replay import SCENARIOS, record_scenario
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]

REQUEST = "agentcore.api.request"
REGISTRY = Path("tests/fixtures/registry-demo")
RUNS = Path("tests/fixtures/runs")
CATALOG = Path("tests/fixtures/catalogo-datos-prueba.yaml")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CHILDREN = (tel.DECIDE, tel.RULE, tel.EXECUTE_TOOL)
# Review Focus 2 / M11 §3.2: spans of raw tracers do not go through `ALLOWED_ATTRIBUTES`; each has its own
# closed list in the code that opens it, checked here. `chat` (gateway) is excluded by name: its list is the
# gateway's (gateway spec §3.5, T-U5-19 in tests/u05) and no engine double here opens it.
RAW_TRACER_ATTRIBUTES: dict[str, frozenset[str]] = {
    REQUEST: frozenset({"http.request.method", "http.response.status_code", "error.type"}),
}
EXCLUDED_BY_NAME = frozenset({tel.CHAT})
EVENT_ATTRIBUTES: dict[str, frozenset[str]] = {  # span events: only the security log's
    "agentcore.access_rejected": frozenset({"agentcore.reason", "agentcore.principal_type"}),
}


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


# --- T4: children derived from the turn's events (M11 §3.2, F13) ---------------------------------------


def _ns(ts: datetime) -> int:
    return (ts - _EPOCH) // timedelta(microseconds=1) * 1_000


def _events_of(camino: str) -> list[EngineEvent]:
    world = EngineWorld()
    SCENARIOS[camino](world)
    assert world.driver.run_id is not None
    return world.audit.read(world.driver.run_id)


@pytest.fixture(scope="module")
def recorded() -> dict[str, list[EngineEvent]]:
    return {camino: _events_of(camino) for camino in ("resuelto", "escalado_por_monto")}


def _first[E: EngineEvent](events: list[EngineEvent], kind: type[E]) -> E:
    return next(e for e in events if isinstance(e, kind))


@pytest.fixture
def decision_made(recorded: dict[str, list[EngineEvent]]) -> DecisionMade:
    return _first(recorded["resuelto"], DecisionMade)


@pytest.fixture
def rule_evaluated(recorded: dict[str, list[EngineEvent]]) -> RuleEvaluated:
    return _first(recorded["escalado_por_monto"], RuleEvaluated)


@pytest.fixture
def tool_called(recorded: dict[str, list[EngineEvent]]) -> ToolCalled:
    return _first(recorded["resuelto"], ToolCalled)


def test_a_decision_becomes_a_decide_span_ending_at_its_ts(decision_made: DecisionMade) -> None:  # T-M11-14
    (span,) = derived_spans([decision_made])
    assert span.name == tel.DECIDE and span.end_ns == _ns(decision_made.ts)
    assert span.end_ns - span.start_ns == decision_made.payload.latency_ms * 1_000_000
    assert span.attributes == {
        "agentcore.decision.id": decision_made.payload.decision_id,
        "agentcore.decision.model": str(decision_made.payload.model),
        "agentcore.decision.provider": decision_made.payload.provider_used,
        "agentcore.decision.fallback_depth": decision_made.payload.fallback_depth,
        "agentcore.decision.tokens": decision_made.payload.tokens,
    }


def test_a_rule_is_an_instant_span_without_its_inputs(rule_evaluated: RuleEvaluated) -> None:  # T-M11-14
    (span,) = derived_spans([rule_evaluated])
    assert span.name == tel.RULE and span.start_ns == span.end_ns == _ns(rule_evaluated.ts)
    assert span.attributes["agentcore.rule.result"] is rule_evaluated.payload.result
    assert span.attributes["agentcore.node"] == rule_evaluated.payload.node_id
    assert rule_evaluated.payload.policy is not None
    assert span.attributes["agentcore.rule.policy"] == str(rule_evaluated.payload.policy)
    assert set(span.attributes) == {"agentcore.node", "agentcore.rule.result", "agentcore.rule.policy"}


def test_a_rule_without_a_policy_has_no_policy_attribute(rule_evaluated: RuleEvaluated) -> None:
    payload = rule_evaluated.payload.model_copy(update={"policy": None})
    bare = rule_evaluated.model_copy(update={"payload": payload})
    (span,) = derived_spans([bare])
    assert "agentcore.rule.policy" not in span.attributes


def test_a_tool_call_carries_ids_and_status_never_args_result_or_error(tool_called: ToolCalled) -> None:
    (span,) = derived_spans([tool_called])
    t = tool_called.payload
    assert span.name == tel.EXECUTE_TOOL and span.end_ns == _ns(tool_called.ts)
    assert span.end_ns - span.start_ns == t.latency_ms * 1_000_000
    assert span.attributes == {
        "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": t.tool.id,
        "gen_ai.tool.call.id": t.call_id,
        "agentcore.tool": str(t.tool), "agentcore.node": t.node_id, "agentcore.tool.status": t.status.value,
        "agentcore.tool.attempt": t.attempt,
    }
    assert set(span.attributes) <= tel.ALLOWED_ATTRIBUTES


def test_other_events_produce_no_children(recorded: dict[str, list[EngineEvent]]) -> None:
    others = [e for e in recorded["resuelto"] if not isinstance(e, DecisionMade | RuleEvaluated | ToolCalled)]
    assert {e.type for e in others} >= {"turn_started", "turn_completed", "run_started"}
    assert derived_spans(others) == []


def test_deriving_the_children_never_touches_the_events(recorded: dict[str, list[EngineEvent]]) -> None:
    """The payload dicts are shared with the engine's chain (m04 §3.9): the derivation is read-only."""
    events = recorded["resuelto"] + recorded["escalado_por_monto"]
    before = [e.model_dump(mode="json") for e in events]
    assert derived_spans(events)
    assert [e.model_dump(mode="json") for e in events] == before


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_recording_with_live_telemetry_keeps_every_fixture_byte_identical(  # T-M11-13, Review Focus 1
        camino: str, otel: InMemorySpanExporter) -> None:
    grabado = dump_fixture(record_scenario(camino, REGISTRY, telemetry=OtelTurnTelemetry()))
    assert (RUNS / f"{camino}.yaml").read_text(encoding="utf-8") == grabado
    names = [s.name for s in otel.get_finished_spans()]
    assert tel.INVOKE_AGENT in names and tel.DECIDE in names  # the telemetry really ran


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_children_match_the_events_and_hang_from_their_turn(camino: str, otel: InMemorySpanExporter) -> None:
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    SCENARIOS[camino](world)
    assert world.driver.run_id is not None
    events = world.audit.read(world.driver.run_id)
    spans = otel.get_finished_spans()
    for name, kind in ((tel.DECIDE, "decision_made"), (tel.RULE, "rule_evaluated"),
                       (tel.EXECUTE_TOOL, "tool_called")):
        assert sum(s.name == name for s in spans) == sum(e.type == kind for e in events), name
    turns = {s.context.span_id: s for s in spans if s.name == tel.INVOKE_AGENT and s.context is not None}
    by_turn: dict[str, list[EngineEvent]] = {}
    for e in events:
        by_turn.setdefault(e.turn_id or "", []).append(e)
    for child in (s for s in spans if s.name in CHILDREN):
        assert child.parent is not None and child.parent.span_id in turns
        parent = turns[child.parent.span_id]
        assert child.context is not None and parent.context is not None
        assert child.context.trace_id == parent.context.trace_id
        turn_id = dict(parent.attributes or {})["turn_id"]
        assert dict(child.attributes or {})["turn_id"] == turn_id
        assert child.end_time is not None and child.start_time is not None
        own = derived_spans(by_turn[str(turn_id)])
        expected = {(d.start_ns, d.end_ns) for d in own if d.name == child.name}
        assert (child.start_time, child.end_time) in expected  # the event's times, not the SDK clock


_PAYLOAD_KEYS = ("args", "result", "error", "inputs", "value")


def _payload_values(event: EngineEvent) -> list[JsonValue]:
    """`args`, `result`, `error`, `inputs` and `value` of any event that has them (rule 6)."""
    dumped = event.payload.model_dump(mode="json")
    return [v for k, v in dumped.items() if k in _PAYLOAD_KEYS]


def _string_leaves(value: JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        if len(value) >= 4:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_leaves(item)


def _ids(events: list[EngineEvent]) -> set[str]:
    """Ids do go in attributes (`gen_ai.tool.call.id`, `agentcore.decision.id`, correlation)."""
    out = {x for e in events for x in (e.run_id, e.turn_id, e.session_id, e.release) if x is not None}
    out |= {e.payload.call_id for e in events if isinstance(e, ToolCalled)}
    out |= {e.payload.decision_id for e in events if isinstance(e, DecisionMade)}
    return out


def _assert_closed_keys(spans: Any) -> None:
    """Review Focus 2: every key of every exported span is in its closed list (M11 §3.2)."""
    for s in spans:
        attrs = set(dict(s.attributes or {}))
        if s.name in EXCLUDED_BY_NAME:
            continue
        assert attrs <= RAW_TRACER_ATTRIBUTES.get(s.name, tel.ALLOWED_ATTRIBUTES), (s.name, attrs)
        for event in s.events:
            assert event.name in EVENT_ATTRIBUTES, (s.name, event.name)
            assert set(dict(event.attributes or {})) <= EVENT_ATTRIBUTES[event.name], (s.name, event.name)


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_no_span_attribute_carries_a_payload_value(camino: str, otel: InMemorySpanExporter) -> None:  # rule 6
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    SCENARIOS[camino](world)
    assert world.driver.run_id is not None
    events = world.audit.read(world.driver.run_id)
    texts = {str(op["text"]) for op in world.driver.ops if op.get("text")}
    leaves = (set(_string_leaves([v for e in events for v in _payload_values(e)])) | texts) - _ids(events)
    assert leaves  # the check has something to bite on
    spans = otel.get_finished_spans()
    assert {s.name for s in spans} <= {tel.INVOKE_AGENT, *CHILDREN}  # no raw-tracer span escapes the check
    _assert_closed_keys(spans)
    for s in spans:
        for value in dict(s.attributes or {}).values():
            assert str(value) not in leaves, (s.name, value)


def test_every_span_of_a_request_has_closed_keys(otel: InMemorySpanExporter) -> None:  # Review Focus 2
    """Through M9: `agentcore.api.request` (raw tracer, own list), the rejected access event of the security
    log, `invoke_agent` and its children (ALLOWED_ATTRIBUTES)."""
    world = EngineWorld()
    client, issuer = _client(world)
    session_id = _create(client, issuer)["session_id"]
    world.understands("continue")
    world.matches()
    assert _turn(client, issuer, session_id, "no reconozco un cargo", "c-1").status_code == 200
    rejected = client.post("/v1/runs", json={"agent": "atencion"}, headers={"Authorization": "Bearer nope"})
    assert rejected.status_code == 401
    spans = otel.get_finished_spans()
    names = {s.name for s in spans}
    assert {REQUEST, tel.INVOKE_AGENT, tel.DECIDE} <= names
    assert any(e.name == "agentcore.access_rejected" for s in spans for e in s.events)
    _assert_closed_keys(spans)


def test_the_replay_engine_has_no_telemetry(otel: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:  # Review Focus 1
    from agent_core.cli import main
    from agent_core.turn import NoTurnTelemetry, TurnEngine
    from testing.replay import runner

    engines: list[TurnEngine] = []
    build = runner.build_turn_engine

    def capturing(deps: Any) -> TurnEngine:
        engines.append(build(deps))
        return engines[-1]

    monkeypatch.setattr(runner, "build_turn_engine", capturing)
    code = main(["replay", str(RUNS / "resuelto.yaml"), "--mode", "fixture", "--registry", str(REGISTRY),
                 "--catalog", str(CATALOG)])
    assert code == 0 and capsys.readouterr().out.startswith("match")
    assert engines and all(isinstance(e._telemetry, NoTurnTelemetry) for e in engines)
    assert otel.get_finished_spans() == ()
