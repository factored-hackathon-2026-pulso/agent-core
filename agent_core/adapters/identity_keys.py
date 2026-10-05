"""Carga las claves públicas de identidad (principal y delegación) desde un archivo y arma el verificador.

Formato: `{"principal_keys": {kid: b64url}, "delegation_keys": {kid: b64url}}` (32 bytes Ed25519 por clave).
El archivo del staff puede omitir `delegation_keys`.
Falla cerrado: cualquier irregularidad es un `SchemaError` que nombra el mapa y el `kid`, nunca el valor."""

import threading
from collections.abc import Callable, Hashable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_core.adapters.jws_identity import JwsIdentityVerifier, b64url_decode
from agent_core.domain import OnBehalfOf, Principal, SchemaError
from agent_core.ports import Clock

_KEY_BYTES = 32


class _UniqueKeyLoader(yaml.SafeLoader):
    """`safe_load` que rechaza claves repetidas en un mapa (por defecto gana la última, en silencio)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if isinstance(key, Hashable) and key in seen:
                raise SchemaError(f"clave repetida en el archivo de claves de identidad: {key}")
            if isinstance(key, Hashable):
                seen.add(key)
        return super().construct_mapping(node, deep)


def _keys(data: dict[str, object], name: str) -> dict[str, Ed25519PublicKey]:
    raw = data.get(name)
    if not isinstance(raw, dict) or not raw:
        raise SchemaError(f"{name} debe ser un mapa kid -> clave y no puede estar vacío")
    out: dict[str, Ed25519PublicKey] = {}
    for kid, encoded in raw.items():
        if not isinstance(kid, str) or not kid or not isinstance(encoded, str):
            raise SchemaError(f"{name}: entrada inválida (kid o clave no son texto)")
        try:
            material = b64url_decode(encoded)
            if len(material) != _KEY_BYTES:
                raise ValueError("longitud")
            out[kid] = Ed25519PublicKey.from_public_bytes(material)
        except ValueError:
            raise SchemaError(f"{name}[{kid}]: clave Ed25519 inválida (base64url de 32 bytes)") from None
    return out


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise SchemaError(f"no se pudo leer el archivo de claves de identidad ({path.name})") from None


def _build(raw: bytes, name: str, grant_active: Callable[[str, datetime], bool],
           delegation: bool) -> JwsIdentityVerifier:
    try:
        data = yaml.load(raw, Loader=_UniqueKeyLoader)
    except yaml.YAMLError:
        raise SchemaError(f"no se pudo leer el archivo de claves de identidad ({name})") from None
    if not isinstance(data, dict):
        raise SchemaError("el archivo de claves de identidad debe ser un mapa")
    principal_keys = _keys(data, "principal_keys")
    delegation_keys = _keys(data, "delegation_keys") if delegation or "delegation_keys" in data else {}
    return JwsIdentityVerifier(principal_keys, delegation_keys, grant_active)


def load_identity_verifier(path: Path, grant_active: Callable[[str, datetime], bool], *,
                           delegation: bool = True) -> JwsIdentityVerifier:
    """`delegation=False` (staff): `delegation_keys` es opcional; sin él, toda delegación se rechaza."""
    return _build(_read(path), path.name, grant_active, delegation)


class ReloadingIdentityVerifier:
    """Verificador que vuelve a leer el archivo de claves, a lo sumo una vez por `interval`, al usarse (N-09).

    Rotar es publicar la clave nueva con su `kid` junto a la vieja y retirar la vieja después: no hace falta
    reiniciar. El arranque falla cerrado como `load_identity_verifier`; una recarga que falla (archivo roto o
    ausente) conserva el último verificador bueno y deja solo el tipo del error en `last_reload_error`, nunca
    un mensaje con claves. `interval` 0 la desactiva. El instante sale del `Clock` inyectado."""

    def __init__(self, path: Path, grant_active: Callable[[str, datetime], bool], clock: Clock,
                 interval: timedelta, *, delegation: bool = True) -> None:
        self._path = path
        self._grant_active = grant_active
        self._clock = clock
        self._interval = interval
        self._delegation = delegation
        self._lock = threading.Lock()
        self._raw = _read(path)
        self._inner = _build(self._raw, path.name, grant_active, delegation)
        self._checked_at = clock.now()
        self.last_reload_error: str | None = None

    def _current(self) -> JwsIdentityVerifier:
        with self._lock:
            now = self._clock.now()
            if self._interval > timedelta(0) and now - self._checked_at >= self._interval:
                self._checked_at = now
                try:
                    raw = _read(self._path)
                    if raw != self._raw:
                        self._inner = _build(raw, self._path.name, self._grant_active, self._delegation)
                        self._raw = raw
                    self.last_reload_error = None
                except SchemaError as exc:
                    self.last_reload_error = type(exc).__name__
            return self._inner

    def verify(self, raw_credential: str) -> Principal:
        return self._current().verify(raw_credential)

    def verify_delegation(self, raw: str) -> OnBehalfOf:
        return self._current().verify_delegation(raw)

    def grant_active(self, grant_ref: str, now: datetime) -> bool:
        return self._current().grant_active(grant_ref, now)
