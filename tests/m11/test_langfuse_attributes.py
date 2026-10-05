"""Langfuse-compatible attributes on the closed list (Langfuse plan, C2): `langfuse.*` always pass, prompt and
completion content only with the content flag, and the derived `langfuse.*` set only when enabled."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel

pytest_plugins = ["tests.support.otel"]

LANGFUSE = {
    "langfuse.session.id": "sess-1", "langfuse.user.id": "user-1", "langfuse.trace.tags": ("agent:a",),
    "langfuse.release": "rel-1", "langfuse.observation.type": "agent",
}


def _attrs(otel: InMemorySpanExporter) -> dict[str, object]:
    return dict(otel.get_finished_spans()[0].attributes or {})


def test_langfuse_attributes_pass_the_closed_list(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="r", release="rel-1"), tel.span(tel.CHAT, attributes=LANGFUSE):
        pass
    attrs = _attrs(otel)
    assert all(attrs[k] == v for k, v in LANGFUSE.items())


def test_content_attributes_are_dropped_by_default(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="r", release="rel-1"), tel.span(
            tel.CHAT, attributes={"gen_ai.prompt": "hola", "gen_ai.completion": "adios"}):
        pass
    attrs = _attrs(otel)
    assert "gen_ai.prompt" not in attrs and "gen_ai.completion" not in attrs


def test_content_attributes_pass_only_with_the_content_flag(otel: InMemorySpanExporter) -> None:
    tel.configure(capture_content=True)
    with tel.bind(run_id="r", release="rel-1"), tel.span(
            tel.CHAT, attributes={"gen_ai.prompt": "hola", "gen_ai.completion": "adios"}):
        pass
    attrs = _attrs(otel)
    assert attrs["gen_ai.prompt"] == "hola" and attrs["gen_ai.completion"] == "adios"


def test_derived_langfuse_attributes_are_off_by_default(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="r", session_id="s", release="rel-1", agent="atencion@1.0.0"):
        with tel.span(tel.INVOKE_AGENT):
            pass
    assert not [k for k in _attrs(otel) if k.startswith("langfuse.")]


def test_derived_langfuse_attributes_follow_the_bound_context(otel: InMemorySpanExporter) -> None:
    tel.configure(langfuse_attributes=True)
    with tel.bind(run_id="r", session_id="s", release="rel-1", agent="atencion@1.0.0"):
        with tel.span(tel.INVOKE_AGENT):
            with tel.span(tel.EXECUTE_TOOL):
                pass
    by_name = {s.name: dict(s.attributes or {}) for s in otel.get_finished_spans()}
    root = by_name[tel.INVOKE_AGENT]
    assert root["langfuse.session.id"] == "s" and root["langfuse.release"] == "rel-1"
    assert root["langfuse.trace.tags"] == ("agent:atencion",)
    assert root["langfuse.observation.type"] == "agent"
    assert by_name[tel.EXECUTE_TOOL]["langfuse.observation.type"] == "tool"
    assert by_name[tel.EXECUTE_TOOL]["langfuse.session.id"] == "s"


def test_derived_attributes_are_omitted_without_their_source(otel: InMemorySpanExporter) -> None:
    tel.configure(langfuse_attributes=True)
    with tel.bind(run_id="r", release="rel-1"), tel.span(tel.CHAT):
        pass
    attrs = _attrs(otel)
    assert "langfuse.session.id" not in attrs and "langfuse.trace.tags" not in attrs
    assert attrs["langfuse.release"] == "rel-1"


def test_a_foreign_attribute_still_raises_in_strict_mode(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="r", release="rel-1"), pytest.raises(ValueError):
        with tel.span(tel.CHAT, attributes={"langfuse.secret": "x"}):
            pass
