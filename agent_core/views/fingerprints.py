"""Huellas con clave (M7 §3.6, ADR 0008 #4): `HMAC-SHA256(k[kid], JCS(NFC(dato)))`. Nunca sha256 sin clave."""

import hashlib
import hmac
import unicodedata
from typing import Any

from agent_core.domain import Fingerprint, canonical_bytes, to_jsonable
from agent_core.ports import KeyProvider, KeyPurpose


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = unicodedata.normalize("NFC", key)
            if normalized in result:
                raise ValueError("claves duplicadas tras normalizar a NFC")
            result[normalized] = _nfc(item)
        return result
    if isinstance(value, list):
        return [_nfc(item) for item in value]
    return value


def _mac(data: Any, key: bytes) -> str:
    return hmac.new(key, canonical_bytes(_nfc(to_jsonable(data))), hashlib.sha256).hexdigest()


def fingerprint(data: Any, keys: KeyProvider) -> Fingerprint:
    kid = keys.current_kid(KeyPurpose.fingerprint)
    return Fingerprint(alg="HMAC-SHA256", kid=kid, value=_mac(data, keys.key(KeyPurpose.fingerprint, kid)))


def verify_fingerprint(data: Any, fp: Fingerprint, keys: KeyProvider) -> bool:
    try:
        key = keys.key(KeyPurpose.fingerprint, fp.kid)
    except KeyError:
        return False
    return hmac.compare_digest(_mac(data, key), fp.value)
