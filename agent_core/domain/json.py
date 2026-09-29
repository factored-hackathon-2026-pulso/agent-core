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
# Un Decimal con |exponente| mayor se rechaza: `format(d, "f")` lo expandiría a miles de millones de dígitos.
MAX_DECIMAL_EXPONENT = 1000


def to_jsonable(value: object) -> Any:
    """Convierte a tipos JSON conservando `Decimal` (y `float` finito, solo para probabilidades)."""
    try:
        return _to_jsonable(value)
    except RecursionError:
        raise ValueError("estructura demasiado anidada o cíclica") from None


def _check_decimal(value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError(f"Decimal no finito: {value}")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise ValueError(f"exponente de Decimal fuera de rango (máx. {MAX_DECIMAL_EXPONENT})")


def _to_jsonable(value: object) -> Any:
    if isinstance(value, BaseModel):
        return _to_jsonable(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, Enum):
        return _to_jsonable(value.value)
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, Decimal):
        _check_decimal(value)
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
        result: dict[str, Any] = {}
        for k, v in value.items():
            key = str(_to_jsonable(k))
            if key in result:
                raise ValueError(f"claves duplicadas tras convertir a str: {key!r}")
            result[key] = _to_jsonable(v)
        return result
    if isinstance(value, set | frozenset):
        items = [_to_jsonable(v) for v in value]
        return sorted(items, key=lambda item: canonical_bytes(item))
    if isinstance(value, list | tuple):
        return [_to_jsonable(v) for v in value]
    raise TypeError(f"tipo no serializable: {type(value).__name__}")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"constante JSON no permitida: {name}")


def _parse_decimal(text: str) -> Decimal:
    value = Decimal(text)
    _check_decimal(value)
    return value


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"clave duplicada en el JSON: {key!r}")
        result[key] = item
    return result


def loads(raw: str | bytes) -> JsonValue:
    """Lee JSON con `parse_float=Decimal`; rechaza NaN, Infinity, claves duplicadas y anidación excesiva."""
    try:
        value: JsonValue = json.loads(
            raw,
            parse_float=_parse_decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicates,
        )
    except RecursionError:
        raise ValueError("estructura demasiado anidada") from None
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
    try:
        _write(to_jsonable(value), out)
    except RecursionError:
        raise ValueError("estructura demasiado anidada") from None
    return "".join(out)


def _prepare_jcs(value: Any) -> Any:
    if value is None or isinstance(value, bool | str | float):
        return value
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        if isinstance(exponent, int) and exponent >= 0:
            # `dumps` lo escribe sin punto y `loads` lo devuelve como int: se canoniza igual (sin deriva tras
            # persistir y recargar). Los Decimal con escala ("500.00") siguen como string.
            value = int(value)
        else:
            return format(value, "f")
    if isinstance(value, int):
        return str(value) if abs(value) > _MAX_SAFE_INT else value
    if isinstance(value, dict):
        return {key: _prepare_jcs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare_jcs(item) for item in value]
    raise TypeError(f"tipo no canonizable: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """JCS (RFC 8785): `Decimal` con escala → string; `Decimal` entero → el int de `loads(dumps(x))`;
    `int` fuera de ±(2⁵³−1) → string."""
    try:
        result: bytes = rfc8785.dumps(_prepare_jcs(to_jsonable(value)))
    except RecursionError:
        raise ValueError("estructura demasiado anidada") from None
    return result


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
