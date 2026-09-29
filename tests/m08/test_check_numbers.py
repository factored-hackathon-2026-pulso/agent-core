"""Comprobación 3 (`numbers`): igualdad exacta con `Decimal` contra hechos citados (ADR 0011, spec §3)."""

from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_core.response.check_numbers import check_numbers
from agent_core.response.numbers import NumberFormat, scan_figures
from agent_core.response.types import Draft, Failure
from tests.m08.helpers import COMMA_DOT, DOT_COMMA, make_ctx, make_fact


def _check(text: str, citations: list[str], facts: dict[str, Any],
           fmt: NumberFormat | None = None) -> list[Failure]:
    """`facts`: `fact_id -> valor` o `fact_id -> (valor, kwargs de FactSource)` (p. ej. `kind="compute"`)."""
    built = {}
    for fid, spec in facts.items():
        value, source = spec if isinstance(spec, tuple) else (spec, {})
        built[fid] = make_fact(fid, value, **source)
    return check_numbers(Draft(text=text, citations=citations), make_ctx(built, number_format=fmt))


CARGO = {"cargo": {"monto": "1234.56"}}


def test_same_fact_passes_in_both_formats() -> None:  # T-M8-01
    assert _check("Tu cargo es de $1.234,56", ["f1"], {"f1": CARGO}, DOT_COMMA) == []
    assert _check("Tu cargo es de $1,234.56", ["f1"], {"f1": CARGO}, COMMA_DOT) == []


def test_wrong_format_for_the_text_is_rejected_not_misread() -> None:  # T-M8-01
    failures = _check("Tu cargo es de $1,234.56", ["f1"], {"f1": CARGO}, DOT_COMMA)
    assert [f.check for f in failures] == ["numbers"]


def test_compute_fact_backs_a_converted_amount() -> None:  # T-M8-02
    facts = {
        "f_usd": {"monto_usd": "250.00"},
        "f_cop": ({"monto_cop": "1000000.00"},
                  {"kind": "compute", "ref": "convertir_moneda@1.0.0", "inputs": ["f_usd"]}),
    }
    text = "Son USD 250,00, que equivalen a $1.000.000,00 COP."
    assert _check(text, ["f_usd", "f_cop"], facts, DOT_COMMA) == []


def test_calculated_figure_without_its_compute_fact_is_rejected() -> None:  # T-M8-03
    facts = {"f_usd": {"monto_usd": "250.00"}}
    failures = _check("Son 250,00 USD, o sea $1.000.000,00", ["f_usd"], facts, DOT_COMMA)
    assert [f.check for f in failures] == ["numbers"]
    assert failures[0].detail == "cifra 2 sin fuente" and "1.000.000" not in failures[0].detail


def test_digits_inside_tokens_are_ignored() -> None:  # T-M8-10
    assert _check("Tu documento ⟦doc:1⟧ y ⟦tel:23⟧", [], {}, DOT_COMMA) == []
    assert _check("Tu documento ⟦doc:1⟧ y ⟦tel:23⟧", [], {}, None) == []


def test_uncited_fact_does_not_back_a_figure() -> None:
    failures = _check("Tu cargo es de 1.234,56", [], {"f1": CARGO}, DOT_COMMA)
    assert [f.check for f in failures] == ["numbers"]


def test_no_tolerance() -> None:
    facts = {"f1": {"monto": "1234.57"}}
    assert [f.check for f in _check("Son 1.234,56", ["f1"], facts, DOT_COMMA)] == ["numbers"]


def test_trailing_zeros_do_not_matter_but_value_must_be_equal() -> None:
    facts = {"f1": {"monto": "1234.50"}, "f2": {"n": 7}}
    assert _check("Son 1.234,5 y 7", ["f1", "f2"], facts, DOT_COMMA) == []


def test_without_number_format_ambiguous_reading_is_rejected() -> None:  # P1
    failures = _check("Tu cargo es de 1.234", ["f1"], {"f1": {"monto": "1234"}}, None)
    assert [f.check for f in failures] == ["numbers"] and failures[0].detail == "cifra 1 ambigua"


