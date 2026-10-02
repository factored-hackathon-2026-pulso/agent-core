"""Exporter configuration. The provider lives here, never as OpenTelemetry's global (F3): every agentcore
tracer comes from `tracer()`, so reconfiguring or shutting down never leaves a span on a closed provider, and
tests never leave a process-wide provider behind."""

from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, Sampler

from agent_telemetry.semconv import SCHEMA_URL, SEMCONV_VERSION

_NOOP = trace.NoOpTracerProvider()
_PROVIDER: TracerProvider | None = None


def get_provider() -> trace.TracerProvider:
    """The provider of `setup_tracing`, or a no-op one (never OpenTelemetry's global)."""
    return _PROVIDER if _PROVIDER is not None else _NOOP


def tracer(name: str) -> trace.Tracer:
    """A tracer of the current provider that declares the pinned semconv schema."""
    return get_provider().get_tracer(name, SEMCONV_VERSION, schema_url=SCHEMA_URL)


def setup_tracing(endpoint: str | None = None, exporter: SpanExporter | None = None, *,
                  resource: Resource | None = None, sampler: Sampler | None = None,
                  headers: Mapping[str, str] | None = None) -> TracerProvider:
    """`endpoint` (OTLP/HTTP, batched: never blocks a turn) or an explicit `exporter` (tests, synchronous).
    The resource and sampler are explicit so the SDK never reads `OTEL_*` behind the caller's back.
    Reconfiguring shuts the previous provider down."""
    global _PROVIDER
    if exporter is None:
        if endpoint is None:
            raise ValueError("indica endpoint OTLP o un exporter")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor: SimpleSpanProcessor | BatchSpanProcessor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers=dict(headers or {})))
    else:
        processor = SimpleSpanProcessor(exporter)  # backend failures are logged and dropped by the SDK
    provider = TracerProvider(resource=resource or Resource({"service.name": "agentcore"}),
                              sampler=sampler or ParentBased(ALWAYS_ON), shutdown_on_exit=False)
    provider.add_span_processor(processor)
    if _PROVIDER is not None:
        _PROVIDER.shutdown()  # flushes and frees the previous provider's thread and exporter
    _PROVIDER = provider
    return provider


def shutdown_tracing() -> None:
    """Flush and close the current provider (end of `serve`, tests); afterwards every span is a no-op.
    Idempotent."""
    global _PROVIDER
    provider, _PROVIDER = _PROVIDER, None
    if provider is not None:
        provider.force_flush()
        provider.shutdown()
