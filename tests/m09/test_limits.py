"""M9 F4: tasa y costo diario por principal ya validado (spec §3.1 chequeo 5; T-M9-12)."""

from datetime import timedelta
from decimal import Decimal

import pytest

from agent_core.api.limits import LimitGuard, RateLimitConfig
from agent_core.domain import EngineError, PrincipalKey, ProblemCode
from testing.builders import principal
from testing.fakes.clock import FakeClock
from testing.fakes.storage import InMemoryCostCounters, InMemoryStore
from tests.m09.helpers import anonymous

KEY = PrincipalKey(type="customer", id="cust-001")
CONFIG = RateLimitConfig(window=timedelta(seconds=60), max_hits=3, daily_budget_usd=Decimal("1.00"))


class World:
    def __init__(self) -> None:
        self.store = InMemoryStore()
        self.clock = FakeClock()
        self.guard = LimitGuard(InMemoryCostCounters(self.store), self.clock, CONFIG)

    def spend(self, cost: str = "0.10", key: PrincipalKey = KEY, times: int = 1) -> None:
        with self.store.uow() as uow:
            for _ in range(times):
                uow.add_usage(key, Decimal(cost), self.clock.now())
            uow.commit()

    def code(self, who=None) -> ProblemCode | None:
        try:
            self.guard.check(who or principal())
        except EngineError as exc:
            return exc.code
        return None


def test_under_the_limits_passes() -> None:
    w = World()
    w.spend(times=2)
    assert w.code() is None


def test_rate_limit_is_429_when_the_window_is_full() -> None:  # T-M9-12
    w = World()
    w.spend(times=3)
    assert w.code() is ProblemCode.rate_limited


def test_rate_window_slides() -> None:
    w = World()
    w.spend(times=3)
    w.clock.advance(timedelta(seconds=61))
    assert w.code() is None


def test_daily_budget_is_429_cost_budget_exceeded() -> None:  # T-M9-12
    w = World()
    w.spend(cost="1.00")
    assert w.code() is ProblemCode.cost_budget_exceeded
    assert ProblemCode.cost_budget_exceeded is not ProblemCode.rate_limited


def test_daily_budget_resets_on_the_next_utc_day() -> None:
    w = World()
    w.spend(cost="1.00")
    w.clock.advance(timedelta(hours=13))  # 12:00 UTC + 13 h: día siguiente
    assert w.code() is None


def test_rate_limit_is_checked_before_the_budget() -> None:
    w = World()
    w.spend(cost="0.50", times=3)
    w.spend(cost="0.50")
    assert w.code() is ProblemCode.rate_limited


def test_limits_are_per_principal() -> None:
    w = World()
    w.spend(times=3)
    assert w.code(principal(id="cust-002")) is None


def test_anonymous_principals_are_not_counted_together() -> None:
    """Un anónimo no tiene id: sus contadores compartirían clave con todos los anónimos. Se delega en el
    gateway (límite por IP/canal, chequeo 0) y no se cuenta aquí."""
    w = World()
    w.spend(key=PrincipalKey(type="customer", id=None), times=10)
    assert w.code(anonymous()) is None


def test_rejected_checks_consume_nothing() -> None:
    w = World()
    w.spend(times=3)
    for _ in range(5):
        assert w.code() is ProblemCode.rate_limited
    assert len(w.store.usage[KEY]) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window": timedelta(0)},
        {"window": timedelta(seconds=-1)},
        {"max_hits": 0},
        {"daily_budget_usd": Decimal("0")},
        {"daily_budget_usd": Decimal("-1")},
    ],
)
def test_config_rejects_nonsense(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RateLimitConfig(**kwargs)  # type: ignore[arg-type]


def test_config_defaults_are_demo_values() -> None:
    config = RateLimitConfig()
    assert (config.window, config.max_hits, config.daily_budget_usd) == (
        timedelta(seconds=60),
        30,
        Decimal("5.00"),
    )


# --- ráfagas, reservas en vuelo, Retry-After y servicios --------------------------------------------------


def test_in_flight_requests_count_against_the_window() -> None:
    w = World()
    who = principal()
    with w.guard.slot(who), w.guard.slot(who), w.guard.slot(who):
        with pytest.raises(EngineError) as info:
            with w.guard.slot(who):
                pass
        assert info.value.code is ProblemCode.rate_limited
    with w.guard.slot(who):  # al terminar, los lugares se liberan
        pass


def test_a_concurrent_burst_admits_at_most_the_maximum() -> None:
    import threading

    w = World()
    who = principal()
    barrier, admitted, gate = threading.Barrier(12), [], threading.Event()

    def call() -> None:
        barrier.wait()
        try:
            with w.guard.slot(who):
                admitted.append(1)
                gate.wait(5)  # la petición sigue en vuelo mientras llegan las demás
        except EngineError:
            pass

    threads = [threading.Thread(target=call) for _ in range(12)]
    [t.start() for t in threads]
    barrier_done = threading.Timer(0.5, gate.set)
    barrier_done.start()
    [t.join() for t in threads]
    assert len(admitted) == CONFIG.max_hits


def test_rate_limited_carries_retry_after_for_the_window() -> None:
    w = World()
    w.spend(times=3)
    with pytest.raises(EngineError) as info:
        w.guard.check(principal())
    assert info.value.retry_after == 60


def test_budget_exceeded_carries_retry_after_until_midnight_utc() -> None:
    w = World()
    w.spend(cost="1.00")
    with pytest.raises(EngineError) as info:
        w.guard.check(principal())
    assert info.value.retry_after == 12 * 3600  # el reloj de prueba marca las 12:00 UTC


def test_a_service_principal_has_a_higher_ceiling() -> None:
    w = World()
    service_key = PrincipalKey(type="service", id="svc-1")
    w.spend(key=service_key, times=3)
    assert w.code(principal(type="service", id="svc-1")) is None
    w.spend(key=service_key, times=27)
    assert w.code(principal(type="service", id="svc-1")) is ProblemCode.rate_limited
