"""Subconjunto cerrado de JSON Logic (M1 §3.10). M2 importa `JSONLOGIC_OPS`; nadie mantiene otra lista."""

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from agent_core.domain import JsonValue
from agent_core.flows.paths import Path, parse_path

JSONLOGIC_OPS: Mapping[str, tuple[int, int | None]] = MappingProxyType(
    {
        "var": (1, 2),
        "==": (2, 2),
        "!=": (2, 2),
        ">": (2, 2),
        ">=": (2, 2),
        "<": (2, 2),
        "<=": (2, 2),
        "and": (1, None),
        "or": (1, None),
        "!": (1, 1),
        "in": (2, 2),
        "if": (3, None),
        "missing": (1, None),
    }
)
MAX_DEPTH = 64
"""Anidamiento máximo de listas y objetos; más allá se reporta un problema en vez de recursar sin límite."""
_PATH_OPS = frozenset({"var", "missing"})


def _args(raw: JsonValue) -> list[JsonValue]:
    return raw if isinstance(raw, list) else [raw]


def _path_args(op: str, args: list[JsonValue]) -> list[JsonValue]:
    return args[:1] if op == "var" else args


def jsonlogic_problems(expr: JsonValue) -> list[str]:
    """Operadores no permitidos, aridad, nodos multiclave, rutas inválidas y exceso de profundidad."""
    problems: list[str] = []
    _check(expr, "", problems, 0)
    return problems


def _check(node: JsonValue, where: str, problems: list[str], depth: int) -> None:
    if not isinstance(node, (list, dict)):
        return
    if depth >= MAX_DEPTH:
        problems.append(f"{where or '/'}: profundidad máxima excedida ({MAX_DEPTH})")
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            _check(item, f"{where}/{i}", problems, depth + 1)
        return
    if len(node) != 1:
        problems.append(f"{where or '/'}: un nodo JSON Logic tiene exactamente una clave")
        return
    ((op, raw),) = node.items()
    here = f"{where}/{op}"
    if op not in JSONLOGIC_OPS:
        problems.append(f"{here}: operador no permitido")
        return
    args = _args(raw)
    low, high = JSONLOGIC_OPS[op]
    if len(args) < low or (high is not None and len(args) > high) or (op == "if" and len(args) % 2 == 0):
        problems.append(f"{here}: aridad inválida ({len(args)} argumentos)")
    if op in _PATH_OPS:
        for i, arg in enumerate(_path_args(op, args)):
            if not isinstance(arg, str):
                problems.append(f"{here}/{i}: la ruta debe ser un string")
                continue
            try:
                path = parse_path(arg)
            except ValueError:
                path = None
            if path is None:
                problems.append(f"{here}/{i}: ruta inválida {arg!r}")
        if op == "var":
            for i, arg in enumerate(args[1:], start=1):
                _check(arg, f"{here}/{i}", problems, depth + 1)
        return
    for i, arg in enumerate(args):
        _check(arg, f"{here}/{i}", problems, depth + 1)


def _walk(node: JsonValue, where: str, depth: int = 0) -> Iterator[tuple[str, str, JsonValue]]:
    """Eventos ("path" | "literal", puntero, valor). No desciende más allá de `MAX_DEPTH`."""
    if isinstance(node, (list, dict)) and depth >= MAX_DEPTH:
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk(item, f"{where}/{i}", depth + 1)
    elif isinstance(node, dict):
        if len(node) != 1:
            for key, value in node.items():
                yield from _walk(value, f"{where}/{key}", depth + 1)
            return
        ((op, raw),) = node.items()
        args = _args(raw)
        here = f"{where}/{op}"
        if op in _PATH_OPS:
            for i, arg in enumerate(_path_args(op, args)):
                yield ("path", f"{here}/{i}", arg)
            if op == "var":
                for i, arg in enumerate(args[1:], start=1):
                    yield from _walk(arg, f"{here}/{i}", depth + 1)
        else:
            for i, arg in enumerate(args):
                yield from _walk(arg, f"{here}/{i}", depth + 1)
    else:
        yield ("literal", where, node)


def exceeds_max_depth(expr: JsonValue) -> bool:
    """True si `_walk` y `jsonlogic_problems` dejarían de descender por `MAX_DEPTH`. Iterativa."""
    stack: list[tuple[JsonValue, int]] = [(expr, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (list, dict)):
            continue
        if depth >= MAX_DEPTH:
            return True
        if isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)
        elif len(node) != 1:
            stack.extend((value, depth + 1) for value in node.values())
        else:
            ((op, raw),) = node.items()
            args = _args(raw)
            children = args[1:] if op == "var" else [] if op in _PATH_OPS else args
            stack.extend((arg, depth + 1) for arg in children)
    return False


def expr_paths(expr: JsonValue) -> list[Path]:
    """Rutas válidas que lee la expresión (`var` y `missing`). Ignora lo que excede `MAX_DEPTH`."""
    paths: list[Path] = []
    for kind, _, value in _walk(expr, ""):
        if kind != "path" or not isinstance(value, str):
            continue
        try:
            path = parse_path(value)
        except ValueError:
            continue
        if path is not None:
            paths.append(path)
    return paths


def expr_literals(expr: JsonValue) -> Iterator[tuple[str, JsonValue]]:
    """Hojas literales: todo escalar salvo las rutas de `var`/`missing` (el defecto de `var` sí cuenta)."""
    for kind, where, value in _walk(expr, ""):
        if kind == "literal":
            yield (where or "/", value)
