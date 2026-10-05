"""`token_map` del run (M7 §3.4): token estable por (campo, valor NFC), cifrado con AES-256-GCM.

La clave AES se deriva con HKDF-SHA256 de `KeyProvider.key(token_map, kid)`; el nonce sale de
`IdSource.secret_token()` y el AAD liga el blob a su run. Nunca sale del núcleo ni va a eventos."""

import base64
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from agent_core.domain import EncryptedBlob, JsonValue, dumps, loads
from agent_core.ports import IdSource, KeyProvider, KeyPurpose
from agent_core.views.tokens import TOKEN_RE, format_token

_FORMAT = 1
_NONCE_BYTES = 12
_AAD_PREFIX = b"agentcore/token_map/v1|"
_HKDF_INFO = b"agentcore/token_map/aes-256-gcm"


class TokenMapError(Exception):
    """El `token_map` no se pudo abrir (clave, run o contenido). Su mensaje nunca incluye valores."""


def _aead(key: bytes) -> AESGCM:
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(key)
    return AESGCM(derived)


def _aad(run_id: str) -> bytes:
    return _AAD_PREFIX + run_id.encode()


def _nonce(secret_token: str) -> bytes:
    raw = base64.urlsafe_b64decode(secret_token + "=" * (-len(secret_token) % 4))
    if len(raw) < _NONCE_BYTES:
        raise TokenMapError("secret_token demasiado corto para un nonce")
    return raw[:_NONCE_BYTES]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@dataclass(frozen=True, slots=True, repr=False)
class TokenEntry:
    token: str
    tag: str
    field: str
    value: str

    def __repr__(self) -> str:
        return f"TokenEntry({self.token}, field={self.field})"


class TokenVault:
    """Uno por run. Su `repr` no muestra valores."""

    def __init__(self, run_id: str, keys: KeyProvider, ids: IdSource) -> None:
        self._run_id = run_id
        self._keys = keys
        self._ids = ids
        self._by_token: dict[str, TokenEntry] = {}
        self._by_value: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def tokenize(self, value: str, field: str, tag: str) -> str:
        normalized = unicodedata.normalize("NFC", value)
        existing = self._by_value.get((field, normalized))
        if existing is not None:
            return existing
        token = format_token(tag, self._counters.get(tag, 0) + 1)
        self._add(tag, field, normalized, token)
        return token

    def resolve(self, token: str) -> str | None:
        entry = self._by_token.get(token)
        return None if entry is None else entry.value

    def exists(self, token: str) -> bool:
        return token in self._by_token

    def lookup(self, token: str) -> TokenEntry | None:
        return self._by_token.get(token)

    def entries(self) -> tuple[TokenEntry, ...]:
        """Cada entrada del vault, en orden de creación. Solo para comprobar que un valor ocultado no vuelve
        en claro (`ViewService.find_tokenized_echo`): el `repr` de una entrada no muestra el valor."""
        return tuple(self._by_token.values())

    def __len__(self) -> int:
        return len(self._by_token)

    def __repr__(self) -> str:
        return f"TokenVault(run={self._run_id}, tokens={len(self._by_token)})"

    def seal(self) -> EncryptedBlob:
        kid = self._keys.current_kid(KeyPurpose.token_map)
        nonce = _nonce(self._ids.secret_token())
        entries = [[e.tag, e.field, e.value, e.token] for e in self._by_token.values()]
        plaintext = dumps({"v": _FORMAT, "entries": entries}).encode()
        aead = _aead(self._keys.key(KeyPurpose.token_map, kid))
        ciphertext = aead.encrypt(nonce, plaintext, _aad(self._run_id))
        return EncryptedBlob(kid=kid, nonce=_b64(nonce), ciphertext=_b64(ciphertext))

    @classmethod
    def open(cls, blob: EncryptedBlob, run_id: str, keys: KeyProvider, ids: IdSource) -> "TokenVault":
        try:
            key = keys.key(KeyPurpose.token_map, blob.kid)
        except KeyError:
            raise TokenMapError("kid desconocido para el token_map") from None
        try:
            plaintext = _aead(key).decrypt(
                base64.b64decode(blob.nonce, validate=True),
                base64.b64decode(blob.ciphertext, validate=True),
                _aad(run_id),
            )
            payload = loads(plaintext)
        except (InvalidTag, ValueError):
            raise TokenMapError("el token_map no se pudo abrir (clave, run o contenido)") from None
        vault = cls(run_id, keys, ids)
        vault._restore(payload)
        return vault

    def _restore(self, payload: JsonValue) -> None:
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("v") != _FORMAT or not isinstance(entries, list):
            raise TokenMapError("formato de token_map no reconocido")
        for item in entries:
            match item:
                case [str() as tag, str() as field, str() as value, str() as token]:
                    self._add(tag, field, value, token)
                case _:
                    raise TokenMapError("entrada de token_map inválida")

    def _add(self, tag: str, field: str, value: str, token: str) -> None:
        match = TOKEN_RE.fullmatch(token)
        if (match is None or match.group(1) != tag or token in self._by_token
                or (field, value) in self._by_value):
            raise TokenMapError("entrada de token_map inválida o repetida")
        self._by_token[token] = TokenEntry(token, tag, field, value)
        self._by_value[(field, value)] = token
        self._counters[tag] = max(self._counters.get(tag, 0), int(match.group(2)))
