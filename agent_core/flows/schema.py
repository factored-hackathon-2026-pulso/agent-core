"""`parse_flow` y las comprobaciones de G0-01 que Pydantic no hace (M1 §3.4, §3.4.1)."""

import re
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from agent_core.domain import (
    PRODUCTION_NODE_KINDS,
    AgentNode,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EndNode,
    EscalateNode,
    Flow,
    JsonValue,
    Node,
    RespondNode,
    RuleNode,
    SlotValidator,
    ToolNode,
    VerifyNode,
    node_kind,
)
from agent_core.flows.jsonlogic import jsonlogic_problems
from agent_core.flows.paths import bad_paths, parse_path
from agent_core.flows.violations import MAX_ECHO, FlowSchemaError, Violation, clip, pointer_segment

try:  # parser interno de `re` (Python 3.11+); sin él la verificación de regex falla cerrada
    from re import _constants as _C  # type: ignore[attr-defined]
    from re import _parser as _PARSER  # type: ignore[attr-defined]

    _REPEATS = (_C.MAX_REPEAT, _C.MIN_REPEAT)
    _MAXREPEAT = int(_C.MAXREPEAT)
except ImportError:  # pragma: no cover
    _PARSER = None
    _REPEATS = (None, None)
    _MAXREPEAT = 0

VALIDATOR_TYPES = frozenset({"string", "integer", "decimal", "date", "boolean"})
MAX_REGEX = 200
MAX_ERRORS = 200
_MESSAGES = {
    "missing": "campo obligatorio",
    "extra_forbidden": "campo no permitido",
    "alias_invalid": "alias de agente inválido (formato del selector de agente)",
}
_UNKNOWN_TAG = ("union_tag_invalid", "union_tag_not_found")


def error_message(error_type: str) -> str:
    """Mensaje fijo en español para un tipo de error de Pydantic (nunca su texto ni su valor de entrada)."""
    return _MESSAGES.get(error_type, f"valor inválido ({clip(error_type)})")


def _pointer(loc: tuple[int | str, ...]) -> str:
    """JSON Pointer del error. Pydantic inserta la etiqueta del discriminador tras el índice del nodo
    (`nodes/<i>/<tag>/…`); no existe en el documento, así que se descarta."""
    if len(loc) > 2 and loc[0] == "nodes" and isinstance(loc[1], int):
        loc = (*loc[:2], *loc[3:])
    parts = (pointer_segment(str(p)) for p in loc)
    return "/" + "/".join(parts)


def _label(raw: JsonValue) -> str | None:
    if isinstance(raw, dict) and isinstance(raw.get("id"), str) and isinstance(raw.get("version"), str):
        return clip(f"{raw['id']}@{raw['version']}", 2 * MAX_ECHO)
    return None


def _raw_node_id(raw: JsonValue, loc: tuple[int | str, ...]) -> str | None:
    if len(loc) < 2 or loc[0] != "nodes" or not isinstance(loc[1], int) or not isinstance(raw, dict):
        return None
    nodes = raw.get("nodes")
    if isinstance(nodes, list) and 0 <= loc[1] < len(nodes):
        entry = nodes[loc[1]]
        ident = entry.get("id") if isinstance(entry, dict) else None
        return clip(ident) if isinstance(ident, str) else None
    return None


def _located(source: str | None, pointer: str) -> str:
    return f"{source}#{pointer}" if source else pointer


def parse_flow(raw: JsonValue, *, source: str | None = None) -> Flow:
    """Construye el `Flow` o lanza `FlowSchemaError` con todas las violaciones G0-01/G0-09."""
    label = _label(raw)
    try:
        flow = Flow.model_validate(raw)
    except ValidationError as exc:
        violations = []
        for err in exc.errors(include_url=False, include_context=False, include_input=False)[:MAX_ERRORS]:
            loc = tuple(err["loc"])
            rule = (
                "G0-09"
                if err["type"] == "missing" and loc[-2:] == ("generate", "fallback_template_ref")
                else "G0-01"
            )
            if err["type"] in _UNKNOWN_TAG:
                message = "nodo fuera del catálogo"
            else:
                message = error_message(err["type"])
            violations.append(
                Violation(
                    rule=rule,
                    flow=label,
                    node_id=_raw_node_id(raw, loc),
                    path=_located(source, _pointer(loc)),
                    message=message,
                )
            )
        raise FlowSchemaError(violations) from exc
    except (RecursionError, ValueError, TypeError) as exc:
        # Entrada hostil (anidamiento extremo, tipos que Pydantic no logra recorrer): nunca escapa otro error.
        raise FlowSchemaError(
            [
                Violation(
                    rule="G0-01",
                    flow=label,
                    path=_located(source, ""),
                    message=f"el flow no se pudo procesar ({type(exc).__name__})",
                )
            ]
        ) from exc
    problems = schema_violations(flow)
    if problems:
        raise FlowSchemaError(
            [v.model_copy(update={"flow": label, "path": _located(source, v.path or "")}) for v in problems]
        )
    return flow


