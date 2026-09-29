from datetime import timedelta

import pytest

from agent_core.decision.types import ProviderError, ProviderTimeout, RawPrediction
from agent_core.domain import ProviderSpec
from testing.fakes.clock import FakeClock
from testing.fakes.provider import Failure, Reply, ScriptedProvider, Timeout

SPEC = ProviderSpec(provider="classifier")
SCHEMA: dict[str, object] = {}


def _raw(command: str = "affirm", **over: object) -> RawPrediction:
    return RawPrediction(value={"command": command}, p_raw={"command": 0.9}, **over)  # type: ignore[arg-type]


def _predict(p: ScriptedProvider, inputs: dict[str, object] | None = None) -> RawPrediction:
    return p.predict(SPEC, inputs or {"text": "hola"}, SCHEMA, "es")  # type: ignore[arg-type]


def test_returns_scripted_outputs_in_order() -> None:
    provider = ScriptedProvider("classifier", [_raw("affirm"), _raw("deny")])
    assert _predict(provider).value == {"command": "affirm"}
    assert _predict(provider).value == {"command": "deny"}
    provider.push(_raw("cancel"))
    assert _predict(provider).value == {"command": "cancel"}


def test_timeout_raises_and_advances_the_clock() -> None:
    clock = FakeClock()
    provider = ScriptedProvider("classifier", [Timeout(after_ms=250)], clock=clock)
    start = clock.monotonic_ns()
    with pytest.raises(ProviderTimeout):
        _predict(provider)
    assert clock.monotonic_ns() - start == 250 * 1_000_000


def test_failure_raises_provider_error() -> None:
    provider = ScriptedProvider("classifier", [Failure("caído")])
    with pytest.raises(ProviderError, match="caído"):
        _predict(provider)


def test_latency_advances_the_clock_and_is_reported() -> None:
    clock = FakeClock()
    provider = ScriptedProvider("classifier", [Reply(_raw(), latency_ms=40), _raw(latency_ms=7)], clock=clock)
    start = clock.monotonic_ns()
    first = _predict(provider)
    assert first.latency_ms == 40 and (clock.monotonic_ns() - start) // 1_000_000 == 40
    second = _predict(provider)
    assert second.latency_ms == 7 and (clock.monotonic_ns() - start) // 1_000_000 == 47


def test_without_clock_nothing_moves() -> None:
    provider = ScriptedProvider("classifier", [Reply(_raw(), latency_ms=40)])
    assert _predict(provider).latency_ms == 40


def test_exhausted_script_fails_loudly() -> None:
    provider = ScriptedProvider("classifier")
    with pytest.raises(AssertionError, match="ScriptedProvider sin salida guionada"):
        _predict(provider)


def test_calls_keep_a_copy_of_the_inputs() -> None:
    provider = ScriptedProvider("classifier", [_raw()])
    inputs: dict[str, object] = {"text": "hola", "recent_turns": ["a"]}
    _predict(provider, inputs)
    inputs["recent_turns"].append("b")  # type: ignore[attr-defined]
    inputs["text"] = "cambiado"
    spec, recorded, locale = provider.calls[0]
    assert spec == SPEC and locale == "es"
    assert recorded == {"text": "hola", "recent_turns": ["a"]}


def test_failed_calls_are_recorded_too() -> None:
    provider = ScriptedProvider("classifier", [Failure("x")])
    with pytest.raises(ProviderError):
        _predict(provider)
    assert len(provider.calls) == 1


def test_clock_is_not_touched_by_plain_zero_latency_reply() -> None:
    clock = FakeClock()
    before = clock.now()
    ScriptedProvider("classifier", [_raw()], clock=clock).predict(SPEC, {}, {}, "es")
    assert clock.now() - before == timedelta(0)
