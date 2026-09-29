from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_core.domain.base import (
    EntityId,
    ExactVersion,
    Locale,
    Model,
    NodeId,
    Probability,
    UtcDatetime,
)


class Stamped(Model):
    at: UtcDatetime
    p: Probability | None = None


# T-M0-10 (parte base; el resto en test_state.py)
def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        Stamped(at=datetime(2026, 9, 28, 12, 0))  # noqa: DTZ001


def test_aware_datetime_normalized_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    stamped = Stamped(at=datetime(2026, 9, 28, 7, 0, tzinfo=bogota))
    assert stamped.at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert stamped.at.utcoffset() == timedelta(0)


def test_models_are_frozen_and_forbid_extra() -> None:
    stamped = Stamped(at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC))
    with pytest.raises(ValidationError):
        stamped.at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Stamped.model_validate({"at": "2026-09-28T12:00:00Z", "extra": 1})


def test_probability_bounds() -> None:
    at = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Stamped(at=at, p=1.5)
    with pytest.raises(ValidationError):
        Stamped(at=at, p=float("nan"))


class Patterns(Model):
    locale: Locale | None = None
    entity: EntityId | None = None
    version: ExactVersion | None = None
    node: NodeId | None = None


@pytest.mark.parametrize(
    "data",
    [
        {"locale": "es\n"},  # sin salto de línea final
        {"locale": "ES"},
        {"locale": "еs"},
        {"entity": "tool\n"},
        {"entity": "Tool"},
        {"node": "a\n"},
        {"version": "1.2.0\n"},
        {"version": "1.2"},
        {"version": "01.2.3"},  # semver 2.0: sin ceros a la izquierda
        {"version": "1.2.0-rc1"},
        {"version": "١.٢.٣"},  # dígitos Unicode no ASCII
    ],
)
def test_annotated_patterns_reject(data: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Patterns.model_validate(data)


def test_annotated_patterns_accept() -> None:
    data = {"locale": "es", "entity": "t/pedir_cargo", "version": "10.0.3", "node": "n_1"}
    ok = Patterns.model_validate(data)
    assert ok.version == "10.0.3"
