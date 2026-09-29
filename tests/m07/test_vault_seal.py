"""Sellado del token_map (M7 §3.4). T-M7-10."""

import base64
import json

import pytest

from agent_core.domain import EncryptedBlob
from agent_core.ports import KeyPurpose
from agent_core.views.vault import TokenMapError, TokenVault, _aad, _aead
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider, synthetic_key

DOC = "1023456789"


def _filled() -> tuple[TokenVault, FakeKeyProvider, FakeIds]:
    keys, ids = FakeKeyProvider.default(), FakeIds()
    vault = TokenVault("run-0001", keys, ids)
    vault.tokenize(DOC, "document_number", "doc")
    vault.tokenize("1098765432", "document_number", "doc")
    vault.tokenize("ana@example.test", "email", "email")
    return vault, keys, ids


def test_t_m7_10_seal_open_round_trip() -> None:
    """T-M7-10: seal/open del token_map hace round-trip y el contador continúa."""
    vault, keys, ids = _filled()
    blob = vault.seal()
    assert blob.kid == "tm-1"
    opened = TokenVault.open(blob, "run-0001", keys, ids)
    assert len(opened) == 3
    assert opened.resolve("⟦doc:1⟧") == DOC
    assert opened.resolve("⟦email:1⟧") == "ana@example.test"
    assert opened.tokenize(DOC, "document_number", "doc") == "⟦doc:1⟧"
    assert opened.tokenize("1111111111", "document_number", "doc") == "⟦doc:3⟧"


def test_empty_vault_round_trip() -> None:
    keys, ids = FakeKeyProvider.default(), FakeIds()
    blob = TokenVault("run-0001", keys, ids).seal()
    assert len(TokenVault.open(blob, "run-0001", keys, ids)) == 0


def test_blob_has_no_clear_values() -> None:
    vault, _, _ = _filled()
    blob = vault.seal()
    assert DOC.encode() not in base64.b64decode(blob.ciphertext)
    assert DOC not in blob.model_dump_json()


def test_each_seal_uses_a_fresh_nonce() -> None:
    vault, _, _ = _filled()
    assert vault.seal().nonce != vault.seal().nonce


def test_blob_does_not_open_in_another_run() -> None:
    vault, keys, ids = _filled()
    with pytest.raises(TokenMapError):
        TokenVault.open(vault.seal(), "run-0002", keys, ids)


def test_tampered_blob_fails_without_leaking() -> None:
    vault, keys, ids = _filled()
    blob = vault.seal()
    raw = bytearray(base64.b64decode(blob.ciphertext))
    raw[0] ^= 1
    tampered = blob.model_copy(update={"ciphertext": base64.b64encode(bytes(raw)).decode()})
    with pytest.raises(TokenMapError) as excinfo:
        TokenVault.open(tampered, "run-0001", keys, ids)
    assert DOC not in str(excinfo.value)


def test_unknown_kid_fails() -> None:
    vault, keys, ids = _filled()
    with pytest.raises(TokenMapError):
        TokenVault.open(vault.seal().model_copy(update={"kid": "tm-9"}), "run-0001", keys, ids)


def test_old_blob_opens_after_rotation_and_reseal_uses_new_kid() -> None:
    vault, keys, ids = _filled()
    old = vault.seal()
    keys.rotate(KeyPurpose.token_map, "tm-2", synthetic_key("tm-2"))
    opened = TokenVault.open(old, "run-0001", keys, ids)
    assert opened.resolve("⟦doc:1⟧") == DOC
    assert opened.seal().kid == "tm-2"


def test_token_map_key_is_not_the_fingerprint_key() -> None:
    keys = FakeKeyProvider.default()
    assert keys.key(KeyPurpose.token_map, "tm-1") != keys.key(KeyPurpose.fingerprint, "fp-1")


def _crafted(keys: FakeKeyProvider, payload: object, run_id: str = "run-0001") -> EncryptedBlob:
    """Sella un contenido arbitrario con las claves reales para llegar a las ramas de `_restore`."""
    nonce = base64.urlsafe_b64decode(FakeIds().secret_token() + "==")[:12]
    key = keys.key(KeyPurpose.token_map, keys.current_kid(KeyPurpose.token_map))
    plaintext = json.dumps(payload).encode()
    ciphertext = _aead(key).encrypt(nonce, plaintext, _aad(run_id))
    return EncryptedBlob(
        kid="tm-1",
        nonce=base64.b64encode(nonce).decode(),
        ciphertext=base64.b64encode(ciphertext).decode(),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "entries": [["doc", "f", "a", "⟦doc:1⟧"], ["doc", "f", "b", "⟦doc:1⟧"]]},
        {"v": 1, "entries": [["doc", "f", "a", "⟦doc:1⟧"], ["doc", "f", "a", "⟦doc:2⟧"]]},
        {"v": 1, "entries": [["email", "f", "a", "⟦doc:1⟧"]]},
        {"v": 1, "entries": [["doc", "f", "a", "no-es-token"]]},
        {"v": 1, "entries": [["doc", "f", "a"]]},
        {"v": 2, "entries": []},
        {"v": 1, "entries": "x"},
        [1, 2],
    ],
)
def test_invalid_restored_content_fails(payload: object) -> None:
    keys, ids = FakeKeyProvider.default(), FakeIds()
    with pytest.raises(TokenMapError):
        TokenVault.open(_crafted(keys, payload), "run-0001", keys, ids)
