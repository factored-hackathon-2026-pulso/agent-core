"""Mecánica del parser numérico de M8 (§3.3): `Decimal`, fechas ES/PT y lecturas inequívocas sin formato."""

from datetime import date
from decimal import Decimal

import pytest

from agent_core.response.numbers import NumberFormat, scan_figures, strip_tokens

DOT_COMMA = NumberFormat(thousands=".", decimal=",")  # 1.234,56
COMMA_DOT = NumberFormat(thousands=",", decimal=".")  # 1,234.56


def _values(text: str, fmt: NumberFormat | None) -> list[object]:
    return [f.value for f in scan_figures(text, fmt)]


def test_amount_in_both_formats_is_the_same_decimal() -> None:
    assert _values("Son $1.234,56 en total", DOT_COMMA) == [Decimal("1234.56")]
    assert _values("Son $1,234.56 en total", COMMA_DOT) == [Decimal("1234.56")]


def test_values_are_decimal_never_float() -> None:
    (fig,) = scan_figures("0,10", DOT_COMMA)
    assert isinstance(fig.value, Decimal) and fig.value == Decimal("0.10")


def test_percent_and_date() -> None:
    (pct,) = scan_figures("una tasa del 15,5%", DOT_COMMA)
    assert pct.kind == "percent" and pct.value == Decimal("15.5")
    assert _values("vence el 03/10/2026", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence el 2026-10-03", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence el 3 de octubre de 2026", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence em 3 de outubro de 2026", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence em 3 de março de 2026", DOT_COMMA) == [date(2026, 3, 3)]


def test_invalid_date_is_unreadable_not_an_exception() -> None:
    (fig,) = scan_figures("vence el 31/02/2026", DOT_COMMA)
    assert fig.kind == "date" and fig.value is None


def test_digits_inside_tokens_are_not_figures() -> None:  # T-M8-10
    text = "Tu documento ⟦doc:1⟧ y tu teléfono ⟦tel:23⟧"
    assert strip_tokens(text).count("1") == 0 and scan_figures(text, DOT_COMMA) == []
    assert scan_figures(text, None) == []


def test_three_digit_group_is_flagged_ambiguous() -> None:
    (fig,) = scan_figures("1.234", DOT_COMMA)
    assert fig.ambiguous is True


def test_scan_is_pure_and_deterministic() -> None:
    text = "1.234,56 y 2.000,00"
    assert scan_figures(text, DOT_COMMA) == scan_figures(text, DOT_COMMA)


# --- sin `number_format` (P1): solo lecturas inequívocas ---

@pytest.mark.parametrize(("text", "value"), [
    ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("1.234.567", "1234567"),
    ("1,234,567", "1234567"), ("12,5", "12.5"), ("12.5", "12.5"), ("0,123", "0.123"), ("1500", "1500"),
    ("1234,5678", "1234.5678"),
])
def test_without_format_unambiguous_readings_parse(text: str, value: str) -> None:
    (fig,) = scan_figures(text, None)
    assert fig.value == Decimal(value) and fig.ambiguous is False


@pytest.mark.parametrize("text", ["1.234", "1,234", "12.345", "999,999"])
def test_without_format_single_separator_and_three_digits_is_ambiguous_and_unread(text: str) -> None:
    (fig,) = scan_figures(text, None)
    assert fig.ambiguous is True and fig.value is None


@pytest.mark.parametrize("text", ["1.2.3", "1.234,56,7", "12.34.567", "1,234.56.7"])
def test_malformed_groupings_are_unreadable_in_any_mode(text: str) -> None:
    for fmt in (None, DOT_COMMA, COMMA_DOT):
        (fig,) = scan_figures(text, fmt)
        assert fig.value is None and fig.ambiguous is False


def test_with_format_the_reading_follows_the_format() -> None:
    assert _values("1.234", DOT_COMMA) == [Decimal("1234")]
    assert _values("1.234", COMMA_DOT) == [Decimal("1.234")]
    assert _values("1.23", DOT_COMMA) == [None]  # agrupación de miles inválida para ese formato


def test_currency_markers_are_part_of_the_figure() -> None:
    figs = scan_figures("USD 250,00 y 1.000.000,00 COP", DOT_COMMA)
    assert [f.raw for f in figs] == ["USD 250,00", "1.000.000,00 COP"]
    assert [f.value for f in figs] == [Decimal("250.00"), Decimal("1000000.00")]


def test_trailing_punctuation_is_not_part_of_the_number() -> None:
    assert _values("Total $1.234,56.", DOT_COMMA) == [Decimal("1234.56")]
    assert _values("Son 3, 4 y 5.", DOT_COMMA) == [Decimal("3"), Decimal("4"), Decimal("5")]


def test_number_format_rejects_bad_separators() -> None:
    with pytest.raises(ValueError):
        NumberFormat(thousands=".", decimal=".")
    with pytest.raises(ValueError):
        NumberFormat(thousands=" ", decimal=",")
