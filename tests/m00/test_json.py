from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest

from agent_core.domain.json import canonical_bytes, dumps, loads, sha256_hex, to_jsonable


class Color(StrEnum):
    red = "red"


# T-M0-07
def test_decimal_keeps_scale_as_string_in_jcs() -> None:
    assert canonical_bytes({"monto": Decimal("500.00")}) == b'{"monto":"500.00"}'


def test_jcs_sorts_keys() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1}) == b'{"a":2,"b":1}'


def test_jcs_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        canonical_bytes({"p": float("nan")})
    with pytest.raises(ValueError):
        canonical_bytes({"p": float("inf")})
    with pytest.raises(ValueError):
        canonical_bytes({"m": Decimal("NaN")})


def test_jcs_big_int_as_string() -> None:
    assert canonical_bytes({"n": 2**53 - 1}) == b'{"n":9007199254740991}'
    assert canonical_bytes({"n": 2**53}) == b'{"n":"9007199254740992"}'


def test_jcs_probability_float() -> None:
    assert canonical_bytes({"p": 0.5}) == b'{"p":0.5}'


def test_jcs_datetime_enum_timedelta_set() -> None:
    value = {
        "ts": datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        "c": Color.red,
        "ttl": timedelta(minutes=30),
        "s": frozenset({"b", "a"}),
    }
    assert canonical_bytes(value) == b'{"c":"red","s":["a","b"],"ts":"2026-09-28T12:00:00Z","ttl":"PT30M"}'


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        to_jsonable(datetime(2026, 9, 28, 12, 0))  # noqa: DTZ001


def test_loads_reads_decimals() -> None:
    value = loads('{"monto": 1.10, "n": 3}')
    assert value == {"monto": Decimal("1.10"), "n": 3}
    assert isinstance(value, dict)
    assert isinstance(value["monto"], Decimal)


def test_loads_rejects_nan() -> None:
    with pytest.raises(ValueError):
        loads('{"x": NaN}')


def test_dumps_round_trip_is_exact() -> None:
    raw = '{"a":500.00,"b":[1,"x",null,true]}'
    assert dumps(loads(raw)) == raw


def test_sha256_hex() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_loads_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError):
        loads('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        loads('{"x": {"a": 1, "a": 1}}')


def test_to_jsonable_rejects_key_collision() -> None:
    with pytest.raises(ValueError):
        to_jsonable({1: "a", "1": "b"})
    with pytest.raises(ValueError):
        canonical_bytes({1: "a", "1": "b"})


def _deep(n: int) -> list[object]:
    root: list[object] = []
    cur = root
    for _ in range(n):
        nxt: list[object] = []
        cur.append(nxt)
        cur = nxt
    return root


def test_deep_nesting_and_cycles_raise_value_error() -> None:
    deep = _deep(10_000)
    for fn in (to_jsonable, dumps, canonical_bytes):
        with pytest.raises(ValueError):
            fn(deep)
    with pytest.raises(ValueError):
        loads("[" * 10_000 + "]" * 10_000)
    cyc: list[object] = []
    cyc.append(cyc)
    with pytest.raises(ValueError):
        dumps(cyc)


def test_huge_decimal_exponent_rejected() -> None:
    for fn in (to_jsonable, dumps, canonical_bytes):
        with pytest.raises(ValueError):
            fn({"x": Decimal("1E999999999")})
        with pytest.raises(ValueError):
            fn({"x": Decimal("1E-1001")})
    with pytest.raises(ValueError):
        loads('{"x": 1E999999999}')
    assert canonical_bytes({"x": Decimal("1E+1000")}) == b'{"x":"1' + b"0" * 1000 + b'"}'
