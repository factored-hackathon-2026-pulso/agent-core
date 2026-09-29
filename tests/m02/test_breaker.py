from datetime import timedelta

from agent_core.domain import EntityRef
from agent_core.interpreter import CircuitBreaker
from testing.builders import NOW

TOOL = EntityRef.parse("buscar@1.0.0")
OTHER = EntityRef.parse("otra@1.0.0")


def test_opens_at_threshold_and_closes_when_failures_age_out() -> None:
    breaker = CircuitBreaker(threshold=2, window=timedelta(seconds=60))
    breaker.record_failure(TOOL, NOW)
    assert not breaker.is_open(TOOL, NOW)
    breaker.record_failure(TOOL, NOW + timedelta(seconds=10))
    assert breaker.is_open(TOOL, NOW + timedelta(seconds=11))
    assert not breaker.is_open(OTHER, NOW + timedelta(seconds=11))  # por tool
    assert not breaker.is_open(TOOL, NOW + timedelta(seconds=71))  # la primera falla ya salió de la ventana


def test_success_clears_failures() -> None:
    breaker = CircuitBreaker(threshold=2)
    breaker.record_failure(TOOL, NOW)
    breaker.record_success(TOOL)
    breaker.record_failure(TOOL, NOW)
    assert not breaker.is_open(TOOL, NOW)
