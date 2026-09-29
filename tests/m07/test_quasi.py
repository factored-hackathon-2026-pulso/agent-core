"""Operaciones genéricas de pii_quasi (M7 §3.2)."""

from datetime import date

import pytest

from agent_core.views.classification import QuasiRule
from agent_core.views.quasi import DROPPED, apply_quasi

TODAY = date(2026, 9, 28)
BUCKET = QuasiRule(op="age_bucket")


def test_drop_is_the_default() -> None:
    assert apply_quasi("110111", QuasiRule(), TODAY) is DROPPED
    assert apply_quasi("1990-05-14", QuasiRule(), TODAY) is DROPPED


def test_age_bucket() -> None:
    assert apply_quasi("1990-05-14", BUCKET, TODAY) == "30-39"


def test_age_bucket_counts_birthday() -> None:
    assert apply_quasi("1996-09-29", BUCKET, TODAY) == "20-29"
    assert apply_quasi("1996-09-28", BUCKET, TODAY) == "30-39"


def test_age_bucket_width_is_data() -> None:
    assert apply_quasi("1990-05-14", QuasiRule(op="age_bucket", width=5), TODAY) == "35-39"


def test_age_bucket_accepts_datetime_strings() -> None:
    assert apply_quasi("1990-05-14T00:00:00Z", BUCKET, TODAY) == "30-39"


@pytest.mark.parametrize("value", ["no-es-fecha", "2030-01-01", "1990-13-01", 19900514, None])
def test_invalid_or_future_dates_are_dropped(value: object) -> None:
    assert apply_quasi(value, BUCKET, TODAY) is DROPPED  # type: ignore[arg-type]
