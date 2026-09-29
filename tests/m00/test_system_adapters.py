import base64
import uuid
from datetime import timedelta

from agent_core.adapters.system_clock import SystemClock
from agent_core.adapters.system_ids import SystemIds
from agent_core.ports import IdKind


def test_clock_now_is_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzname() == "UTC"


def test_clock_monotonic_never_decreases() -> None:
    clock = SystemClock()
    samples = [clock.monotonic_ns() for _ in range(1000)]
    assert samples == sorted(samples)
    assert all(isinstance(s, int) for s in samples)


def test_new_id_is_unique_uuid7_for_every_kind() -> None:
    ids = SystemIds()
    seen: set[str] = set()
    for kind in IdKind:
        for _ in range(500):
            value = ids.new_id(kind)
            parsed = uuid.UUID(value)
            assert parsed.version == 7
            assert parsed.variant == uuid.RFC_4122
            assert str(parsed) == value
            seen.add(value)
    assert len(seen) == len(IdKind) * 500


def test_secret_token_has_at_least_128_bits_url_safe_no_padding() -> None:
    ids = SystemIds()
    tokens = {ids.secret_token() for _ in range(2000)}
    assert len(tokens) == 2000
    alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    for tok in tokens:
        assert "=" not in tok
        assert set(tok) <= alphabet
        raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))
        assert len(raw) >= 16


def test_adapters_hold_no_state_that_could_leak_via_repr() -> None:
    ids = SystemIds()
    token = ids.secret_token()
    assert token not in repr(ids)
    assert not vars(ids)
    assert not vars(SystemClock())