def _jsonlogic_fields(node: Node) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(node, RuleNode) and node.config.expr is not None:
        yield ("/config/expr", node.config.expr)
    if isinstance(node, VerifyNode):
        yield ("/config/predicate", node.config.predicate)
    if isinstance(node, EscalateNode) and node.config.priority_expr is not None:
        yield ("/config/priority_expr", node.config.priority_expr)


def _required_paths(node: Node) -> Iterator[tuple[str, str]]:
    """Campos que solo admiten rutas (nunca literales)."""
    if isinstance(node, RespondNode) and node.config.generate is not None:
        for i, text in enumerate(node.config.generate.allowed_facts):
            yield (f"/config/generate/allowed_facts/{i}", text)
    if isinstance(node, EndNode) and node.config.output_map:
        for key, text in sorted(node.config.output_map.items()):
            yield (f"/config/output_map/{pointer_segment(key)}", text)
    if isinstance(node, DecideNode | AgentNode) and node.config.input_view:
        for i, text in enumerate(node.config.input_view):
            yield (f"/config/input_view/{i}", text)
    if isinstance(node, DecideNode) and node.config.choices_from is not None:
        yield ("/config/choices_from", node.config.choices_from)
    if isinstance(node, VerifyNode) and node.config.by.startswith("fact:"):
        yield ("/config/by", node.config.by.removeprefix("fact:"))


def _args_fields(node: Node) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(node, ToolNode):
        yield ("/config/args", dict(node.config.args))
    if isinstance(node, ConfirmNode):
        yield ("/config/action/args", dict(node.config.action.args))


def _has_repeat(pattern: Any) -> bool:
    """True si el subpatrón contiene, a cualquier profundidad, un cuantificador ilimitado."""
    stack = [pattern]
    while stack:
        current = stack.pop()
        for op, av in current:
            if op in _REPEATS and av[1] >= _MAXREPEAT:
                return True
            stack.extend(_children(op, av))
    return False


def _holds_subpattern(value: Any) -> bool:
    """True si `value` es un subpatrón o una secuencia que contiene uno (a cualquier profundidad)."""
    if isinstance(value, _PARSER.SubPattern):
        return True
    return isinstance(value, tuple | list) and any(_holds_subpattern(item) for item in value)


def _children(op: Any, av: Any) -> list[Any]:
    """Subpatrones de un nodo del árbol de `re`. Falla cerrado: un operador con subpatrones que no se
    conoce (versiones futuras de Python) lanza `ValueError` en vez de omitirse en silencio."""
    if op in _REPEATS or op == _PARSER.POSSESSIVE_REPEAT:
        return [av[2]]
    if op == _PARSER.SUBPATTERN:
        return [av[3]]
    if op == _PARSER.ATOMIC_GROUP:
        return [av]
    if op == _PARSER.BRANCH:
        return list(av[1])
    if op in (_PARSER.ASSERT, _PARSER.ASSERT_NOT):
        return [av[1]]
    if op == _PARSER.GROUPREF_EXISTS:
        return [av[1]] if av[2] is None else [av[1], av[2]]
    if _holds_subpattern(av):
        raise ValueError(f"operador de regex sin manejar: {op}")
    return []


_UNVERIFIABLE = "no se puede verificar la regex"


