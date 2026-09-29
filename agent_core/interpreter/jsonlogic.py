"""Evaluador del subconjunto cerrado de JSON Logic (M2 §3.6). Aritmética con `Decimal`, nunca `float`.

Semántica deliberadamente estricta y sin excepciones para datos: sin coerción de tipos, `bool` no es número y
una comparación de orden entre tipos incomparables da `False`. Solo un operador fuera del conjunto o una
profundidad excesiva lanzan `ValueError` (son errores de esquema que M1 rechaza al cargar, G0-01)."""

from collections.abc import Callable
from decimal import Decimal

from agent_core.domain import JsonValue

MAX_DEPTH = 64

type _Op = Callable[[list[JsonValue], JsonValue, list[str], int], JsonValue]


def _number(value: JsonValue) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    return None


def truthy(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str | list | dict):
        return len(value) > 0
    number = _number(value)
    return number != 0 if number is not None else True


def _equal(a: JsonValue, b: JsonValue) -> bool:
    na, nb = _number(a), _number(b)
    if na is not None or nb is not None:
        return na is not None and nb is not None and na == nb
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _order(a: JsonValue, b: JsonValue) -> int | None:
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return (na > nb) - (na < nb)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    return None


def _eval(node: JsonValue, data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    if depth > MAX_DEPTH:
        raise ValueError("profundidad máxima excedida")
    if isinstance(node, list):
        return [_eval(item, data, reads, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    if len(node) != 1:
        raise ValueError("un nodo JSON Logic tiene exactamente una clave")
    ((op, raw),) = node.items()
    handler = OPS.get(op)
    if handler is None:
        raise ValueError(f"operador no permitido: {op}")
    return handler(raw if isinstance(raw, list) else [raw], data, reads, depth + 1)


def _lookup(data: JsonValue, path: str) -> tuple[bool, JsonValue]:
    current = data
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return False, None
    return True, current


def _path(arg: JsonValue) -> str:
    if not isinstance(arg, str):
        raise ValueError("la ruta debe ser un string")
    return arg


def _var(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    path = _path(args[0])
    reads.append(path)
    found, value = _lookup(data, path)
    if found:
        return value
    return _eval(args[1], data, reads, depth) if len(args) > 1 else None


def _missing(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    absent: list[JsonValue] = []
    for arg in args:
        path = _path(arg)
        reads.append(path)
        found, value = _lookup(data, path)
        if not found or value is None:
            absent.append(path)
    return absent


def _equality(negate: bool) -> _Op:
    def op(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
        same = _equal(_eval(args[0], data, reads, depth), _eval(args[1], data, reads, depth))
        return same != negate

    return op


def _compare(accept: Callable[[int], bool]) -> _Op:
    def op(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
        order = _order(_eval(args[0], data, reads, depth), _eval(args[1], data, reads, depth))
        return order is not None and accept(order)

    return op


def _and(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    result: JsonValue = None
    for arg in args:
        result = _eval(arg, data, reads, depth)
        if not truthy(result):
            return result
    return result


def _or(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    result: JsonValue = None
    for arg in args:
        result = _eval(arg, data, reads, depth)
        if truthy(result):
            return result
    return result


def _not(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    return not truthy(_eval(args[0], data, reads, depth))


def _in(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    needle = _eval(args[0], data, reads, depth)
    haystack = _eval(args[1], data, reads, depth)
    if isinstance(haystack, list):
        return any(_equal(needle, item) for item in haystack)
    if isinstance(haystack, str) and isinstance(needle, str):
        return needle in haystack
    return False


def _if(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    i = 0
    while i + 1 < len(args):
        if truthy(_eval(args[i], data, reads, depth)):
            return _eval(args[i + 1], data, reads, depth)
        i += 2
    return _eval(args[i], data, reads, depth) if i < len(args) else None


OPS: dict[str, _Op] = {
    "var": _var, "missing": _missing, "==": _equality(False), "!=": _equality(True),
    ">": _compare(lambda c: c > 0), ">=": _compare(lambda c: c >= 0),
    "<": _compare(lambda c: c < 0), "<=": _compare(lambda c: c <= 0),
    "and": _and, "or": _or, "!": _not, "in": _in, "if": _if,
}


def evaluate(expr: JsonValue, data: JsonValue, reads: list[str] | None = None) -> JsonValue:
    """Evalúa `expr` sobre `data`. Si se pasa `reads`, se llena con las rutas `var`/`missing` leídas (D11)."""
    return _eval(expr, data, reads if reads is not None else [], 0)
