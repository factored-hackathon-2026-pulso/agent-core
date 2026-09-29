from datetime import timedelta
from decimal import Decimal

import pytest

from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind, LlmUsage
from agent_core.ports import GenerationResult
from agent_core.response.usage import UsageMeter
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway, gen

PROMPT = EntityRef.parse("resumen@1.0.0")


def test_two_calls_are_summed() -> None:
    clock = FakeClock()
    meter = UsageMeter(clock)
    gateway = ScriptedGateway([gen("a", [], tokens_in=10, tokens_out=5, cost="0.001"),
                               gen("b", [], tokens_in=20, tokens_out=8, cost="0.002")])
    for ms in (150, 250):
        def call(ms: int = ms) -> GenerationResult:
            clock.advance(timedelta(milliseconds=ms))
            return gateway.generate(PROMPT, {}, "es")

        meter.call(call)
    assert meter.usage() == LlmUsage(
        calls=2, latency_ms=400, tokens_in=30, tokens_out=13, cost_usd=Decimal("0.003"), cost_known=True,
        models=["scripted-1"])


def test_gateway_error_without_usage_counts_the_call_and_marks_cost_unknown() -> None:
    clock = FakeClock()
    meter = UsageMeter(clock)

    def boom() -> GenerationResult:
        clock.advance(timedelta(milliseconds=30))
        raise GatewayError(GatewayErrorKind.timeout)

    with pytest.raises(GatewayError):
        meter.call(boom)
    usage = meter.usage()
    assert usage is not None and usage.calls == 1 and usage.cost_known is False and usage.latency_ms == 30
    assert usage.tokens_in == 0 and usage.models == []


def test_gateway_error_with_partial_usage_is_accumulated() -> None:
    meter = UsageMeter(FakeClock())

    def boom() -> GenerationResult:
        raise GatewayError(GatewayErrorKind.invalid_output, tokens_in=7, tokens_out=3,
                           cost_usd=Decimal("0.01"), model="m-x")

    with pytest.raises(GatewayError):
        meter.call(boom)
    usage = meter.usage()
    assert usage == LlmUsage(calls=1, latency_ms=0, tokens_in=7, tokens_out=3, cost_usd=Decimal("0.01"),
                             cost_known=True, models=["m-x"])


def test_models_are_unique_and_in_order() -> None:
    meter = UsageMeter(FakeClock())
    gateway = ScriptedGateway([gen("a", [], model="m1"), gen("b", [], model="m2"), gen("c", [], model="m1")])
    for _ in range(3):
        meter.call(lambda: gateway.generate(PROMPT, {}, "es"))
    usage = meter.usage()
    assert usage is not None and usage.models == ["m1", "m2"]


def test_no_calls_means_no_usage() -> None:
    assert UsageMeter(FakeClock()).usage() is None


def test_cost_is_decimal() -> None:
    meter = UsageMeter(FakeClock())
    gateway = ScriptedGateway([gen("a", [], cost="0.1")])
    meter.call(lambda: gateway.generate(PROMPT, {}, "es"))
    usage = meter.usage()
    assert usage is not None and isinstance(usage.cost_usd, Decimal)


def test_latency_is_integer_milliseconds_floor() -> None:
    clock = FakeClock()
    meter = UsageMeter(clock)
    gateway = ScriptedGateway([gen("a", [])])

    def call() -> GenerationResult:
        clock.advance(timedelta(microseconds=1999))
        return gateway.generate(PROMPT, {}, "es")

    meter.call(call)
    usage = meter.usage()
    assert usage is not None and usage.latency_ms == 1