def _regex_safety(pattern: str) -> list[str]:
    """Rechaza cuantificadores ilimitados anidados (`(a+)+`, `(.*)*`). Falla cerrado.

    Heurística de mejor esfuerzo: NO detecta alternancias ambiguas como `(a|aa)+` ni cuantificadores
    adyacentes solapados. El runtime que compile la regex debe imponer igualmente un timeout y un tope
    de longitud de entrada. Si el parser interno no está disponible o el árbol tiene una forma
    inesperada, devuelve un problema (no se acepta lo que no se pudo verificar).
    """
    if _PARSER is None:
        return [f"{_UNVERIFIABLE} (parser no disponible)"]
    try:
        parsed = _PARSER.parse(pattern)
        stack: list[Any] = [parsed]
        while stack:
            current = stack.pop()
            for op, av in current:
                if op in _REPEATS and av[1] >= _MAXREPEAT and _has_repeat(av[2]):
                    return ["la regex tiene cuantificadores ilimitados anidados (retroceso catastrófico)"]
                stack.extend(_children(op, av))
    except (AttributeError, IndexError, TypeError, ValueError, RecursionError, OverflowError, re.error):
        return [f"{_UNVERIFIABLE} (forma inesperada)"]
    return []


def validator_problems(validator: SlotValidator) -> list[str]:
    value = validator.value
    if validator.kind == "type":
        return (
            []
            if isinstance(value, str) and value in VALIDATOR_TYPES
            else [f"tipo de validador desconocido: {clip(value)!r}"]
        )
    if validator.kind == "regex":
        if not isinstance(value, str) or len(value) > MAX_REGEX:
            return [f"la regex debe ser un string de hasta {MAX_REGEX} caracteres"]
        try:
            re.compile(value)
        except re.error as exc:
            return [f"regex inválida: {clip(str(exc))}"]
        return _regex_safety(value)
    if validator.kind == "enum":
        items = value if isinstance(value, list) else []
        strings = [v for v in items if isinstance(v, str)]
        ok = bool(strings) and len(strings) == len(items) and len(set(strings)) == len(strings)
        return [] if ok else ["el enum debe ser una lista no vacía de strings sin repetidos"]
    # `decide`: el intérprete aún no lo ejecuta (m02 D14: no se sabe qué campo de la decisión valida); se
    # rechaza aquí para que una release no se publique con un flow que fallaría en ejecución.
    return ["el validador decide no está soportado todavía (m02 D14)"]


def schema_violations(flow: Flow) -> list[Violation]:
    """G0-01 más allá del esquema Pydantic. `path` es un JSON Pointer dentro del flow.

    Devuelve a lo sumo `MAX_ERRORS` violaciones más un aviso final con la cantidad omitida.
    """
    found: list[Violation] = []
    if not flow.nodes:
        found.append(Violation(rule="G0-01", path="/nodes", message="el flow no tiene nodos"))
    seen: set[str] = set()
    for index, node in enumerate(flow.nodes):
        where = f"/nodes/{index}"

        def add(message: str, sub: str = "", _node: Node = node, _where: str = where) -> None:
            found.append(Violation(rule="G0-01", node_id=_node.id, path=_where + sub, message=message))

        if node.id in seen:
            add(f"id de nodo duplicado: {clip(node.id)}")
        seen.add(node.id)
        if node_kind(node) in PRODUCTION_NODE_KINDS:
            add("tipo de producción no habilitado")
            continue
        for sub, expr in _jsonlogic_fields(node):
            for problem in jsonlogic_problems(expr):
                add(f"JSON Logic {clip(problem, 2 * MAX_ECHO)}", sub)
        for sub, text in _required_paths(node):
            try:
                path = parse_path(text)
            except ValueError:
                add(f"ruta mal formada: {clip(text)!r}", sub)
                continue
            if path is None:
                add(f"se esperaba una ruta y llegó un literal: {clip(text)!r}", sub)
        for sub, value in _args_fields(node):
            for text in bad_paths(value):
                add(f"ruta mal formada: {clip(text)!r}", sub)
        if isinstance(node, CollectNode) and node.config.validator is not None:
            for problem in validator_problems(node.config.validator):
                add(problem, "/config/validator")
    if len(found) > MAX_ERRORS:  # cota determinista: el orden natural (por nodo) y luego el aviso
        omitted = len(found) - MAX_ERRORS
        found = [*found[:MAX_ERRORS], Violation(rule="G0-01", message=f"se omitieron {omitted} errores más")]
    return found
