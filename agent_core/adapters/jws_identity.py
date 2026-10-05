"""`IdentityVerifier` sobre JWS compacto Ed25519 (formato A de `raw_credential`, M9 §11).

`header.payload.firma`, cada parte en base64url sin relleno. Header exacto `{"alg": "EdDSA", "kid", "typ"}`:
`typ` separa la credencial de un principal (`principal+jws`) de la de una delegación (`delegation+jws`) y
cada una se verifica con su propio juego de claves (la delegación la firma el emisor de asignaciones,
ADR 0006). El payload es el `Principal` o el `OnBehalfOf` serializado, sin secretos.

Solo valida la firma: la vigencia (`exp`) es de M9 con el `Clock`. Falla cerrado ante cualquier duda y el
error nunca lleva la credencial."""

import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_core.domain import CredentialsInvalid, GrantCheckUnavailable, OnBehalfOf, Principal, loads

PRINCIPAL_TYP = "principal+jws"
DELEGATION_TYP = "delegation+jws"
ALG = "EdDSA"
MAX_TOKEN_CHARS = 8192
_HEADER_KEYS = frozenset({"alg", "kid", "typ"})
_B64URL = re.compile(r"[A-Za-z0-9_-]*")


def b64url_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Estricto: solo el alfabeto url-safe, sin relleno."""
    if _B64URL.fullmatch(text) is None or len(text) % 4 == 1:
        raise ValueError("base64url inválido")
    return urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _invalid() -> CredentialsInvalid:
    return CredentialsInvalid("credencial inválida")  # nunca el contenido de la credencial


class JwsIdentityVerifier:
    def __init__(
        self,
        principal_keys: Mapping[str, Ed25519PublicKey],
        delegation_keys: Mapping[str, Ed25519PublicKey],
        grant_active: Callable[[str, datetime], bool],
    ) -> None:
        """Las claves se indexan por `kid`: rotar es publicar la nueva y retirar la vieja."""
        self._principal_keys = dict(principal_keys)
        self._delegation_keys = dict(delegation_keys)
        self._grant_active = grant_active

    def verify(self, raw_credential: str) -> Principal:
        payload = self._open(raw_credential, PRINCIPAL_TYP, self._principal_keys)
        try:
            return Principal.model_validate(loads(payload))
        except ValueError:
            raise _invalid() from None

    def verify_delegation(self, raw: str) -> OnBehalfOf:
        payload = self._open(raw, DELEGATION_TYP, self._delegation_keys)
        try:
            return OnBehalfOf.model_validate(loads(payload))
        except ValueError:
            raise _invalid() from None

    def grant_active(self, grant_ref: str, now: datetime) -> bool:
        """Falla cerrado: `GrantCheckUnavailable` pasa tal cual (M9 da 503); otra falla es "no activo"."""
        try:
            return bool(self._grant_active(grant_ref, now))
        except GrantCheckUnavailable:
            raise
        except Exception:
            return False

    @staticmethod
    def _open(raw: str, typ: str, keys: Mapping[str, Ed25519PublicKey]) -> bytes:
        """Payload (bytes) de un JWS con firma, `alg`, `typ` y `kid` válidos; si no, `CredentialsInvalid`."""
        try:
            if not isinstance(raw, str) or not raw or len(raw) > MAX_TOKEN_CHARS:
                raise ValueError
            head, body, sig = raw.split(".")
            header: Any = loads(b64url_decode(head))
            if not isinstance(header, dict) or set(header) != _HEADER_KEYS:
                raise ValueError
            if header["alg"] != ALG or header["typ"] != typ or not isinstance(header["kid"], str):
                raise ValueError
            key = keys[header["kid"]]
            key.verify(b64url_decode(sig), f"{head}.{body}".encode("ascii"))
            return b64url_decode(body)
        except Exception:  # firma inválida, formato roto, kid desconocido: todo es lo mismo hacia fuera
            raise _invalid() from None


if TYPE_CHECKING:
    from agent_core.ports import IdentityVerifier

    def _conforms(x: JwsIdentityVerifier) -> IdentityVerifier:
        return x
