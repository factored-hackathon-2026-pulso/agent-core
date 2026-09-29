from collections.abc import Mapping
from typing import TYPE_CHECKING

from agent_core.ports import KeyPurpose

_MIN_KEY_BYTES = 32


def synthetic_key(label: str) -> bytes:
    """Clave de 32 bytes obviamente falsa (`FAKE-INSECURE-KEY:<label>`); no se confunde con una real."""
    return f"FAKE-INSECURE-KEY:{label}".encode().ljust(_MIN_KEY_BYTES, b"!")


class FakeKeyProvider:
    def __init__(
        self, keys: Mapping[KeyPurpose, Mapping[str, bytes]], current: Mapping[KeyPurpose, str]
    ) -> None:
        self._keys = {purpose: dict(by_kid) for purpose, by_kid in keys.items()}
        self._current = dict(current)
        for purpose, kid in self._current.items():
            if kid not in self._keys.get(purpose, {}):
                raise ValueError(f"kid vigente sin clave para {purpose.value}")
        for by_kid in self._keys.values():
            for key in by_kid.values():
                self._require_length(key)

    @classmethod
    def default(cls) -> "FakeKeyProvider":
        return cls(
            keys={
                KeyPurpose.fingerprint: {"fp-1": synthetic_key("fp-1")},
                KeyPurpose.token_map: {"tm-1": synthetic_key("tm-1")},
            },
            current={KeyPurpose.fingerprint: "fp-1", KeyPurpose.token_map: "tm-1"},
        )

    def current_kid(self, purpose: KeyPurpose) -> str:
        return self._current[purpose]

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        try:
            return self._keys[purpose][kid]
        except KeyError:
            raise KeyError(f"kid desconocido: {kid}") from None

    def rotate(self, purpose: KeyPurpose, kid: str, key: bytes) -> None:
        """Agrega `kid` como vigente; los anteriores siguen disponibles. Un `kid` no se reutiliza."""
        self._require_length(key)
        by_kid = self._keys.setdefault(purpose, {})
        if kid in by_kid:
            raise ValueError("el kid ya existe; una rotación usa un kid nuevo")
        by_kid[kid] = key
        self._current[purpose] = kid

    def __repr__(self) -> str:
        kids = {purpose.value: sorted(by_kid) for purpose, by_kid in self._keys.items()}
        return f"FakeKeyProvider(kids={kids})"

    @staticmethod
    def _require_length(key: bytes) -> None:
        if len(key) < _MIN_KEY_BYTES:
            raise ValueError(f"la clave debe tener al menos {_MIN_KEY_BYTES} bytes")


if TYPE_CHECKING:
    from agent_core.ports import KeyProvider

    def _conforms(x: FakeKeyProvider) -> KeyProvider:
        return x
