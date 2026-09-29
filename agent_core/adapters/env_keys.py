"""Claves desde variables de entorno. SOLO DEMO: etiquetado como secreto de entorno (spec §8.1.1).

Seguridad: el material de clave no aparece en `repr`, `str` ni mensajes de error (los mensajes nombran la
variable y la posición de la entrada, nunca su valor); el base64 se valida en modo estricto y cualquier
irregularidad falla al construir (fail closed)."""

import base64
import binascii
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from agent_core.ports.keys import KeyPurpose

_VARS = {
    KeyPurpose.fingerprint: "AGENTCORE_KEYS_FINGERPRINT",
    KeyPurpose.token_map: "AGENTCORE_KEYS_TOKEN_MAP",
}
_MIN_KEY_BYTES = 32


class KeyConfigError(RuntimeError):
    """Configuración de claves ausente o inválida. Su mensaje nunca contiene valores del entorno."""


def _decode(encoded: str) -> bytes | None:
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


class EnvKeyProvider:
    """Formato por variable: `kid:base64,kid:base64`; la primera es la vigente. Falla al arrancar si falta.

    Recibe el mapeo por inyección (se copia al construir: cambios posteriores no lo afectan). Para leer el
    entorno del proceso se usa, de forma explícita, `EnvKeyProvider.from_environ()`."""

    LABEL = "demo-secret-env"

    def __init__(self, env: Mapping[str, str]) -> None:
        self._keys: dict[KeyPurpose, dict[str, bytes]] = {}
        self._current: dict[KeyPurpose, str] = {}
        for purpose, var in _VARS.items():
            raw = env.get(var, "").strip()
            if not raw:
                raise KeyConfigError(f"falta {var} ({self.LABEL}); nunca se cae a hash sin clave")
            by_kid = self._keys.setdefault(purpose, {})
            for index, item in enumerate(raw.split(",")):
                kid, _, encoded = item.partition(":")
                kid = kid.strip()
                key = _decode(encoded.strip())
                if not kid or key is None or len(key) < _MIN_KEY_BYTES:
                    raise KeyConfigError(
                        f"{var}: entrada {index} inválida (kid vacío, base64 no estricto "
                        f"o clave menor a {_MIN_KEY_BYTES} bytes)"
                    )
                if kid in by_kid:
                    raise KeyConfigError(f"{var}: kid repetido en la entrada {index}")
                by_kid[kid] = key
                if index == 0:
                    self._current[purpose] = kid
        fingerprint_keys = set(self._keys[KeyPurpose.fingerprint].values())
        if any(key in fingerprint_keys for key in self._keys[KeyPurpose.token_map].values()):
            raise KeyConfigError("los propósitos no pueden compartir material de clave")

    @classmethod
    def from_environ(cls) -> "EnvKeyProvider":
        """Única lectura del entorno global del proceso; el resto del código inyecta un mapeo."""
        return cls(os.environ)

    def current_kid(self, purpose: KeyPurpose) -> str:
        return self._current[purpose]

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        try:
            return self._keys[purpose][kid]
        except KeyError:
            raise KeyError(f"kid desconocido: {kid}") from None

    def __repr__(self) -> str:
        kids = {purpose.value: list(by_kid) for purpose, by_kid in self._keys.items()}
        return f"EnvKeyProvider({self.LABEL}, kids={kids})"


if TYPE_CHECKING:
    from agent_core.ports.keys import KeyProvider

    def _conforms(x: EnvKeyProvider) -> KeyProvider:
        return x
