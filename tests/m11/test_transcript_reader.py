"""T-M11-07 (permisos del lector) y T-M11-08 (suprimir el transcript no rompe la cadena)."""

import pytest

from agent_core.audit.log import AuditLog
from agent_core.audit.transcript import (
    TRANSCRIPT_PURPOSE,
    RunNotFound,
    TranscriptReader,
    TurnRecorder,
)
from agent_core.views import TokenVault
from testing.builders import advisor_with_delegation, principal, run_state
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript
from tests.m07.helpers import FieldAuthz, make_service
from tests.m11.helpers import events_of

DOC = "1023456789"  # documento inventado


def setup() -> tuple[InMemoryStore, InMemoryTranscript, FakeKeyProvider, TokenVault, str]:
    store, transcript, keys = InMemoryStore(), InMemoryTranscript(), FakeKeyProvider.default()
    vault = TokenVault("run-0001", keys, FakeIds())
    token = vault.tokenize(DOC, "document_number", "doc")
    with store.uow() as uow:
        uow.save_run(run_state(token_map=vault.seal()), 0)
        uow.commit()
    TurnRecorder(transcript, keys).record_turn(
        "run-0001", "turn-0001", f"mi documento es {token}", "ok", [])
    return store, transcript, keys, vault, token


def reader_for(store: InMemoryStore, transcript: InMemoryTranscript, keys: FakeKeyProvider,
               grants: set[tuple[str, str, str]]) -> TranscriptReader:
    views = make_service(keys=keys, authz=FieldAuthz(grants))
    return TranscriptReader(transcript, store.uow, views, keys, FakeIds())


def test_t_m11_07_authorized_advisor_sees_value_and_others_see_mask() -> None:
    store, transcript, keys, _, _ = setup()
    advisor, obo = advisor_with_delegation()
    allowed = reader_for(store, transcript, keys, {("adv-7", "document_number", TRANSCRIPT_PURPOSE)})
    shown = allowed.read_rendered("run-0001", advisor, obo)
    assert DOC in shown[0].text and shown[1].text == "ok"
    denied = reader_for(store, transcript, keys, set())
    hidden = denied.read_rendered("run-0001", advisor, obo)
    assert DOC not in hidden[0].text and "***" in hidden[0].text


def test_advisor_without_delegation_never_sees_the_value() -> None:
    store, transcript, keys, _, _ = setup()
    advisor, _ = advisor_with_delegation()
    reader = reader_for(store, transcript, keys, {("adv-7", "document_number", TRANSCRIPT_PURPOSE)})
    assert DOC not in reader.read_rendered("run-0001", advisor, None)[0].text


def test_unknown_tokens_are_reported_not_rendered() -> None:
    store, transcript, keys, _, _ = setup()
    TurnRecorder(transcript, keys).record_turn("run-0001", "turn-0002", "⟦doc:99⟧", "ok", [])
    reader = reader_for(store, transcript, keys, set())
    entries = reader.read_rendered("run-0001", principal(), None)
    assert entries[2].unknown_tokens == ["⟦doc:99⟧"]


def test_rendered_entry_repr_hides_text() -> None:
    store, transcript, keys, _, _ = setup()
    entries = reader_for(store, transcript, keys, set()).read_rendered("run-0001", principal(), None)
    assert "mi documento" not in repr(entries[0])


def test_unknown_run_raises() -> None:
    store, transcript, keys, _, _ = setup()
    with pytest.raises(RunNotFound):
        reader_for(store, transcript, keys, set()).read_rendered("run-9999", principal(), None)


def test_t_m11_08_suppressing_transcript_does_not_break_the_chain() -> None:
    store, transcript, _, _, _ = setup()
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 3))
        uow.commit()
    transcript.delete_run("run-0001")
    assert transcript.read("run-0001") == []
    assert log.verify_chain("run-0001").ok
