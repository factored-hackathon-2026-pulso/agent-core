"""Contrato de `TranscriptStore`: cada `check_*` corre contra cualquier backend."""

from collections.abc import Callable

import pytest

from agent_core.domain import TranscriptEntry
from agent_core.ports import TranscriptStore
from testing.fakes.transcript import InMemoryTranscript

BACKENDS: list[Callable[[], TranscriptStore]] = [InMemoryTranscript]


def entry(run_id: str = "run-0001", turn_id: str = "turn-0001", role: str = "user",
          text: str = "hola ⟦name:1⟧") -> TranscriptEntry:
    return TranscriptEntry.model_validate(
        {"run_id": run_id, "turn_id": turn_id, "role": role, "text_model": text})


@pytest.fixture(params=BACKENDS)
def store(request: pytest.FixtureRequest) -> TranscriptStore:
    return request.param()  # type: ignore[no-any-return]


def test_append_returns_distinct_ids_and_read_keeps_order(store: TranscriptStore) -> None:
    a = store.append(entry(text="uno"))
    b = store.append(entry(role="assistant", text="dos"))
    assert a != b
    assert [e.text_model for e in store.read("run-0001")] == ["uno", "dos"]


def test_read_is_scoped_by_run(store: TranscriptStore) -> None:
    store.append(entry(run_id="run-0001"))
    assert store.read("run-0002") == []


def test_recent_turns_returns_last_n_entries_oldest_first(store: TranscriptStore) -> None:
    for i in range(5):
        store.append(entry(turn_id=f"turn-{i}", text=f"t{i}"))
    assert [e.text_model for e in store.recent_turns("run-0001", 2)] == ["t3", "t4"]
    assert store.recent_turns("run-0001", 0) == []


def test_write_turn_stores_the_entries_in_order_and_returns_their_ids(store: TranscriptStore) -> None:
    ids = store.write_turn("run-0001", "turn-0001",
                           [entry(text="uno"), entry(role="rejected_draft", text="malo"),
                            entry(role="assistant", text="dos")])
    assert len(ids) == 3 and len(set(ids)) == 3
    assert [e.text_model for e in store.read("run-0001")] == ["uno", "malo", "dos"]


def test_write_turn_is_idempotent_and_the_last_attempt_wins(store: TranscriptStore) -> None:
    store.write_turn("run-0001", "turn-0000", [entry(turn_id="turn-0000", text="antes")])
    store.write_turn("run-0001", "turn-0001", [entry(text="uno"), entry(role="rejected_draft", text="restos"),
                                               entry(role="assistant", text="dos")])
    store.write_turn("run-0001", "turn-0001", [entry(text="uno"), entry(role="assistant", text="tres")])
    # sin duplicados ni restos del intento anterior
    assert [e.text_model for e in store.read("run-0001")] == ["antes", "uno", "tres"]


def test_write_turn_does_not_touch_other_runs_or_turns(store: TranscriptStore) -> None:
    store.write_turn("run-0002", "turn-0001", [entry(run_id="run-0002", text="otro run")])
    store.write_turn("run-0001", "turn-0001", [entry(text="uno")])
    store.write_turn("run-0001", "turn-0001", [entry(text="uno")])
    assert [e.text_model for e in store.read("run-0002")] == ["otro run"]


def test_write_turn_refuses_entries_of_another_run_or_turn_and_writes_nothing(store: TranscriptStore) -> None:
    with pytest.raises(ValueError):
        store.write_turn("run-0001", "turn-0001",
                         [entry(text="bien"), entry(turn_id="turn-0002", text="mal")])
    assert store.read("run-0001") == []
