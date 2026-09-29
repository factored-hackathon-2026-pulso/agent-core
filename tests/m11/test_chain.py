"""T-M11-03 (unidad): un evento alterado rompe la cadena. Fórmula del hash (decisión 2)."""

import pytest

from agent_core.audit.chain import (
    ChainError,
    chain_events,
    check_chain,
    event_from_json,
    event_hash,
    event_to_json,
    genesis_hash,
)
from agent_core.domain import sha256_hex
from tests.m11.helpers import EVENT_TYPES, event, events_of


def test_genesis_is_sha256_of_prefix_and_run_id() -> None:
    assert genesis_hash("run-0001") == sha256_hex(b"agentcore:run-0001")


def test_first_event_links_to_genesis_and_seq_is_contiguous() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 3), last=None)
    assert [e.seq for e in chained] == [0, 1, 2]
    assert chained[0].prev_hash == genesis_hash("run-0001")
    assert chained[1].prev_hash == chained[0].hash
    assert chained[2].prev_hash == chained[1].hash


def test_hash_formula() -> None:
    [first] = chain_events("run-0001", [event()], last=None)
    assert first.hash == event_hash(first, genesis_hash("run-0001"))
    # el hash no depende de sí mismo: recalcular con hash=None da lo mismo
    assert first.hash == event_hash(first.model_copy(update={"hash": None}), first.prev_hash or "")


def test_continues_from_last_event() -> None:
    first = chain_events("run-0001", events_of("run-0001", 2), last=None)
    more = chain_events("run-0001", [event(n=9)], last=first[-1])
    assert more[0].seq == 2 and more[0].prev_hash == first[-1].hash


def test_rejects_foreign_run_and_already_chained_events() -> None:
    with pytest.raises(ChainError):
        chain_events("run-0001", [event(run_id="run-0002")], last=None)
    [done] = chain_events("run-0001", [event()], last=None)
    with pytest.raises(ChainError):
        chain_events("run-0001", [done], last=None)


def test_valid_chain_and_empty_chain_are_ok() -> None:
    assert check_chain("run-0001", []).ok
    assert check_chain("run-0001", chain_events("run-0001", events_of("run-0001", 5), None)).ok


def test_altered_payload_breaks_at_that_seq() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 4), None)
    forged = chained[2].model_copy(update={"release": "rel-otra"})
    result = check_chain("run-0001", [*chained[:2], forged, chained[3]])
    assert (result.ok, result.broken_at) == (False, 2)


def test_deleted_reordered_or_wrong_genesis_break() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 4), None)
    assert check_chain("run-0001", [chained[0], chained[2], chained[3]]).broken_at == 1
    assert check_chain("run-0001", [chained[1], chained[0], chained[2]]).broken_at == 0
    assert not check_chain("run-0999", chained).ok  # otra génesis
    assert check_chain("run-0001", chained[1:]).broken_at == 0  # falta el inicio: seq esperado 0


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_hash_survives_json_roundtrip_for_every_event_type(event_type: str) -> None:
    """Postgres guarda el evento como JSON: el hash debe recalcularse igual tras leerlo."""
    [chained] = chain_events("run-0001", [event(event_type)], None)
    back = event_from_json(event_to_json(chained))
    assert back == chained
    assert check_chain("run-0001", [back]).ok
