"""N-09 (§31.12): el verificador recarga el archivo de claves sin reiniciar; conserva el último bueno."""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_core.adapters.identity_keys import ReloadingIdentityVerifier
from agent_core.adapters.jws_identity import b64url_encode
from agent_core.domain import CredentialsInvalid, SchemaError
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer

INTERVAL = timedelta(seconds=5)


class _Rotated(TestIdentityIssuer):
    """Un emisor con otro `kid` y otra clave de principal: lo que llega en una rotación."""

    principal_kid = "p-new"
    delegation_kid = "d-new"

    def __init__(self, clock: FakeClock) -> None:
        super().__init__(clock)
        self.principal_key = Ed25519PrivateKey.generate()
        self.delegation_key = Ed25519PrivateKey.generate()


def _pub(key: object) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)  # type: ignore[attr-defined]
    return b64url_encode(raw)


def _write(path: Path, *issuers: TestIdentityIssuer) -> None:
    body = {"principal_keys": {i.principal_kid: _pub(i.principal_key) for i in issuers},
            "delegation_keys": {i.delegation_kid: _pub(i.delegation_key) for i in issuers}}
    path.write_text(json.dumps(body), encoding="utf-8")


def _issuers() -> tuple[FakeClock, TestIdentityIssuer, TestIdentityIssuer]:
    clock = FakeClock()
    old = TestIdentityIssuer(clock)
    new = _Rotated(clock)
    return clock, old, new


def test_a_rotation_is_picked_up_after_the_interval_without_a_restart(tmp_path: Path) -> None:
    clock, old, new = _issuers()
    path = tmp_path / "keys.yaml"
    _write(path, old)
    verifier = ReloadingIdentityVerifier(path, lambda ref, now: True, clock, INTERVAL)
    with pytest.raises(CredentialsInvalid):
        verifier.verify(new.customer())
    _write(path, old, new)  # kid solapados: la vieja sigue valiendo mientras se rota
    clock.advance(INTERVAL)
    assert verifier.verify(new.customer()).id == "cust-001"
    assert verifier.verify(old.customer()).id == "cust-001"
    _write(path, new)  # se retira la vieja
    clock.advance(INTERVAL)
    with pytest.raises(CredentialsInvalid):
        verifier.verify(old.customer())
    assert verifier.verify(new.customer()).id == "cust-001"


def test_the_file_is_not_read_again_before_the_interval(tmp_path: Path) -> None:
    clock, old, new = _issuers()
    path = tmp_path / "keys.yaml"
    _write(path, old)
    verifier = ReloadingIdentityVerifier(path, lambda ref, now: True, clock, INTERVAL)
    _write(path, new)
    clock.advance(INTERVAL - timedelta(seconds=1))
    assert verifier.verify(old.customer()).id == "cust-001"


def test_a_broken_file_keeps_the_last_good_keys_and_reports_only_the_error_type(tmp_path: Path) -> None:
    clock, old, _ = _issuers()
    path = tmp_path / "keys.yaml"
    _write(path, old)
    verifier = ReloadingIdentityVerifier(path, lambda ref, now: True, clock, INTERVAL)
    path.write_text('{"principal_keys": {}}', encoding="utf-8")
    clock.advance(INTERVAL)
    assert verifier.verify(old.customer()).id == "cust-001"
    assert verifier.last_reload_error == "SchemaError"
    _write(path, old)
    clock.advance(INTERVAL)
    verifier.verify(old.customer())
    assert verifier.last_reload_error is None


def test_a_missing_or_invalid_file_at_startup_still_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        ReloadingIdentityVerifier(tmp_path / "no-existe.yaml", lambda ref, now: True, FakeClock(), INTERVAL)


def test_a_zero_interval_disables_reloading(tmp_path: Path) -> None:
    clock, old, new = _issuers()
    path = tmp_path / "keys.yaml"
    _write(path, old)
    verifier = ReloadingIdentityVerifier(path, lambda ref, now: True, clock, timedelta(0))
    _write(path, new)
    clock.advance(timedelta(days=1))
    with pytest.raises(CredentialsInvalid):
        verifier.verify(new.customer())
