"""T-M11-06 (huella HMAC con kid) y falla del transcript store (decisión 4)."""

import hashlib

import pytest

from agent_core.audit.transcript import TranscriptWriteError, TurnRecorder
from agent_core.domain import RejectedDraft
from agent_core.views import verify_fingerprint
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.transcript import InMemoryTranscript

REJECTED = [RejectedDraft(text_model="borrador ⟦doc:1⟧", reason="pii_en_claro", failures=["pii"])]


def make() -> tuple[InMemoryTranscript, FakeKeyProvider, TurnRecorder]:
    store, keys = InMemoryTranscript(), FakeKeyProvider.default()
    return store, keys, TurnRecorder(store, keys)


def test_t_m11_06_fingerprint_is_keyed_hmac_with_kid() -> None:
    _, keys, recorder = make()
    refs = recorder.record_turn("run-0001", "turn-0001", "hola", "respuesta", REJECTED)
    assert len(refs) == 3
    for ref, text in zip(refs, ["hola", "borrador ⟦doc:1⟧", "respuesta"], strict=True):
        assert ref.fingerprint.alg == "HMAC-SHA256" and ref.fingerprint.kid == "fp-1"
        assert ref.fingerprint.value != hashlib.sha256(text.encode()).hexdigest()  # nunca sha256 sin clave
        assert verify_fingerprint(text, ref.fingerprint, keys)


def test_entries_are_stored_in_order_with_roles_and_reason() -> None:
    store, _, recorder = make()
    refs = recorder.record_turn("run-0001", "turn-0001", "hola", "respuesta", REJECTED)
    stored = store.read("run-0001")
    assert [(e.role, e.reason) for e in stored] == [
        ("user", None), ("rejected_draft", "pii_en_claro"), ("assistant", None)]
    assert refs[0].entry_id != refs[2].entry_id


def test_store_failure_fails_the_turn_without_leaking_text() -> None:
    store, _, recorder = make()
    store.fail_next()
    with pytest.raises(TranscriptWriteError) as info:
        recorder.record_turn("run-0001", "turn-0001", "hola secreta", "ok", [])
    assert "secreta" not in str(info.value) and "secreta" not in repr(info.value)


def test_fingerprints_are_computed_before_any_write() -> None:
    store, keys, recorder = make()
    keys._current.pop(next(iter(keys._current)))  # type: ignore[attr-defined]  # sin kid vigente: falla al calcular
    with pytest.raises(KeyError):
        recorder.record_turn("run-0001", "turn-0001", "hola", "ok", [])
    assert store.read("run-0001") == []
