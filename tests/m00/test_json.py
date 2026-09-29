from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest
from hypothesis import given
from hypothesis import strategies as st

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


# --- Estabilidad del hash canónico tras persistir y recargar (dumps -> loads) -----------------------------

_json_leaf = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**70), max_value=2**70),
    st.text(max_size=8),
    st.decimals(allow_nan=False, allow_infinity=False, places=None).filter(
        lambda d: abs(d.as_tuple().exponent) <= 50  # type: ignore[operator]
    ),
    st.sampled_from(
        [Decimal("500"), Decimal("5E+2"), Decimal("-0"), Decimal("0"), Decimal("0.0"), Decimal("-0.0"),
         Decimal("500.00"), Decimal(2**53), Decimal(-(2**53)), Decimal("1E+30")]
    ),
)
_json_values = st.recursive(
    _json_leaf,
    lambda children: st.one_of(
        st.lists(children, max_size=3), st.dictionaries(st.text(max_size=4), children, max_size=3)
    ),
    max_leaves=8,
)


@given(_json_values)
def test_canonical_bytes_survive_dumps_loads(value: object) -> None:
    assert canonical_bytes(value) == canonical_bytes(loads(dumps(value)))


@pytest.mark.parametrize("text", ["500", "5E+2", "-0", "0", "-500", "1E+30"])
def test_integral_decimal_canonicalizes_as_number(text: str) -> None:
    assert canonical_bytes({"n": Decimal(text)}) == canonical_bytes({"n": int(Decimal(text))})


def test_integral_decimal_beyond_safe_int_is_string_and_scaled_stays_string() -> None:
    assert canonical_bytes({"n": Decimal(2**53)}) == b'{"n":"9007199254740992"}'
    assert canonical_bytes({"n": Decimal("500.00")}) == b'{"n":"500.00"}'
