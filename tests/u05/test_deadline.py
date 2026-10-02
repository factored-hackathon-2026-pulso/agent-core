"""Plazo total: un endpoint que responde tarde produce `timeout` dentro de `timeout_s` (spec §3.1 paso 5)."""

import threading

import httpx
import pytest
from respx import MockRouter

from agent_core.adapters.system_clock import SystemClock
from agent_core.domain import GatewayError, GatewayErrorKind
from tests.u05.helpers import CHAT, INPUTS, PROMPT, completion, make_world


def test_a_slow_endpoint_times_out_within_the_total_deadline(respx_mock: MockRouter) -> None:  # T-U5-11
    release = threading.Event()
    woke = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(3)  # cada fase HTTP estaría bajo su timeout, pero el total supera timeout_s
        woke.set()
        return httpx.Response(200, json=completion("tarde"))

    respx_mock.post(CHAT).mock(side_effect=slow)
    clock = SystemClock()  # el reloj real es el único permitido (M0 §3); perf_counter está vetado
    started = clock.monotonic_ns()
    try:
        with pytest.raises(GatewayError) as caught:
            make_world(timeout_s=1).gateway.generate(PROMPT, INPUTS, "es")
        elapsed = (clock.monotonic_ns() - started) / 1e9
        # El plazo lo cortó el future (no el SDK): el hilo del handler seguía bloqueado al lanzar el error.
        assert not woke.is_set()
    finally:
        release.set()
    assert caught.value.kind is GatewayErrorKind.timeout
    assert 0.9 <= elapsed < 2.5  # 1 s de plazo, con holgura; no espera a que el hilo termine


class _BlockingClient:
    """Cliente falso: `create` se bloquea hasta que `close()` lo libera (como un socket que gotea)."""

    def __init__(self) -> None:
        self.closed = threading.Event()
        self.chat = self
        self.completions = self

    def create(self, **_: object) -> object:
        self.closed.wait(10)
        raise RuntimeError("conexión cerrada")

    def close(self) -> None:
        self.closed.set()


def _gateway_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("llm-gateway")]


def test_the_abandoned_worker_thread_ends_after_the_deadline() -> None:  # cierra el cliente al vencer
    client = _BlockingClient()
    world = make_world(timeout_s=1, client_factory=lambda *_: client)
    with pytest.raises(GatewayError) as caught:
        world.gateway.generate(PROMPT, INPUTS, "es")
    assert caught.value.kind is GatewayErrorKind.timeout
    assert client.closed.is_set()
    for thread in _gateway_threads():
        thread.join(2)  # acotado: sin `close()` el hilo seguiría 10 s
    assert _gateway_threads() == []


def test_a_failing_close_does_not_mask_the_timeout() -> None:
    client = _BlockingClient()

    def bad_close() -> None:
        client.closed.set()
        raise OSError("boom")

    client.close = bad_close  # type: ignore[method-assign]
    world = make_world(timeout_s=1, client_factory=lambda *_: client)
    with pytest.raises(GatewayError) as caught:
        world.gateway.generate(PROMPT, INPUTS, "es")
    assert caught.value.kind is GatewayErrorKind.timeout


def test_the_worker_thread_keeps_the_otel_context() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("t")
    seen: list[object] = []

    class Client:
        def __init__(self) -> None:
            self.chat = self
            self.completions = self

        def create(self, **_: object) -> object:
            seen.append(trace.get_current_span().get_span_context().span_id)
            return __import__("openai").types.chat.ChatCompletion.model_validate(completion('{"a": 1}'))

        def close(self) -> None:
            pass

    world = make_world(client_factory=lambda *_: Client(), tracer=tracer)
    world.gateway.generate(PROMPT, INPUTS, "es")
    (chat,) = [s for s in exporter.get_finished_spans() if s.name == "chat vendor/modelo-x"]
    assert seen == [chat.context.span_id]
