"""`price_of`: costo exacto en Decimal, 6 decimales, ROUND_HALF_EVEN (spec del gateway §3.4, T-U5-07)."""

from datetime import date
from decimal import Decimal

from agent_core.adapters.llm.cost import price_of
from agent_core.domain import ModelPrice


def _price(inp: str, out: str) -> ModelPrice:
    return ModelPrice(input_per_mtok=Decimal(inp), output_per_mtok=Decimal(out), source="prueba",
                      as_of=date(2026, 9, 30))


def test_known_price_and_tokens_give_the_expected_decimal() -> None:
    cost = price_of(_price("3.00", "15.00"), 1000, 500)  # (3000 + 7500) / 1e6
    assert cost == Decimal("0.010500") and cost.as_tuple().exponent == -6


def test_zero_tokens_cost_zero() -> None:
    assert price_of(_price("3", "15"), 0, 0) == Decimal("0.000000")


def test_rounding_is_half_even_at_six_decimals() -> None:
    assert price_of(_price("0.5", "0"), 1, 0) == Decimal("0.000000")  # 0.0000005 -> 0 (par)
    assert price_of(_price("1.5", "0"), 1, 0) == Decimal("0.000002")  # 0.0000015 -> 2 (par)
    assert price_of(_price("2.5", "0"), 1, 0) == Decimal("0.000002")  # 0.0000025 -> 2 (par)


def test_result_is_a_decimal_never_a_float() -> None:
    assert isinstance(price_of(_price("3", "15"), 7, 9), Decimal)
