"""Huellas con clave (M7 §3.6). T-M7-05, T-M7-06, T-M7-07."""

import hashlib
import hmac
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import pytest

import agent_core.views.fingerprints as fingerprints_module
from agent_core.domain import canonical_bytes, dumps, loads, sha256_hex
from agent_core.ports import KeyPurpose
from agent_core.views.fingerprints import fingerprint, verify_fingerprint
from testing.fakes.keys import FakeKeyProvider, synthetic_key

DATA = {"document_number": "1023456789", "amount": Decimal("500.00")}


def test_t_m7_05_fingerprint_is_hmac_with_current_kid() -> None:
    """T-M7-05: ninguna huella es sha256 sin clave."""
    keys = FakeKeyProvider.default()
    fp = fingerprint(DATA, keys)
    assert fp.alg == "HMAC-SHA256"
    assert fp.kid == "fp-1"
    assert re.fullmatch(r"[0-9a-f]{64}", fp.value)
    assert fp.value != sha256_hex(canonical_bytes(DATA))
    expected = hmac.new(keys.key(KeyPurpose.fingerprint, "fp-1"), canonical_bytes(DATA), hashlib.sha256)
    assert fp.value == expected.hexdigest()


def test_t_m7_05_other_key_other_fingerprint() -> None:
    other = FakeKeyProvider(
        keys={KeyPurpose.fingerprint: {"fp-1": synthetic_key("otra")},
              KeyPurpose.token_map: {"tm-1": synthetic_key("tm-1")}},
        current={KeyPurpose.fingerprint: "fp-1", KeyPurpose.token_map: "tm-1"},
    )
    assert fingerprint(DATA, other).value != fingerprint(DATA, FakeKeyProvider.default()).value


def test_t_m7_05_views_package_never_hashes_without_key() -> None:
    views_dir = Path(fingerprints_module.__file__).parent
    source = "".join(path.read_text(encoding="utf-8") for path in views_dir.glob("*.py"))
    assert "sha256_hex" not in source
    assert "hashlib.sha256(" not in source


def test_missing_fingerprint_key_fails_closed() -> None:
    keys = FakeKeyProvider(
        keys={KeyPurpose.token_map: {"tm-1": synthetic_key("tm-1")}},
        current={KeyPurpose.token_map: "tm-1"},
    )
    with pytest.raises(KeyError):
        fingerprint(DATA, keys)


def test_t_m7_06_key_order_does_not_matter() -> None:
    """T-M7-06: claves en otro orden → misma huella."""
    keys = FakeKeyProvider.default()
    a = {"a": 1, "b": {"x": "1", "y": [1, 2]}}
    b = {"b": {"y": [1, 2], "x": "1"}, "a": 1}
    assert fingerprint(a, keys) == fingerprint(b, keys)


def test_texts_are_hashed_in_nfc() -> None:
    keys = FakeKeyProvider.default()
    nfc = {unicodedata.normalize("NFC", "señal"): unicodedata.normalize("NFC", "José")}
    nfd = {unicodedata.normalize("NFD", "señal"): unicodedata.normalize("NFD", "José")}
    assert fingerprint(nfc, keys) == fingerprint(nfd, keys)


def test_stable_after_persist_and_reload() -> None:
    keys = FakeKeyProvider.default()
    assert fingerprint(loads(dumps(DATA)), keys) == fingerprint(DATA, keys)


def test_t_m7_07_old_kid_verifies_after_rotation() -> None:
    """T-M7-07: un registro con kid antiguo se verifica después de rotar."""
    keys = FakeKeyProvider.default()
    old = fingerprint(DATA, keys)
    keys.rotate(KeyPurpose.fingerprint, "fp-2", synthetic_key("fp-2"))
    new = fingerprint(DATA, keys)
    assert new.kid == "fp-2"
    assert new.value != old.value
    assert verify_fingerprint(DATA, old, keys)
    assert verify_fingerprint(DATA, new, keys)


def test_verify_rejects_altered_data_unknown_kid_and_wrong_value() -> None:
    keys = FakeKeyProvider.default()
    fp = fingerprint(DATA, keys)
    assert not verify_fingerprint({**DATA, "amount": Decimal("500.01")}, fp, keys)
    assert not verify_fingerprint(DATA, fp.model_copy(update={"kid": "fp-9"}), keys)
    assert not verify_fingerprint(DATA, fp.model_copy(update={"value": "0" * 64}), keys)


def test_verify_returns_false_for_non_ascii_value() -> None:
    keys = FakeKeyProvider.default()
    fp = fingerprint(DATA, keys)
    assert not verify_fingerprint(DATA, fp.model_copy(update={"value": "é" * 64}), keys)
