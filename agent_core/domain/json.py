"""Serialización del núcleo (M0 §2.1).

- `loads`: todo JSON que entra al núcleo; números con decimales como `Decimal`.
- `dumps`: `Decimal` como número JSON exacto; round-trip sin pérdida con `loads`.
- `canonical_bytes`: JCS (RFC 8785). Única canonización del repo (M3 args_hash, M7 huellas, M11 cadena).
"""

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

import rfc8785
from pydantic import BaseModel, TypeAdapter

type JsonValue = bool | int | Decimal | str | list[JsonValue] | dict[str, JsonValue] | None

_MAX_SAFE_INT = 2**53 - 1  # I-JSON: enteros seguros ±(2⁵³−1)
_TIMEDELTA = TypeAdapter(timedelta)


def to_jsonable(value: object) -> Any:
    """Convierte a tipos JSON conservando `Decimal` (y `float` finito, solo para probabilidades)."""
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"Decimal no finito: {value}")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("float no finito (NaN o infinito)")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime sin zona horaria")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, timedelta):
        return _TIMEDELTA.dump_python(value, mode="json")
    if isinstance(value, Mapping):
        return {str(to_jsonable(k)): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        items = [to_jsonable(v) for v in value]
        return sorted(items, key=lambda item: canonical_bytes(item))
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    raise TypeError(f"tipo no serializable: {type(value).__name__}")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON no permitida: {name}")


def loads(raw: str | bytes) -> JsonValue:
    """Lee JSON con `parse_float=Decimal`; rechaza NaN e Infinity."""
    value: JsonValue = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    return value


def _write(value: Any, out: list[str]) -> None:
    if isinstance(value, Decimal):
        out.append(format(value, "f"))
    elif isinstance(value, dict):
        out.append("{")
        for index, (key, item) in enumerate(value.items()):
            if index:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _write(item, out)
        out.append("}")
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _write(item, out)
        out.append("]")
    else:
        out.append(json.dumps(value, ensure_ascii=False, allow_nan=False))


def dumps(value: object) -> str:
    """JSON compacto; `Decimal` como número exacto (`500.00`)."""
    out: list[str] = []
    _write(to_jsonable(value), out)
    return "".join(out)


def _prepare_jcs(value: Any) -> Any:
    if value is None or isinstance(value, bool | str | float):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > _MAX_SAFE_INT else value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _prepare_jcs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare_jcs(item) for item in value]
    raise TypeError(f"tipo no canonizable: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """JCS (RFC 8785): `Decimal` → string con su escala; `int` fuera de ±(2⁵³−1) → string."""
    result: bytes = rfc8785.dumps(_prepare_jcs(to_jsonable(value)))
    return result


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
