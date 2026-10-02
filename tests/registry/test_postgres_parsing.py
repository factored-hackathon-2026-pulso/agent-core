"""Postgres-free seam of the Postgres store: parsing of stored columns."""

import pytest

from agent_core.registry.errors import IntegrityError
from agent_core.registry.postgres.store import parse_loosened


def test_a_corrupt_loosened_column_raises_an_integrity_error() -> None:  # final review M-2
    with pytest.raises(IntegrityError, match="yardstick_loosened"):
        parse_loosened('{"kind": "repetitions_lowered"}')


def test_an_empty_loosened_column_parses_to_no_changes() -> None:
    assert parse_loosened("[]") == []
