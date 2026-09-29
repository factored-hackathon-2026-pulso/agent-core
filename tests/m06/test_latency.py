"""Latencia p95 de las guardas < 20 ms (M6 §10, propuesta). Solo con AGENT_CORE_PERF=1."""

import os

import pytest

from agent_core.adapters.system_clock import SystemClock
from tests.m06.helpers import EN, ES, PT, make_agent, make_release, make_service, make_state

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(os.environ.get("AGENT_CORE_PERF") != "1", reason="perf: exporta AGENT_CORE_PERF=1"),
]


def test_guards_p95_under_20ms() -> None:
    clock = SystemClock()
    service = make_service()
    state, agent, release = make_state(locale="es"), make_agent(), make_release()
    for text in (ES, PT, EN):  # calienta lingua: la carga de modelos no cuenta como latencia por turno
        service.run(text, state, agent, release, None, False)
    samples: list[int] = []
    for i in range(200):
        text = (ES, PT, EN, "ok")[i % 4]
        start = clock.monotonic_ns()
        service.run(text, state, agent, release, None, False)
        samples.append(clock.monotonic_ns() - start)
    samples.sort()
    p95_ms = samples[int(len(samples) * 0.95) - 1] / 1_000_000
    print(f"p50={samples[len(samples) // 2] / 1e6:.2f} ms  p95={p95_ms:.2f} ms")
    assert p95_ms < 20
