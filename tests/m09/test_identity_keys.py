"""Carga de claves públicas de identidad desde archivo: falla cerrado y nunca imprime el valor."""

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_core.adapters.identity_keys import load_identity_verifier
from agent_core.adapters.jws_identity import b64url_encode
from agent_core.domain import SchemaError
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer


def _pub(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url_encode(raw)


def _file(tmp_path: Path, issuer: TestIdentityIssuer, **over: Any) -> Path:
    body = {"principal_keys": {issuer.principal_kid: _pub(issuer.principal_key)},
            "delegation_keys": {issuer.delegation_kid: _pub(issuer.delegation_key)}, **over}
    path = tmp_path / "keys.yaml"
    path.write_text(json.dumps(body), encoding="utf-8")  # JSON es YAML válido
    return path


def test_a_credential_signed_by_the_issuer_verifies(tmp_path: Path) -> None:
    issuer = TestIdentityIssuer(FakeClock())
    verifier = load_identity_verifier(_file(tmp_path, issuer), lambda ref, now: True)
    assert verifier.verify(issuer.customer()).id == "cust-001"


@pytest.mark.parametrize(("bad", "value"), [
    ({"principal_keys": {}}, None),                              # mapa vacío
    ({"principal_keys": {"k1": "no-es-b64url!"}}, "no-es-b64url"),  # base64url inválido
    ({"principal_keys": {"k1": "QUJDRA"}}, "QUJDRA"),            # longitud distinta de 32 bytes
    ({"principal_keys": {"k1": 5}}, None),                       # no es texto
    ({"delegation_keys": {}}, None),
    ({"delegation_keys": {"d1": "no-es-b64url!"}}, "no-es-b64url"),
])
def test_invalid_key_files_fail_closed_without_echoing_the_value(
        tmp_path: Path, bad: dict[str, Any], value: str | None) -> None:
    issuer = TestIdentityIssuer(FakeClock())
    with pytest.raises(SchemaError) as info:
        load_identity_verifier(_file(tmp_path, issuer, **bad), lambda ref, now: True)
    message = str(info.value)
    if value is not None:
        assert value not in message
    for key in (issuer.principal_key, issuer.delegation_key):  # ninguna clave válida del archivo se imprime
        assert _pub(key) not in message


def test_missing_file_is_a_schema_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        load_identity_verifier(tmp_path / "nope.yaml", lambda ref, now: True)


def test_a_file_that_is_not_a_map_is_a_schema_error(tmp_path: Path) -> None:
    path = tmp_path / "keys.yaml"
    path.write_text("- uno\n- dos\n", encoding="utf-8")
    with pytest.raises(SchemaError):
        load_identity_verifier(path, lambda ref, now: True)


@pytest.mark.parametrize("text", [
    '{"principal_keys": {"k1": "AAAA", "k1": "BBBB"}, "delegation_keys": {"d": "AAAA"}}',
    '{"principal_keys": {"k1": "AAAA"}, "principal_keys": {"k2": "BBBB"}, "delegation_keys": {"d": "AAAA"}}',
    "principal_keys:\n  k1: AAAA\n  k1: BBBB\ndelegation_keys:\n  d: AAAA\n",
])
def test_a_repeated_key_fails_closed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "keys.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SchemaError, match="repetid"):
        load_identity_verifier(path, lambda ref, now: True)
