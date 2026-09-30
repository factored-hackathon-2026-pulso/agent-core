"""Carga las claves públicas de identidad (principal y delegación) desde un archivo y arma el verificador.

Formato: `{"principal_keys": {kid: b64url}, "delegation_keys": {kid: b64url}}` (32 bytes Ed25519 por clave).
Falla cerrado: cualquier irregularidad es un `SchemaError` que nombra el mapa y el `kid`, nunca el valor."""

from collections.abc import Callable, Hashable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_core.adapters.jws_identity import JwsIdentityVerifier, b64url_decode
from agent_core.domain import SchemaError

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


def load_identity_verifier(path: Path,
                           grant_active: Callable[[str, datetime], bool]) -> JwsIdentityVerifier:
    try:
        data = yaml.load(path.read_bytes(), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError):
        raise SchemaError(f"no se pudo leer el archivo de claves de identidad ({path.name})") from None
    if not isinstance(data, dict):
        raise SchemaError("el archivo de claves de identidad debe ser un mapa")
    return JwsIdentityVerifier(_keys(data, "principal_keys"), _keys(data, "delegation_keys"), grant_active)
