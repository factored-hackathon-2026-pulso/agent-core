"""Configuración del exportador: OTLP/HTTP a Phoenix en la demo (cambiar el endpoint = Langfuse)."""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

# `trace.set_tracer_provider` solo se acepta una vez por proceso; el provider vigente se guarda aquí
# y `spans.span` lo usa directamente, así `setup_tracing` puede reconfigurarse (pruebas).
_PROVIDER: TracerProvider | None = None


def get_provider() -> trace.TracerProvider:
    """Provider vigente de `setup_tracing`, o el global si aún no se configuró."""
    return _PROVIDER if _PROVIDER is not None else trace.get_tracer_provider()


def setup_tracing(endpoint: str | None = None, exporter: SpanExporter | None = None) -> TracerProvider:
    """`endpoint` p. ej. `http://localhost:6006/v1/traces` (exporta por lotes, sin bloquear al turno).
    Un `exporter` explícito manda (pruebas) y exporta de forma síncrona. Reconfigurar cierra el anterior."""
    global _PROVIDER
    if exporter is None:
        if endpoint is None:
            raise ValueError("indica endpoint OTLP o un exporter")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor: SimpleSpanProcessor | BatchSpanProcessor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint))
    else:
        processor = SimpleSpanProcessor(exporter)  # fallas del backend: se registran y descartan
    provider = TracerProvider()
    provider.add_span_processor(processor)
    if _PROVIDER is None:
        trace.set_tracer_provider(provider)  # solo la primera vez; luego manda `_PROVIDER`
    else:
        _PROVIDER.shutdown()  # vacía y libera el hilo/exportador del provider anterior
    _PROVIDER = provider
    return provider
