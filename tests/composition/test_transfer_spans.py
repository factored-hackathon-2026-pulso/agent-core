"""ADR 0021 D9 over OpenTelemetry (T-TR-16): `agentcore.transfer` and the target's span link."""

from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.audit import AuditLog
from agent_core.composition import OtelTurnTelemetry
from agent_core.turn import NoTurnTelemetry
from tests.m04.harness import SPECIALIST_RELEASE_ID, World
from tests.m04.helpers import cmd
from tests.m04.test_transfer import REENVIO, ROUTER

pytest_plugins = ["tests.support.otel"]
TEXT = "no reconozco un cargo"
TRANSFER_KEYS = {"agentcore.transfer.id", "agentcore.transfer.from_agent", "agentcore.transfer.to_agent",
                 "agentcore.transfer.to_release_id", "agentcore.transfer.outcome",
                 "agentcore.transfer.reason_code"}


def _spans(otel: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in otel.get_finished_spans() if s.name == name]


def _attrs(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def test_transfer_span_attributes_and_the_target_link(otel: InMemorySpanExporter) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    # The `chat` helper span stands for the API's request span: both invoke_agent spans hang from it.
    with tel.bind(run_id="req", release="req"), tel.span(tel.CHAT) as request:
        w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    origin, target = sorted(_spans(otel, tel.INVOKE_AGENT), key=lambda s: s.start_time or 0)
    source_run, target_run = w.session_runs()
    moved = next(e for e in w.audit.read(source_run.run_id) if e.type == "run_transferred")
    attrs = _attrs(transfer)
    assert {k: v for k, v in attrs.items() if k in TRANSFER_KEYS} == {
        "agentcore.transfer.id": moved.payload.transfer_id,
        "agentcore.transfer.from_agent": "recepcion",
        "agentcore.transfer.to_agent": "disputas",
        "agentcore.transfer.to_release_id": SPECIALIST_RELEASE_ID,
        "agentcore.transfer.outcome": "transferred",
    }
    assert attrs["run_id"] == source_run.run_id  # correlation of the origin's turn
    assert transfer.parent is not None and transfer.parent.span_id == origin.context.span_id
    (link,) = target.links
    assert link.context.span_id == transfer.context.span_id
    assert link.context.trace_id == transfer.context.trace_id
    assert not origin.links
    # F10: siblings under the same parent, in the same trace
    assert target.parent is not None and origin.parent is not None
    assert target.parent.span_id == origin.parent.span_id == request.get_span_context().span_id
    assert target.context.trace_id == origin.context.trace_id == transfer.context.trace_id
    assert _attrs(target)["run_id"] == target_run.run_id == moved.payload.to_run_id
    # What is guaranteed: the target starts after the transfer span ended and, in time, runs inside the
    # origin's invoke_agent (still open while the target is processed); it is a sibling in the tree only.
    assert target.start_time is not None and transfer.end_time is not None
    assert target.start_time >= transfer.end_time
    assert origin.start_time is not None and origin.end_time is not None and target.end_time is not None
    assert origin.start_time <= target.start_time and target.end_time <= origin.end_time


def test_a_rejected_transfer_has_no_release_and_links_nothing(otel: InMemorySpanExporter) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception(choice="saldos")
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    attrs = _attrs(transfer)
    assert attrs["agentcore.transfer.outcome"] == "rejected"
    assert attrs["agentcore.transfer.reason_code"] == "not_in_directory"
    assert "agentcore.transfer.to_release_id" not in attrs and "agentcore.transfer.to_agent" not in attrs
    (turn,) = _spans(otel, tel.INVOKE_AGENT)
    assert not turn.links and transfer.parent is not None
    assert transfer.parent.span_id == turn.context.span_id


def test_a_rejection_of_a_listed_target_echoes_to_agent_only(otel: InMemorySpanExporter) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception(origin_depth=1)
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    attrs = _attrs(transfer)
    assert attrs["agentcore.transfer.to_agent"] == "disputas"
    assert attrs["agentcore.transfer.reason_code"] == "transfer_limit"
    assert "agentcore.transfer.to_release_id" not in attrs


def test_transfer_attributes_are_ids_and_enums_only(otel: InMemorySpanExporter) -> None:  # rule 6
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    values = {str(v) for s in otel.get_finished_spans() for v in _attrs(s).values()}
    assert not any(TEXT in v for v in values)  # the client's text
    (transfer,) = _spans(otel, tel.TRANSFER)
    assert set(_attrs(transfer)) <= tel.ALLOWED_ATTRIBUTES


def test_the_chains_are_byte_identical_with_and_without_the_otel_telemetry(
        otel: InMemorySpanExporter) -> None:  # determinism: telemetry never alters events or hashes
    chains = []
    for telemetry in (NoTurnTelemetry(), OtelTurnTelemetry()):
        w = World(chain_factory=AuditLog, telemetry=telemetry)
        w.reception()
        w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
        w.turn(TEXT)
        chains.append([[e.model_dump_json() for e in w.audit.read(run.run_id)] for run in w.session_runs()])
    assert len(chains[0]) == 2 and chains[0] == chains[1]
    assert all(e for chain in chains[0] for e in chain)
    assert _spans(otel, tel.TRANSFER)  # the live telemetry really ran


def test_without_a_provider_a_transfer_still_works() -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    result = w.turn(TEXT)
    assert result.agent is not None and result.agent.id == "disputas"


def test_without_a_request_span_the_target_stays_in_the_origins_trace(otel: InMemorySpanExporter) -> None:
    """No parent span at all (provider set, no API span): the target hangs from the origin's invoke_agent so
    both runs share a trace; the link still points to the transfer span."""
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    origin, target = sorted(_spans(otel, tel.INVOKE_AGENT), key=lambda s: s.start_time or 0)
    assert origin.parent is None
    assert target.context.trace_id == origin.context.trace_id == transfer.context.trace_id
    assert target.parent is not None and target.parent.span_id == origin.context.span_id
    (link,) = target.links
    assert link.context.span_id == transfer.context.span_id


def test_a_rejection_in_the_target_hangs_from_the_targets_invoke_agent(otel: InMemorySpanExporter) -> None:
    from testing.fakes.decision import make_decision
    from tests.m02.harness import tool_def

    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.add(ROUTER, REENVIO)
    served: dict[str, Any] = {}
    w.add_tool(tool_def("leer_directorio", "read"), handler=lambda _: served["directorio"])
    state = w.reception()
    assert state is not None
    served["directorio"] = state.facts["directorio"].value
    w.decisions.push(make_decision({"choice": "disputas"}, {"choice": True}))
    w.understand.push(cmd("continue"), cmd("start_flow", flow="reenvio"))
    w.turn(TEXT)
    origin, target = sorted(_spans(otel, tel.INVOKE_AGENT), key=lambda s: s.start_time or 0)
    first, second = sorted(_spans(otel, tel.TRANSFER), key=lambda s: s.start_time or 0)
    assert _attrs(first)["agentcore.transfer.outcome"] == "transferred"
    assert first.parent is not None and first.parent.span_id == origin.context.span_id
    assert _attrs(second)["agentcore.transfer.outcome"] == "rejected"
    assert _attrs(second)["agentcore.transfer.reason_code"] == "transfer_limit"
    assert _attrs(second)["agentcore.transfer.from_agent"] == "disputas"
    assert second.parent is not None and second.parent.span_id == target.context.span_id


def test_a_validation_that_raises_finishes_the_transfer_span_once_with_an_error(
        otel: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("SECRETO")

    monkeypatch.setattr(w.engine._transferer, "validate", boom)
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    with pytest.raises(RuntimeError):
        w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)  # exported once, i.e. ended once
    attrs = _attrs(transfer)
    assert attrs["error.type"] == "RuntimeError" and "agentcore.transfer.outcome" not in attrs
    assert "SECRETO" not in str(attrs) and not transfer.events
