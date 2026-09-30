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