def test_without_number_format_unambiguous_reading_is_accepted() -> None:  # P1
    assert _check("Tu cargo es de $1.234,56", ["f1"], {"f1": CARGO}, None) == []
    assert _check("Tu cargo es de $1,234.56", ["f1"], {"f1": CARGO}, None) == []


def test_ambiguous_reading_is_not_rescued_by_a_matching_fact() -> None:  # P1: mejora no aprobada
    facts = {"f1": {"monto": "1.234"}}
    failures = _check("Son 1.234 en total", ["f1"], facts, None)
    assert [f.check for f in failures] == ["numbers"]


def test_with_number_format_the_ambiguous_shape_reads_by_format() -> None:
    assert _check("Son 1.234 en total", ["f1"], {"f1": {"monto": "1234"}}, DOT_COMMA) == []
    assert _check("Son 1.234 en total", ["f1"], {"f1": {"monto": "1.234"}}, COMMA_DOT) == []


def test_unreadable_figure_is_rejected() -> None:
    failures = _check("Versión 1.2.3", [], {}, DOT_COMMA)
    assert [f.check for f in failures] == ["numbers"] and failures[0].detail == "cifra 1 ilegible"


def test_percent_and_date_are_compared_against_facts() -> None:
    facts = {"f1": {"tasa": "15.5", "vence": "2026-10-03"}}
    assert _check("Tasa del 15,5% con vencimiento el 03/10/2026", ["f1"], facts, DOT_COMMA) == []
    other = _check("Vence el 04/10/2026", ["f1"], facts, DOT_COMMA)
    assert [f.check for f in other] == ["numbers"]


def test_non_business_numbers_count_like_any_figure() -> None:  # P2 (decidido: cuentan igual)
    failures = _check("Sigue el paso 2 dentro de 24 horas", [], {}, DOT_COMMA)
    assert [f.detail for f in failures] == ["cifra 1 sin fuente", "cifra 2 sin fuente"]
    assert _check("Sigue el paso 2 dentro de 24 horas", ["f1"], {"f1": {"paso": 2, "sla_horas": 24}},
                  DOT_COMMA) == []


def test_one_failure_per_figure_in_order_and_no_pii_in_detail() -> None:
    failures = _check("Son 5 y 6 y 7", ["f1"], {"f1": {"n": 6}}, DOT_COMMA)
    assert [f.detail for f in failures] == ["cifra 1 sin fuente", "cifra 3 sin fuente"]


def test_nested_lists_and_ints_and_decimal_values_are_indexed() -> None:
    facts = {"f1": {"items": [{"cantidad": 3}, {"cantidad": 12}], "total": Decimal("45.90")}}
    assert _check("Tienes 3 items, 12 unidades por 45,90", ["f1"], facts, DOT_COMMA) == []


def test_booleans_are_not_numbers() -> None:
    facts = {"f1": {"activo": True}}
    assert [f.check for f in _check("Tienes 1 cuenta", ["f1"], facts, DOT_COMMA)] == ["numbers"]


def test_does_not_mutate_context_or_draft() -> None:
    draft = Draft(text="Son 1.234,56", citations=["f1"])
    ctx = make_ctx({"f1": make_fact("f1", CARGO)}, number_format=DOT_COMMA)
    before = (draft.model_dump(), dict(ctx.facts_model_view))
    check_numbers(draft, ctx)
    assert (draft.model_dump(), dict(ctx.facts_model_view)) == before


def _format(value: Decimal, fmt: NumberFormat) -> str:
    whole, _, frac = f"{value:.2f}".partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return fmt.thousands.join(groups) + fmt.decimal + frac


@settings(max_examples=200, deadline=None)
@given(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("99999999.99"), places=2))
def test_round_trip_with_both_formats(value: Decimal) -> None:
    for fmt in (DOT_COMMA, COMMA_DOT):
        (fig,) = scan_figures(_format(value, fmt), fmt)
        assert isinstance(fig.value, Decimal) and fig.value == value
