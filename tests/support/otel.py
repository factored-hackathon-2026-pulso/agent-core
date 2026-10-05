"""Test isolation for the operational plane: a private span exporter and a root logger left as found.

`otel` installs agent_telemetry's own provider (never OpenTelemetry's global, F3) with an in-memory exporter
and strict mode (a span without `bind` or with an attribute outside the closed list raises). On exit it shuts
the provider down and restores the previous `configure` settings and the once-per-name warning memory.
`root_logging` restores the root logger's handlers and level and the SDK loggers' levels. `no_otel_env`
removes the developer shell's OTEL_* and OPENAI_LOG variables for tests that go through
`main(["serve", ...])`."""

import logging
import os
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_telemetry import setup as tel_setup
from agent_telemetry import spans as tel_spans

_SDK_LOGGERS = ("httpx", "httpcore")


@pytest.fixture
def otel() -> Iterator[InMemorySpanExporter]:
    # A provider left behind by another test would make this one export to the wrong place: fail loudly.
    assert tel_setup._PROVIDER is None, "a previous test leaked an agent_telemetry provider"
    settings = (tel_spans._capture_content, tel_spans._strict, tel_spans._langfuse_attributes)
    warned = set(tel_spans._warned)
    exporter = InMemorySpanExporter()
    tel.setup_tracing(exporter=exporter)
    tel.configure(capture_content=False, strict=True, langfuse_attributes=False)
    try:
        yield exporter
    finally:
        tel.shutdown_tracing()
        tel.configure(capture_content=settings[0], strict=settings[1], langfuse_attributes=settings[2])
        tel_spans._warned.clear()
        tel_spans._warned.update(warned)


@pytest.fixture
def root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    quiet = {name: logging.getLogger(name).level for name in _SDK_LOGGERS}
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, value in quiet.items():
            logging.getLogger(name).setLevel(value)


@pytest.fixture
def no_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [n for n in os.environ if n.startswith("OTEL_") or n == "OPENAI_LOG"]:
        monkeypatch.delenv(name)
