"""Costo de una llamada: siempre `Decimal` con la tarifa del `ModelProfile` (spec §3.4)."""

from decimal import ROUND_HALF_EVEN, Decimal

from agent_core.domain import ModelPrice

_MILLION = Decimal(1_000_000)
_SIX_PLACES = Decimal("0.000001")


def price_of(price: ModelPrice, tokens_in: int, tokens_out: int) -> Decimal:
    """Costo exacto en `Decimal`, redondeado a 6 decimales con ROUND_HALF_EVEN."""
    total = (
        Decimal(tokens_in) * price.input_per_mtok + Decimal(tokens_out) * price.output_per_mtok
    ) / _MILLION
    return total.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)
