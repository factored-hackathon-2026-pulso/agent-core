"""Contrato de `Clock`: mismas aserciones contra SystemClock y FakeClock (spec M0 T-M0-C-*)."""

from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise

import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.ports import Clock
from testing.fakes.clock import FakeClock


def check_now_is_utc_aware(clock: Clock) -> None:
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def check_monotonic_never_decreases(clock: Clock) -> None:
    readings = [clock.monotonic_ns() for _ in range(1000)]
    assert all(b >= a for a, b in pairwise(readings))
    assert all(isinstance(r, int) for r in readings)


@pytest.fixture(params=["fake", "system"])
def clock(request: pytest.FixtureRequest) -> Clock:
    return FakeClock() if request.param == "fake" else SystemClock()


def test_now_is_utc_aware(clock: Clock) -> None:
    check_now_is_utc_aware(clock)


def test_monotonic_never_decreases(clock: Clock) -> None:
    check_monotonic_never_decreases(clock)


# --- sanidad negativa: el contrato debe poder fallar ---


class _NaiveClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1)  # noqa: DTZ001

    def monotonic_ns(self) -> int:
        return 0


class _NonUtcClock(_NaiveClock):
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5)))


class _BackwardsClock(_NaiveClock):
    def __init__(self) -> None:
        self._n = 10**6

    def monotonic_ns(self) -> int:
        self._n -= 1
        return self._n


def test_contract_detects_naive_now() -> None:
    with pytest.raises(AssertionError):
        check_now_is_utc_aware(_NaiveClock())


def test_contract_detects_non_utc_now() -> None:
    with pytest.raises(AssertionError):
        check_now_is_utc_aware(_NonUtcClock())


def test_contract_detects_decreasing_monotonic() -> None:
    with pytest.raises(AssertionError):
        check_monotonic_never_decreases(_BackwardsClock())


# --- propiedades propias de FakeClock ---


def test_fake_advance_moves_both_clocks_together() -> None:
    clock = FakeClock()
    start, mono = clock.now(), clock.monotonic_ns()
    clock.advance(timedelta(milliseconds=1500))
    assert clock.now() - start == timedelta(milliseconds=1500)
    assert clock.monotonic_ns() - mono == 1_500_000_000
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))


def test_fake_only_moves_when_advanced() -> None:
    clock = FakeClock()
    assert clock.now() == clock.now()
    assert clock.monotonic_ns() == clock.monotonic_ns()


def test_fake_rejected_advance_changes_nothing() -> None:
    clock = FakeClock()
    now, mono = clock.now(), clock.monotonic_ns()
    with pytest.raises(ValueError):
        clock.advance(timedelta(microseconds=-1))
    assert (clock.now(), clock.monotonic_ns()) == (now, mono)


def test_fake_zero_advance_is_allowed_and_monotonic_never_decreases() -> None:
    clock = FakeClock()
    readings = []
    for step in (0, 1, 0, 7, 1_000_000):
        clock.advance(timedelta(microseconds=step))
        readings.append(clock.monotonic_ns())
    assert readings == sorted(readings)


def test_fake_advance_is_exact_for_large_deltas() -> None:
    clock = FakeClock()
    delta = timedelta(days=3650, microseconds=1)  # float perdería el microsegundo
    clock.advance(delta)
    assert clock.monotonic_ns() == (3650 * 86_400 * 1_000_000 + 1) * 1_000


_BAD_STARTS = [
    datetime(2026, 1, 1),  # noqa: DTZ001
    datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))),
]


@pytest.mark.parametrize("start", _BAD_STARTS)
def test_fake_rejects_naive_or_non_utc_start(start: datetime) -> None:
    with pytest.raises(ValueError):
        FakeClock(start)


def test_fake_accepts_explicit_utc_start() -> None:
    start = datetime(2030, 5, 1, 8, 30, tzinfo=UTC)
    assert FakeClock(start).now() == start
