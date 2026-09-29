# M1 — Esquema de flows y validación estática: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `agent_core.flows` (M1). Incluye:
- carga segura de un registro de autoría en YAML;
- `parse_flow`;
- reglas G0-01…G0-16 y chequeos por agente;
- `derive_claims` y el análisis compartido con M2;
- `pin_release` para la demo;
- CLI `agentcore validate`.

**Architecture:** Todo es función pura sobre tipos de M0, salvo la lectura de archivos (`load_registry`).
- Un `FlowGraph` inmutable resuelve la alcanzabilidad con BFS quitando aristas.
- Cada regla es una función `(Ctx) -> Iterable[Violation]` registrada en una tupla (`FLOW_RULES`), así que agregar una regla nunca toca las demás.
- La vista del registro (`RegistryView.resolve(kind, ref)`) tiene dos implementaciones:
  - `AuthoringRegistry`, que resuelve rangos semver;
  - `release_view`, para runtime, que usa las referencias exactas de una `Release`.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML (`SafeLoader` subclaseado), hypothesis, pytest, ruff, mypy strict, import-linter.

**Spec:** `docs/specs/motor/m01-validacion-estatica.md` (rev. 2). El spec manda sobre este plan. Lee también:
- `docs/specs/motor/00-indice.md`;
- `docs/specs/motor/m00-dominio-y-contratos.md` §2.2, §2.4, §2.5, §2.7;
- ADR 0004, 0007, 0009, 0011 y 0016.

## Global Constraints

- **Stack:** Python `>=3.12,<3.13`, Pydantic v2. Única dependencia nueva de runtime: `pyyaml`; en dev, `types-PyYAML`.
- **Fronteras:** `agent_core.flows` solo importa `agent_core.domain` y `agent_core.ports` (`.importlinter`, contrato `flows`). `agent_core` nunca importa `testing`.
- **Tiempo y aleatoriedad:** nunca `datetime.now()`, `time.*`, `uuid4()`, `random` ni `secrets`. En la CLI, el tiempo sale de `SystemClock().monotonic_ns()`.
- **Números:** nunca `float` en valores cargados. Números con decimales → `Decimal`.
- **Totalidad:** `validate_*` y `derive_claims` nunca lanzan ante un flow mal formado; devuelven violaciones.
- **Orden de las violaciones:** total por `Violation.sort_key()` = `(rule, flow, node_id, path, message)`, sin duplicados. Los mensajes no dependen del orden de los nodos: toda lista dentro de un mensaje va ordenada.
- **Mensajes:** en español, sin datos de cliente.
- **Datos:** solo sintéticos. Nunca datos reales del dataset ni las credenciales AWS del diccionario de datos.
- **Comandos:** `uv run pytest tests/m01`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore validate tests/m01/fixtures/registry`.
- **Commits:** mensajes en español que terminan con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Si el ejecutor es otro modelo, pone su nombre real (regla del ledger de M0).

## Prerrequisito (bloqueante)

M0 debe estar implementado. El plan `docs/superpowers/plans/2026-09-28-m0-dominio-y-contratos.md` quedó en la rev. 4 del spec de M0; **además** debe tener la rev. 5 (`EntityKind.model_profile`, `ModelProfile`, `ModelPrice`, `Prompt.model_profile`). La Task 0 lo verifica. Si falla, detente y repórtalo: completar M0 no es parte de este plan.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `agent_core/flows/violations.py` | `Violation`, `sort_violations`, `FlowSchemaError` |
| `agent_core/flows/paths.py` | gramática de rutas (`Path`, `parse_path`, `value_paths`, `bad_paths`) y de plantillas (`template_vars`) |
| `agent_core/flows/jsonlogic.py` | `JSONLOGIC_OPS`, `jsonlogic_problems`, `expr_paths`, `expr_literals` |
| `agent_core/flows/yaml_loader.py` | `load_yaml`, `YamlError`, `MAX_BYTES` |
| `agent_core/flows/view.py` | `RegistryView`, `ENTITY_TYPES`, `satisfies`, `best_version`, `release_view` |
| `agent_core/flows/refs.py` | `RefSite`, `node_ref_sites`, `flow_ref_sites`, `agent_ref_sites`, `entity_ref_sites`, `pointer_str` |
| `agent_core/flows/registry.py` | `ReleaseAgent`, `ReleaseDecl`, `AuthoringRegistry`, `load_registry` |
| `agent_core/flows/schema.py` | `parse_flow`, `schema_violations` (G0-01, G0-09) |
| `agent_core/flows/graph.py` | `FlowGraph`, `is_waiting`, `writes_by_confirm`, `verify_of`, `flow_mode` |
| `agent_core/flows/context.py` | `Ctx` (flow + vista + grafo + índice), `Rule` |
| `agent_core/flows/claims.py` | `derive_claims` |
| `agent_core/flows/rules/structure.py` | G0-02, G0-03, G0-04 |
| `agent_core/flows/rules/writes.py` | G0-05 |
| `agent_core/flows/rules/exits.py` | G0-06 |
| `agent_core/flows/rules/phase5.py` | G0-07, G0-08, G0-10, G0-11, G0-13…G0-16 |
| `agent_core/flows/agent.py` | `validate_flow_for_agent` (G0-12, AG-01), `validate_agent` |
| `agent_core/flows/validate.py` | `FLOW_RULES`, `validate_flow`, `validate_registry` |
| `agent_core/flows/pin.py` | `PinnedRelease`, `pin_release` |
| `agent_core/flows/cli_validate.py` | `run_validate` (lógica de la CLI) |
| `agent_core/flows/__init__.py` | interfaz pública |
| `agent_core/cli.py` | subcomando `validate` |
| `testing/fakes/registry_dir.py` | `registry_from_directory` |
| `tests/m01/cases.py` | flows y entidades sintéticos de prueba, helpers |
| `tests/m01/fixtures/registry/**` | registro de ejemplo con `disputa-cargo` |
| `tests/m01/test_*.py` | pruebas T-M1-* |

---

### Task 0: Prerrequisitos, dependencias y esqueleto

**Files:**
- Modify: `pyproject.toml` (vía `uv add`)
- Create: `tests/m01/__init__.py`, `tests/m01/test_prereqs.py`, `agent_core/flows/rules/__init__.py`
- Modify: `agent_core/flows/__init__.py`

**Interfaces:**
- Consumes: M0 completo (ver Prerrequisito).
- Produces: nada que usen otras tareas salvo la dependencia `yaml`.

- [ ] **Step 1: Escribir la prueba de prerrequisitos**

`tests/m01/__init__.py`: vacío.

`tests/m01/test_prereqs.py`:

```python
"""M1 necesita estos nombres de M0 (rev. 5). Si falla, M0 no está completo: detente y repórtalo."""

import agent_core.domain as domain
from agent_core.domain import EntityKind

NEEDED = [
    "Agent", "DecisionModelDef", "DECLARABLE", "DomainError", "EntityRef", "Flow", "InjectionRuleset", "Interrupt",
    "JsonValue", "LanguageDetection", "ModelProfile", "Outcome", "Policy", "Prompt", "RefSpec", "RegistryEntity",
    "Release", "RiskClass", "SchemaError", "StartFlowAction", "Template", "ToolDef", "is_declarable",
    "require_exact_refs", "node_kind", "RESULTS", "PRODUCTION_NODE_KINDS", "Node", "DecideNode", "RuleNode",
    "CollectNode", "ToolNode", "WriteToolNode", "ConfirmNode", "VerifyNode", "RespondNode", "EscalateNode",
    "EndNode", "AgentNode", "SlotValidator",
]


def test_domain_exports_what_m1_needs() -> None:
    missing = [name for name in NEEDED if not hasattr(domain, name)]
    assert missing == []


def test_model_profile_kind_exists() -> None:
    assert EntityKind.model_profile == "model_profile"


def test_ports_and_fakes_exist() -> None:
    from agent_core.adapters.system_clock import SystemClock
    from agent_core.ports import RegistryPort
    from testing.fakes import InMemoryRegistry

    assert SystemClock and RegistryPort and InMemoryRegistry
```

- [ ] **Step 2: Correrla**

Run: `uv run pytest tests/m01/test_prereqs.py -v`
Esperado: PASS. Si falla, **detente** y reporta la lista `missing` o el import que falla. No agregues nada a M0 desde este plan.

- [ ] **Step 3: Agregar dependencias**

```bash
uv add "pyyaml>=6.0.2,<7"
uv add --dev "types-PyYAML>=6.0.12"
```

- [ ] **Step 4: Esqueleto del paquete**

`agent_core/flows/__init__.py`:

```python
"""M1 — esquema de flows y validación estática (docs/specs/motor/m01-validacion-estatica.md)."""
```

`agent_core/flows/rules/__init__.py`:

```python
"""Reglas G0 de M1, una función por regla."""
```

- [ ] **Step 5: Verificar y commit**

```bash
uv run pytest tests/m01 && uv run ruff check . && uv run mypy && uv run lint-imports
git add pyproject.toml uv.lock agent_core/flows tests/m01
git commit -m "build(m1): dependencia pyyaml y esqueleto de agent_core.flows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Violaciones, rutas, plantillas y JSON Logic

**Files:**
- Create: `agent_core/flows/violations.py`, `agent_core/flows/paths.py`, `agent_core/flows/jsonlogic.py`
- Test: `tests/m01/test_paths.py`, `tests/m01/test_jsonlogic.py`

**Interfaces:**
- Consumes: `SchemaError`, `JsonValue` (M0).
- Produces:
  - `Violation(rule: str, flow: str | None = None, node_id: str | None = None, path: str | None = None, message: str)`, con `.sort_key()`;
  - `sort_violations(Iterable[Violation]) -> list[Violation]`;
  - `FlowSchemaError(violations)`, subclase de `SchemaError`, con `.violations: list[Violation]`;
  - `Path(ns, name, rest, raw)`, con `.whole_fact`; `Namespace`;
  - `looks_like_path(str) -> bool`;
  - `parse_path(str) -> Path | None` (lanza `ValueError` si parece ruta y no parsea);
  - `value_paths(JsonValue, *, strict=True) -> list[Path]`;
  - `bad_paths(JsonValue) -> list[str]`;
  - `template_vars(str) -> frozenset[str]` (lanza `ValueError`);
  - `JSONLOGIC_OPS: Mapping[str, tuple[int, int | None]]`;
  - `jsonlogic_problems(JsonValue) -> list[str]`;
  - `expr_paths(JsonValue) -> list[Path]`;
  - `expr_literals(JsonValue) -> Iterator[tuple[str, JsonValue]]` (puntero relativo, valor).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_paths.py`:

```python
import pytest

from agent_core.flows.paths import bad_paths, parse_path, template_vars, value_paths
from agent_core.flows.violations import FlowSchemaError, Violation, sort_violations


@pytest.mark.parametrize(
    ("text", "ns", "name", "rest"),
    [
        ("slots.desc", "slots", "desc", ()),
        ("facts.datos", "facts", "datos", ()),
        ("facts.datos.value", "facts", "datos", ("value",)),
        ("facts.tx.value.transaction_id", "facts", "tx", ("value", "transaction_id")),
        ("decisions.d.campo", "decisions", "d", ("campo",)),
        ("readback", "readback", None, ()),
        ("readback.status", "readback", None, ("status",)),
    ],
)
def test_parse_path_valid(text: str, ns: str, name: str | None, rest: tuple[str, ...]) -> None:
    path = parse_path(text)
    assert path is not None
    assert (path.ns, path.name, path.rest, path.raw) == (ns, name, rest, text)


@pytest.mark.parametrize("text", ["USD", "hola", "", "slotsx", "factsy.a"])
def test_literals_are_not_paths(text: str) -> None:
    assert parse_path(text) is None


@pytest.mark.parametrize("text", ["slots.a.b", "facts.X.value", "facts.a.valor", "decisions.d", "slots.", "readback."])
def test_malformed_paths_raise(text: str) -> None:
    with pytest.raises(ValueError):
        parse_path(text)


def test_whole_fact() -> None:
    whole, with_value = parse_path("facts.a"), parse_path("facts.a.value")
    assert whole is not None and whole.whole_fact
    assert with_value is not None and not with_value.whole_fact


def test_value_paths_recursive() -> None:
    args = {"a": "slots.x", "b": ["facts.y.value", {"c": "decisions.d.campo"}], "d": "USD", "e": 3}
    assert [p.raw for p in value_paths(args)] == ["slots.x", "facts.y.value", "decisions.d.campo"]


def test_value_paths_strict_and_bad_paths() -> None:
    args = {"a": "slots.x.y", "b": "slots.ok"}
    with pytest.raises(ValueError):
        value_paths(args)
    assert [p.raw for p in value_paths(args, strict=False)] == ["slots.ok"]
    assert bad_paths(args) == ["slots.x.y"]


def test_template_vars() -> None:
    text = "Radicado {{ facts.pqr.value.id }} para {{slots.nombre}}. {{ facts.pqr.value.id }}"
    assert template_vars(text) == frozenset({"facts.pqr.value.id", "slots.nombre"})
    assert template_vars("sin variables") == frozenset()


@pytest.mark.parametrize("text", ["{{ }}", "{{ USD }}", "a {{ facts.x.value", "a }} b", "{{ slots.a.b }}", "{{ {x} }}"])
def test_template_vars_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        template_vars(text)


def test_violation_order_and_dedup() -> None:
    a = Violation(rule="G0-05", flow="f@1.0.0", node_id="b", message="x")
    b = Violation(rule="G0-03", flow="f@1.0.0", node_id="z", message="y")
    c = Violation(rule="G0-05", flow="f@1.0.0", node_id="a", message="x")
    assert sort_violations([a, b, c, a]) == [b, c, a]


def test_flow_schema_error_carries_sorted_violations() -> None:
    err = FlowSchemaError([Violation(rule="G0-09", message="b"), Violation(rule="G0-01", message="a")])
    assert [v.rule for v in err.violations] == ["G0-01", "G0-09"]
    assert "G0-01" in str(err)
```

`tests/m01/test_jsonlogic.py`:

```python
from decimal import Decimal
from typing import Any

import pytest

from agent_core.flows.jsonlogic import JSONLOGIC_OPS, expr_literals, expr_paths, jsonlogic_problems


def test_operator_list_is_closed() -> None:
    assert set(JSONLOGIC_OPS) == {"var", "==", "!=", ">", ">=", "<", "<=", "and", "or", "!", "in", "if", "missing"}


@pytest.mark.parametrize(
    "expr",
    [
        {"==": [{"var": "readback.status"}, "Open"]},
        {"and": [{">": [{"var": "facts.m.value"}, {"var": "facts.n.value"}]}, {"!": {"var": "slots.x"}}]},
        {"if": [{"var": "slots.a"}, 1, {"var": "slots.b"}, 2, 3]},
        {"missing": ["slots.a", "facts.b.value"]},
        {"var": ["slots.a", "defecto"]},
        None,
    ],
)
def test_valid_expressions(expr: Any) -> None:
    assert jsonlogic_problems(expr) == []


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ({"+": [1, 2]}, "operador no permitido"),
        ({"==": [1]}, "aridad"),
        ({"if": [True, 1]}, "aridad"),
        ({"==": [1, 2], "!=": [1, 2]}, "exactamente una clave"),
        ({"var": "USD"}, "ruta inválida"),
        ({"var": "slots.a.b"}, "ruta inválida"),
        ({"var": 3}, "string"),
        ({"and": [{"merge": [1]}]}, "operador no permitido"),
    ],
)
def test_invalid_expressions(expr: Any, fragment: str) -> None:
    problems = jsonlogic_problems(expr)
    assert problems and fragment in problems[0]


def test_expr_paths_and_literals() -> None:
    expr = {"and": [{">": [{"var": "facts.m.value"}, Decimal("500")]}, {"in": [{"var": ["slots.t", "x"]}, ["a", None]]},
                    {"missing": ["slots.z"]}]}
    assert [p.raw for p in expr_paths(expr)] == ["facts.m.value", "slots.t", "slots.z"]
    assert [value for _, value in expr_literals(expr)] == [Decimal("500"), "x", "a", None]
    pointers = [where for where, _ in expr_literals(expr)]
    assert pointers[0] == "/and/0/>/1"
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_paths.py tests/m01/test_jsonlogic.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.paths'`.

- [ ] **Step 3: Implementar `violations.py`**

```python
"""Diagnóstico de la validación estática (M1 §2)."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from agent_core.domain import SchemaError


class Violation(BaseModel):
    """Una violación de una regla G0 o de un chequeo por agente. Nunca lleva datos de cliente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: str
    flow: str | None = None
    node_id: str | None = None
    path: str | None = None
    message: str

    def sort_key(self) -> tuple[str, str, str, str, str]:
        return (self.rule, self.flow or "", self.node_id or "", self.path or "", self.message)


def sort_violations(violations: Iterable[Violation]) -> list[Violation]:
    """Orden total y sin duplicados (M1 §4)."""
    return sorted(set(violations), key=Violation.sort_key)


class FlowSchemaError(SchemaError):
    """`parse_flow` no pudo construir el `Flow` (G0-01 o G0-09)."""

    def __init__(self, violations: Iterable[Violation]) -> None:
        self.violations = sort_violations(violations)
        super().__init__("; ".join(f"{v.rule} {v.path or ''}: {v.message}" for v in self.violations))
```

- [ ] **Step 4: Implementar `paths.py`**

```python
"""Gramática única de rutas y de variables de plantilla (M1 §3.2, §3.3). M2 resuelve con estas mismas funciones."""

import re
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from agent_core.domain import JsonValue

Namespace = Literal["slots", "facts", "decisions", "readback"]

_NAME = r"[a-z][a-z0-9_]*"
_FIELD = r"[A-Za-z0-9_]+"
_PATTERNS: dict[str, re.Pattern[str]] = {
    "slots": re.compile(rf"^slots\.(?P<name>{_NAME})$"),
    "facts": re.compile(rf"^facts\.(?P<name>{_NAME})(?P<rest>\.value(?:\.{_FIELD})*)?$"),
    "decisions": re.compile(rf"^decisions\.(?P<name>{_NAME})(?P<rest>(?:\.{_FIELD})+)$"),
    "readback": re.compile(rf"^readback(?P<rest>(?:\.{_FIELD})*)$"),
}
_PREFIXES = ("slots.", "facts.", "decisions.", "readback.")
_VAR = re.compile(r"\{\{\s*(?P<body>[^{}]*?)\s*\}\}")


class Path(BaseModel):
    """Ruta parseada. `name` es el hecho, slot o decisión que lee (None en `readback`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ns: Namespace
    name: str | None
    rest: tuple[str, ...] = ()
    raw: str

    @property
    def whole_fact(self) -> bool:
        """`facts.<n>` sin `.value`: solo válido en `allowed_facts`."""
        return self.ns == "facts" and not self.rest


def looks_like_path(text: str) -> bool:
    return text == "readback" or text.startswith(_PREFIXES)


def parse_path(text: str) -> Path | None:
    """None si `text` es un literal. `ValueError` si parece ruta y no cumple la gramática."""
    if not looks_like_path(text):
        return None
    ns = text.split(".", 1)[0]
    match = _PATTERNS[ns].match(text)
    if match is None:
        raise ValueError(f"ruta mal formada: {text!r}")
    groups = match.groupdict()
    rest = tuple(part for part in (groups.get("rest") or "").split(".") if part)
    return Path(ns=cast(Namespace, ns), name=groups.get("name"), rest=rest, raw=text)


def _strings(value: JsonValue) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    return []


def value_paths(value: JsonValue, *, strict: bool = True) -> list[Path]:
    """Rutas dentro de un valor de `args` (recursivo en listas y objetos). Con `strict=False` omite las mal formadas."""
    paths: list[Path] = []
    for text in _strings(value):
        try:
            path = parse_path(text)
        except ValueError:
            if strict:
                raise
            continue
        if path is not None:
            paths.append(path)
    return paths


def bad_paths(value: JsonValue) -> list[str]:
    """Strings que parecen rutas y no parsean."""
    bad: list[str] = []
    for text in _strings(value):
        try:
            parse_path(text)
        except ValueError:
            bad.append(text)
    return bad


def _no_braces(chunk: str) -> None:
    if "{{" in chunk or "}}" in chunk:
        raise ValueError("'{{' o '}}' sin una variable válida")


def template_vars(text: str) -> frozenset[str]:
    """Rutas de las variables `{{ ruta }}` de una plantilla (M1 §3.3)."""
    found: set[str] = set()
    position = 0
    for match in _VAR.finditer(text):
        _no_braces(text[position : match.start()])
        body = match["body"]
        path = parse_path(body) if body else None
        if path is None:
            raise ValueError(f"variable de plantilla inválida: {{{{ {body} }}}}")
        found.add(path.raw)
        position = match.end()
    _no_braces(text[position:])
    return frozenset(found)
```

- [ ] **Step 5: Implementar `jsonlogic.py`**

```python
"""Subconjunto cerrado de JSON Logic (M1 §3.10). M2 importa `JSONLOGIC_OPS`; nadie mantiene otra lista."""

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from agent_core.domain import JsonValue
from agent_core.flows.paths import Path, parse_path

JSONLOGIC_OPS: Mapping[str, tuple[int, int | None]] = MappingProxyType(
    {
        "var": (1, 2), "==": (2, 2), "!=": (2, 2), ">": (2, 2), ">=": (2, 2), "<": (2, 2), "<=": (2, 2),
        "and": (1, None), "or": (1, None), "!": (1, 1), "in": (2, 2), "if": (3, None), "missing": (1, None),
    }
)
_PATH_OPS = frozenset({"var", "missing"})


def _args(raw: JsonValue) -> list[JsonValue]:
    return raw if isinstance(raw, list) else [raw]


def _path_args(op: str, args: list[JsonValue]) -> list[JsonValue]:
    return args[:1] if op == "var" else args


def jsonlogic_problems(expr: JsonValue) -> list[str]:
    """Operadores fuera de la lista, aridad inválida, nodos con varias claves y rutas inválidas."""
    problems: list[str] = []
    _check(expr, "", problems)
    return problems


def _check(node: JsonValue, where: str, problems: list[str]) -> None:
    if isinstance(node, list):
        for i, item in enumerate(node):
            _check(item, f"{where}/{i}", problems)
        return
    if not isinstance(node, dict):
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
                _check(arg, f"{here}/{i}", problems)
        return
    for i, arg in enumerate(args):
        _check(arg, f"{here}/{i}", problems)


def _walk(node: JsonValue, where: str) -> Iterator[tuple[str, str, JsonValue]]:
    """Eventos ("path" | "literal", puntero, valor)."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk(item, f"{where}/{i}")
    elif isinstance(node, dict):
        if len(node) != 1:
            for key, value in node.items():
                yield from _walk(value, f"{where}/{key}")
            return
        ((op, raw),) = node.items()
        args = _args(raw)
        here = f"{where}/{op}"
        if op in _PATH_OPS:
            for i, arg in enumerate(_path_args(op, args)):
                yield ("path", f"{here}/{i}", arg)
            if op == "var":
                for i, arg in enumerate(args[1:], start=1):
                    yield from _walk(arg, f"{here}/{i}")
        else:
            for i, arg in enumerate(args):
                yield from _walk(arg, f"{here}/{i}")
    else:
        yield ("literal", where, node)


def expr_paths(expr: JsonValue) -> list[Path]:
    """Rutas válidas que lee la expresión (argumentos de `var` y `missing`)."""
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
```

- [ ] **Step 6: Correr las pruebas**

Run: `uv run pytest tests/m01/test_paths.py tests/m01/test_jsonlogic.py -v`
Esperado: todas PASS.

- [ ] **Step 7: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01
git commit -m "feat(m1): violaciones, gramática de rutas y plantillas, subconjunto JSON Logic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Loader YAML seguro (T-M1-29)

**Files:**
- Create: `agent_core/flows/yaml_loader.py`
- Test: `tests/m01/test_yaml.py`

**Interfaces:**
- Consumes: `JsonValue` (M0).
- Produces: `load_yaml(data: str | bytes) -> JsonValue`, `YamlError(ValueError)`, `MAX_BYTES = 1048576`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_yaml.py`:

```python
from decimal import Decimal

import pytest

from agent_core.flows.yaml_loader import MAX_BYTES, YamlError, load_yaml


# T-M1-29
def test_yaml_12_booleans_and_keys_as_text() -> None:
    data = load_yaml("next: {yes: a, no: b, true: c, 1: d}\nflag: true\nword: yes\non: off\n")
    assert data == {"next": {"yes": "a", "no": "b", "true": "c", "1": "d"}, "flag": True, "word": "yes", "on": "off"}


def test_numbers_are_int_or_decimal() -> None:
    data = load_yaml("a: 500.00\nb: 012\nc: 1e3\nd: -7\ne: .5\n")
    assert data == {"a": Decimal("500.00"), "b": 12, "c": Decimal("1e3"), "d": -7, "e": Decimal(".5")}
    assert isinstance(data, dict)
    assert not any(isinstance(v, float) for v in data.values())


def test_dates_versions_and_nan_stay_strings() -> None:
    data = load_yaml("d: 2026-09-28\nn: .nan\ni: .inf\nv: 1.0.0\nt: PT5M\n")
    assert data == {"d": "2026-09-28", "n": ".nan", "i": ".inf", "v": "1.0.0", "t": "PT5M"}


def test_nulls() -> None:
    assert load_yaml("a: null\nb: ~\nc:\n") == {"a": None, "b": None, "c": None}


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2\n",
        "a: &x 1\nb: *x\n",
        "a: !!python/object/apply:os.system [echo]\n",
        "a: !!str 1\n",
        "a: 1\n---\nb: 2\n",
        "? [a]\n: 1\n",
        "a: [1, 2\n",
    ],
)
def test_rejected(text: str) -> None:
    with pytest.raises(YamlError):
        load_yaml(text)


def test_too_big() -> None:
    with pytest.raises(YamlError):
        load_yaml("a: " + "x" * MAX_BYTES)
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_yaml.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `yaml_loader.py`**

```python
"""Loader YAML seguro para el registro de autoría (M1 §3.10).

Resolvedores YAML 1.2 mínimos: booleanos solo `true`/`false`, sin fechas, números con decimales como `Decimal`,
claves como texto, sin claves duplicadas, sin alias, anclas ni etiquetas explícitas y un solo documento.
"""

import re
from decimal import Decimal
from typing import Any

import yaml

from agent_core.domain import JsonValue

MAX_BYTES = 1024 * 1024


class YamlError(ValueError):
    """Archivo YAML que el registro no admite."""


class _Loader(yaml.SafeLoader):
    yaml_implicit_resolvers: dict[Any, Any] = {}  # noqa: RUF012 — reemplaza los de SafeLoader (YAML 1.1)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:  # type: ignore[override]
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            line = key_node.start_mark.line + 1
            if not isinstance(key_node, yaml.ScalarNode):
                raise YamlError(f"línea {line}: una clave debe ser texto")
            key = str(key_node.value)
            if key in mapping:
                raise YamlError(f"línea {line}: clave duplicada {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _int(loader: yaml.SafeLoader, node: yaml.Node) -> int:
    return int(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


def _decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    return Decimal(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(r"^[-+]?[0-9]+$"), list("-+0123456789"))
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$|^[-+]?[0-9]+[eE][-+]?[0-9]+$"),
    list("-+0123456789."),
)
_Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:~|null|)$"), ["~", "n", ""])
_Loader.add_constructor("tag:yaml.org,2002:int", _int)
_Loader.add_constructor("tag:yaml.org,2002:float", _decimal)


def _precheck(raw: bytes) -> None:
    documents = 0
    for event in yaml.parse(raw, Loader=_Loader):
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
            if documents > 1:
                raise YamlError("un archivo del registro tiene un solo documento")
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise YamlError("alias y anclas no admitidos")
        if getattr(event, "tag", None) is not None:
            raise YamlError("etiquetas explícitas no admitidas")


def load_yaml(data: str | bytes) -> JsonValue:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise YamlError("archivo mayor a 1 MiB")
    try:
        _precheck(raw)
        loader = _Loader(raw)
        try:
            value: JsonValue = loader.get_single_data()
        finally:
            loader.dispose()
    except YamlError:
        raise
    except yaml.YAMLError as exc:
        raise YamlError(str(exc)) from exc
    return value
```

Notas para el implementador:
- Si `mypy` marca un `type: ignore` como innecesario (`warn_unused_ignores` en strict), quítalo. Si pide uno distinto, usa el código exacto que reporta.
- Si `ruff` no marca `RUF012` en esa línea, quita el `noqa`.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/m01/test_yaml.py -v`
Esperado: todas PASS.

- [ ] **Step 5: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows/yaml_loader.py tests/m01/test_yaml.py
git commit -m "feat(m1): loader YAML seguro (YAML 1.2, Decimal, sin duplicados ni alias)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Vista del registro, sitios de referencia y casos de prueba (T-M1-42)

**Files:**
- Create: `agent_core/flows/view.py`, `agent_core/flows/refs.py`, `agent_core/flows/registry.py` (solo `ReleaseAgent`, `ReleaseDecl` y `AuthoringRegistry`; `load_registry` llega en la Task 9)
- Create: `tests/m01/cases.py`
- Test: `tests/m01/test_view.py`

**Interfaces:**
- Consumes: `template_vars` (Task 1); entidades, `EntityKind`, `RefSpec`, `RegistryPort` y `Release` (M0).
- Produces:
  - `RegistryView` (Protocol) con `resolve(kind: EntityKind, ref: RefSpec) -> RegistryEntity | None`;
  - `ENTITY_TYPES: Mapping[EntityKind, type[BaseModel]]`;
  - `parse_version(str) -> tuple[int, int, int]`;
  - `satisfies(spec: str | None, version: str) -> bool`;
  - `best_version(spec, versions: Iterable[str]) -> str | None`;
  - `release_view(port: RegistryPort, release: Release) -> RegistryView`;
  - `RefSite(kind, ref, pointer: tuple[str | int, ...], node_id: str | None = None)`;
  - `node_ref_sites(node, index) -> list[RefSite]`, `flow_ref_sites(flow) -> list[RefSite]`, `agent_ref_sites(agent) -> list[RefSite]`, `entity_ref_sites(entity) -> list[RefSite]`;
  - `TEMPLATE_KINDS`; `pointer_str(pointer) -> str`;
  - `ReleaseAgent(agent: RefSpec, aliases: list[str] = ["prod"])`;
  - `ReleaseDecl(id, agents, flows, interrupts, language_detection, injection_ruleset, max_input_chars)`;
  - `AuthoringRegistry`: `from_entities(entities, releases=())`, `add(entity, source=None)`, `add_release(decl, source=None)`, `resolve`, `versions(kind, id) -> list[str]`, `get_exact(kind, id, version)`, `all(kind)`, `releases()`, `release(id)`, `source(kind, id, version) -> str | None`;
  - `tests/m01/cases.py`: `ENTITIES`, `registry(*extra)`, `base()`, `task_base()`, `node(d, id)`, `flow(d)`, `AGENT`, `agent(**over)`.

- [ ] **Step 1: Escribir `tests/m01/cases.py`**

```python
"""Flows y entidades sintéticos para las pruebas de M1. Cada prueba muta una copia de `base()`."""

from copy import deepcopy
from typing import Any

from agent_core.domain import (
    Agent,
    DecisionModelDef,
    Flow,
    ModelProfile,
    Policy,
    Prompt,
    RegistryEntity,
    Template,
    ToolDef,
)
from agent_core.flows.registry import AuthoringRegistry

_TEXTS = {
    "t/pedir": "¿Qué necesitas?",
    "t/resumen": "Voy a registrar tu solicitud.",
    "t/hecho": "Listo: {{ facts.verif.value.id }}",
    "t/hecho2": "Listo: {{ facts.verif2.value.id }}",
    "t/seguro": "No pudimos completar la solicitud.",
    "t/lee_res": "Resultado: {{ facts.res.value.id }}",
    "t/lee_estado": "Estado: {{ facts.estado.value.x }}",
    "t/lee_calc": "Monto: {{ facts.monto_calc.value }}",
    "t/lee_c": "Valor: {{ facts.c.value }}",
    "t/lee_datos": "Datos: {{ facts.datos.value.id }}",
    "t/lee_dec": "Elegido: {{ decisions.d.campo }}",
    "t/aclarar": "¿Puedes aclararlo?",
    "t/abstencion": "No puedo ayudarte con eso.",
    "t/traspaso": "Te paso con un asesor.",
    "t/acuse": "Lo anoto para después.",
    "t/oferta": "¿Seguimos con lo pendiente?",
    "t/idioma_no_soportado": "Atiendo en español y portugués.",
    "t/mensaje_largo": "El mensaje es muy largo.",
}


def _template(tid: str, text: str, locales: tuple[str, ...] = ("es", "pt")) -> Template:
    return Template.model_validate({"id": tid, "version": "1.0.0", "locales": {loc: text for loc in locales}})


def _tool(tid: str, risk: str, **extra: Any) -> ToolDef:
    return ToolDef.model_validate(
        {"id": tid, "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
         "idempotent": risk in ("read", "compute"), **extra}
    )


def _model(mid: str, calibrated: list[str], enum: list[str] | None = None) -> DecisionModelDef:
    return DecisionModelDef.model_validate(
        {"id": mid, "version": "1.0.0",
         "output_schema": {"type": "object", "properties": {"campo": {"enum": enum or ["a", "b"]}}},
         "calibrated_fields": calibrated, "providers": [{"provider": "rule"}], "calibration": {"method": "none"}}
    )


def _prompt(pid: str, profile: str) -> Prompt:
    return Prompt.model_validate(
        {"id": pid, "version": "1.0.0", "locales": {"es": "Responde.", "pt": "Responda."}, "model_profile": profile}
    )


ENTITIES: list[RegistryEntity] = [
    *(_template(tid, text) for tid, text in _TEXTS.items()),
    _template("t/solo_es", "Solo español.", ("es",)),
    _tool("leer", "read"),
    _tool("leer_escritura", "read"),
    _tool("calc", "compute"),
    _tool("escribir", "write_reversible", readback_by="idempotency_key"),
    _model("modelo", ["campo"]),
    _model("modelo_nc", []),
    _model("modelo_lc", ["campo"], ["a", "low_confidence"]),
    Policy.model_validate(
        {"id": "pol", "version": "1.0.0", "owner": "riesgo",
         "expr": {">": [{"var": "facts.datos.value.n"}, 500]}, "rationale": "sintética"}
    ),
    ModelProfile.model_validate(
        {"id": "perfil", "version": "1.0.0", "endpoint_alias": "demo", "model": "modelo-sintetico",
         "temperature": "0", "max_tokens": 400,
         "price": {"input_per_mtok": "1", "output_per_mtok": "2", "source": "sintético", "as_of": "2026-09-28"}}
    ),
    _prompt("p/gen", "perfil@1"),
    _prompt("p/sinperfil", "perfil_x@1"),
]


def registry(*extra: RegistryEntity) -> AuthoringRegistry:
    return AuthoringRegistry.from_entities([*ENTITIES, *extra])


def base() -> dict[str, Any]:
    """Flow conversacional válido: collect → lectura → confirm → escritura → verify → respond → end."""
    return deepcopy(
        {
            "id": "base",
            "version": "1.0.0",
            "priority": 10,
            "nodes": [
                {"id": "pedir", "type": "collect", "config": {"slot": "desc", "prompt_ref": "t/pedir"},
                 "next": {"ok": "buscar", "max_attempts": "esc"}},
                {"id": "buscar", "type": "tool",
                 "config": {"tool": "leer@1", "args": {"q": "slots.desc"}, "save_as": "datos"},
                 "next": {"ok": "confirmar", "error": "esc", "timeout": "esc", "denied": "esc"}},
                {"id": "confirmar", "type": "confirm",
                 "config": {"action": {"tool": "escribir@1",
                                       "args": {"q": "slots.desc", "ref": "facts.datos.value.id"}},
                            "summary_template": "t/resumen"},
                 "next": {"yes": "escribir", "no": "fin_cancelado", "unclear": "confirmar", "max_attempts": "esc"}},
                {"id": "escribir", "type": "tool", "config": {"action_from": "confirmar", "save_as": "res"},
                 "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}},
                {"id": "verificar", "type": "verify",
                 "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                            "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": "verif"},
                 "next": {"verified": "ok_msg", "failed": "esc"}},
                {"id": "ok_msg", "type": "respond", "config": {"template_ref": "t/hecho", "claims": ["confirmar"]},
                 "next": {"next": "fin"}},
                {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
                {"id": "fin_cancelado", "type": "end", "config": {"outcome": "cancelled"}},
                {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
            ],
        }
    )


def task_base() -> dict[str, Any]:
    """Flow de modo task válido, sin nodos que esperan."""
    return deepcopy(
        {
            "id": "tarea",
            "version": "1.0.0",
            "priority": 10,
            "nodes": [
                {"id": "buscar", "type": "tool", "config": {"tool": "leer@1", "args": {"q": "hola"}, "save_as": "datos"},
                 "next": {"ok": "fin_ok", "error": "fin_fallo", "timeout": "fin_fallo", "denied": "fin_fallo"}},
                {"id": "fin_ok", "type": "end",
                 "config": {"outcome": "completed", "output_map": {"dato": "facts.datos.value.id"}}},
                {"id": "fin_fallo", "type": "end", "config": {"outcome": "failed"}},
            ],
        }
    )


def node(d: dict[str, Any], node_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(n for n in d["nodes"] if n["id"] == node_id)
    return found


def flow(d: dict[str, Any]) -> Flow:
    return Flow.model_validate(d)


AGENT: dict[str, Any] = {
    "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "base@1",
    "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
    "supported_locales": ["es", "pt"], "default_locale": "es", "tools_allowed": ["leer@1", "escribir@1"],
    "budgets": {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
                "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000},
    "templates": {"clarify": "t/aclarar", "abstain": "t/abstencion", "handoff": "t/traspaso",
                  "pending_ack": "t/acuse", "pending_offer": "t/oferta",
                  "unsupported_language": "t/idioma_no_soportado", "input_too_large": "t/mensaje_largo"},
    "max_clarifications": 2, "on_clarify_exhausted": "escalate", "default_target_queue": "general",
}


def agent(**over: Any) -> Agent:
    return Agent.model_validate(deepcopy(AGENT) | over)
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m01/test_view.py`:

```python
import pytest

from agent_core.domain import EntityKind, RefSpec, Template, ToolDef
from agent_core.flows.refs import flow_ref_sites
from agent_core.flows.view import best_version, satisfies
from tests.m01.cases import base, flow, registry


# T-M1-42
@pytest.mark.parametrize(
    ("spec", "version", "ok"),
    [
        (None, "3.1.0", True),
        ("1.2.0", "1.2.0", True), ("1.2.0", "1.2.1", False),
        ("^1", "1.9.9", True), ("^1", "2.0.0", False), ("^1.2", "1.1.0", False), ("^1.2", "1.3.0", True),
        ("~1.2", "1.2.9", True), ("~1.2", "1.3.0", False), ("~1", "1.9.0", True),
        ("1", "1.4.0", True), ("1", "2.0.0", False),
        ("1.2", "1.2.5", True), ("1.2", "1.3.0", False),
    ],
)
def test_satisfies(spec: str | None, version: str, ok: bool) -> None:
    assert satisfies(spec, version) is ok


def test_best_version_uses_semver_precedence() -> None:
    assert best_version("^1", ["1.0.0", "1.10.0", "1.9.0", "2.0.0"]) == "1.10.0"
    assert best_version(None, ["1.0.0", "2.0.0"]) == "2.0.0"
    assert best_version("^3", ["1.0.0"]) is None


def test_registry_resolves_by_kind_and_range() -> None:
    reg = registry()
    assert isinstance(reg.resolve(EntityKind.tool, RefSpec.parse("leer@^1")), ToolDef)
    assert reg.resolve(EntityKind.tool, RefSpec.parse("leer@2")) is None
    assert reg.resolve(EntityKind.template, RefSpec.parse("leer")) is None


def test_template_reads_are_derived() -> None:
    tpl = registry().resolve(EntityKind.template, RefSpec.parse("t/hecho"))
    assert isinstance(tpl, Template)
    assert tpl.reads == frozenset({"facts.verif.value.id"})


def test_flow_ref_sites_cover_the_table() -> None:
    sites = {(s.node_id, s.kind, str(s.ref)) for s in flow_ref_sites(flow(base()))}
    assert sites == {
        ("pedir", EntityKind.template, "t/pedir"),
        ("buscar", EntityKind.tool, "leer@1"),
        ("confirmar", EntityKind.tool, "escribir@1"),
        ("confirmar", EntityKind.template, "t/resumen"),
        ("verificar", EntityKind.tool, "leer_escritura@1"),
        ("ok_msg", EntityKind.template, "t/hecho"),
    }
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_view.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.registry'`.

- [ ] **Step 4: Implementar `view.py`**

```python
"""Vista del registro para la validación (M1 §2, §3.11)."""

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Protocol, cast

from pydantic import BaseModel

from agent_core.domain import (
    Agent,
    DecisionModelDef,
    DomainError,
    EntityKind,
    EntityRef,
    Flow,
    InjectionRuleset,
    LanguageDetection,
    ModelProfile,
    Policy,
    Prompt,
    RefSpec,
    RegistryEntity,
    Release,
    Template,
    ToolDef,
)
from agent_core.ports import RegistryPort

ENTITY_TYPES: Mapping[EntityKind, type[BaseModel]] = MappingProxyType(
    {
        EntityKind.agent: Agent, EntityKind.flow: Flow, EntityKind.decision_model: DecisionModelDef,
        EntityKind.policy: Policy, EntityKind.template: Template, EntityKind.prompt: Prompt,
        EntityKind.tool: ToolDef, EntityKind.language_detection: LanguageDetection,
        EntityKind.injection_ruleset: InjectionRuleset, EntityKind.model_profile: ModelProfile,
    }
)


class RegistryView(Protocol):
    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        """La entidad a la que apunta `ref`, o None si no existe. Nunca lanza."""
        ...


def parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return (major, minor, patch)


def satisfies(spec: str | None, version: str) -> bool:
    """Semántica de M1 §3.11: sin versión, exacta, `^`/`X` (mismo mayor), `~`/`X.Y` (mismo menor)."""
    current = parse_version(version)
    if spec is None:
        return True
    if spec[0] in "^~":
        parts = [int(p) for p in spec[1:].split(".")]
        floor = (parts + [0, 0])[:3]
        base = (floor[0], floor[1], floor[2])
        if spec[0] == "^" or len(parts) == 1:
            return current[0] == base[0] and current >= base
        return current[:2] == base[:2] and current >= base
    parts = [int(p) for p in spec.split(".")]
    if len(parts) == 3:
        return current == (parts[0], parts[1], parts[2])
    if len(parts) == 1:
        return current[0] == parts[0]
    return current[:2] == (parts[0], parts[1])


def best_version(spec: str | None, versions: Iterable[str]) -> str | None:
    matching = [v for v in versions if satisfies(spec, v)]
    return max(matching, key=parse_version) if matching else None


class _ReleaseView:
    """Vista de runtime: cada id tiene la versión exacta que fija la release."""

    def __init__(self, port: RegistryPort, release: Release) -> None:
        self._port = port
        self._release = release

    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        version = self._release.entities.get(kind, {}).get(ref.id)
        if version is None or not satisfies(ref.spec, version):
            return None
        try:
            entity = self._port.get(EntityRef(id=ref.id, version=version), ENTITY_TYPES[kind])
        except (KeyError, LookupError, TypeError, DomainError):
            return None
        return cast(RegistryEntity, entity)


def release_view(port: RegistryPort, release: Release) -> RegistryView:
    return _ReleaseView(port, release)
```

- [ ] **Step 5: Implementar `refs.py`**

```python
"""Dónde hay referencias en cada entidad y a qué tipo apuntan (M1 §3.4.2)."""

from dataclasses import dataclass

from agent_core.domain import (
    Agent,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EntityKind,
    Flow,
    Node,
    Prompt,
    RefSpec,
    RegistryEntity,
    RespondNode,
    RuleNode,
    ToolNode,
    VerifyNode,
)

Pointer = tuple[str | int, ...]
TEMPLATE_KINDS = frozenset({EntityKind.template, EntityKind.prompt})


@dataclass(frozen=True)
class RefSite:
    """Una referencia, su tipo y su ubicación en `model_dump(by_alias=True)` de la entidad."""

    kind: EntityKind
    ref: RefSpec
    pointer: Pointer
    node_id: str | None = None


def pointer_str(pointer: Pointer) -> str:
    return "/" + "/".join(str(part) for part in pointer)


def _ref_or_none(text: object) -> RefSpec | None:
    if not isinstance(text, str):
        return None
    try:
        return RefSpec.parse(text)
    except ValueError:
        return None


def node_ref_sites(node: Node, index: int) -> list[RefSite]:
    base: Pointer = ("nodes", index, "config")
    sites: list[RefSite] = []

    def add(kind: EntityKind, ref: RefSpec | None, *tail: str) -> None:
        if ref is not None:
            sites.append(RefSite(kind, ref, base + tail, node.id))

    match node:
        case DecideNode():
            add(EntityKind.decision_model, node.config.model, "model")
        case RuleNode():
            add(EntityKind.policy, node.config.policy, "policy")
        case CollectNode():
            add(EntityKind.template, node.config.prompt_ref, "prompt_ref")
            validator = node.config.validator
            if validator is not None and validator.kind == "decide":
                add(EntityKind.decision_model, _ref_or_none(validator.value), "validator", "value")
        case ToolNode():
            add(EntityKind.tool, node.config.tool, "tool")
        case ConfirmNode():
            add(EntityKind.tool, node.config.action.tool, "action", "tool")
            add(EntityKind.template, node.config.summary_template, "summary_template")
            add(EntityKind.template, node.config.reprompt_template, "reprompt_template")
        case VerifyNode():
            add(EntityKind.tool, node.config.readback, "readback")
        case RespondNode():
            add(EntityKind.template, node.config.template_ref, "template_ref")
            if node.config.generate is not None:
                add(EntityKind.prompt, node.config.generate.prompt_ref, "generate", "prompt_ref")
                add(EntityKind.template, node.config.generate.fallback_template_ref, "generate",
                    "fallback_template_ref")
        case _:
            pass
    return sites


def flow_ref_sites(flow: Flow) -> list[RefSite]:
    return [site for index, node in enumerate(flow.nodes) for site in node_ref_sites(node, index)]


def agent_ref_sites(agent: Agent) -> list[RefSite]:
    sites = [RefSite(EntityKind.flow, agent.entry_flow, ("entry_flow",))]
    if agent.understand is not None:
        sites.append(RefSite(EntityKind.decision_model, agent.understand, ("understand",)))
    sites += [RefSite(EntityKind.tool, ref, ("tools_allowed", i)) for i, ref in enumerate(agent.tools_allowed)]
    for name in type(agent.templates).model_fields:
        sites.append(RefSite(EntityKind.template, getattr(agent.templates, name), ("templates", name)))
    return sites


def entity_ref_sites(entity: RegistryEntity) -> list[RefSite]:
    if isinstance(entity, Flow):
        return flow_ref_sites(entity)
    if isinstance(entity, Agent):
        return agent_ref_sites(entity)
    if isinstance(entity, Prompt):
        return [RefSite(EntityKind.model_profile, entity.model_profile, ("model_profile",))]
    return []
```

- [ ] **Step 6: Implementar `registry.py` (primera parte)**

```python
"""Registro de autoría: entidades con versiones, resolución de rangos y releases declaradas (M1 §3.11)."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_core.domain import EntityKind, Interrupt, RefSpec, RegistryEntity, Template
from agent_core.flows.paths import template_vars
from agent_core.flows.view import ENTITY_TYPES, best_version, parse_version


class ReleaseAgent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: RefSpec
    aliases: list[str] = Field(default_factory=lambda: ["prod"], min_length=1)


class ReleaseDecl(BaseModel):
    """Release de autoría (`releases/<id>.yaml`). `pin_release` la convierte en una `Release` exacta."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    agents: list[ReleaseAgent] = Field(min_length=1)
    flows: list[RefSpec] = Field(default_factory=list)
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: RefSpec
    injection_ruleset: RefSpec | None = None
    max_input_chars: PositiveInt = 4000


def kind_of(entity: RegistryEntity) -> EntityKind:
    for kind, model in ENTITY_TYPES.items():
        if isinstance(entity, model):
            return kind
    raise TypeError(f"{type(entity).__name__} no es una entidad del registro")


def derive_template_reads(template: Template) -> Template:
    """`reads` = rutas de `{{ }}` de todos los locales (M1 §3.3). Lanza ValueError si una plantilla está mal formada."""
    reads: set[str] = set()
    for text in template.locales.values():
        reads |= template_vars(text)
    return template.model_copy(update={"reads": frozenset(reads)})


class AuthoringRegistry:
    """Implementa `RegistryView` sobre entidades de autoría (referencias con rangos)."""

    def __init__(self) -> None:
        self._entities: dict[EntityKind, dict[str, dict[str, RegistryEntity]]] = {}
        self._sources: dict[tuple[EntityKind, str, str], str] = {}
        self._releases: dict[str, ReleaseDecl] = {}

    @classmethod
    def from_entities(
        cls, entities: Iterable[RegistryEntity], releases: Iterable[ReleaseDecl] = ()
    ) -> "AuthoringRegistry":
        reg = cls()
        for entity in entities:
            reg.add(entity)
        for decl in releases:
            reg.add_release(decl)
        return reg

    def add(self, entity: RegistryEntity, source: str | None = None) -> None:
        if isinstance(entity, Template):
            entity = derive_template_reads(entity)
        kind = kind_of(entity)
        ident, version = str(getattr(entity, "id")), str(getattr(entity, "version"))  # noqa: B009
        self._entities.setdefault(kind, {}).setdefault(ident, {})[version] = entity
        if source is not None:
            self._sources[(kind, ident, version)] = source

    def add_release(self, decl: ReleaseDecl, source: str | None = None) -> None:
        self._releases[decl.id] = decl

    def versions(self, kind: EntityKind, ident: str) -> list[str]:
        return sorted(self._entities.get(kind, {}).get(ident, {}), key=parse_version)

    def get_exact(self, kind: EntityKind, ident: str, version: str) -> RegistryEntity:
        return self._entities[kind][ident][version]

    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None:
        best = best_version(ref.spec, self.versions(kind, ref.id))
        return None if best is None else self._entities[kind][ref.id][best]

    def all(self, kind: EntityKind) -> list[RegistryEntity]:
        by_id = self._entities.get(kind, {})
        return [by_id[ident][v] for ident in sorted(by_id) for v in self.versions(kind, ident)]

    def releases(self) -> list[ReleaseDecl]:
        return [self._releases[rid] for rid in sorted(self._releases)]

    def release(self, release_id: str) -> ReleaseDecl | None:
        return self._releases.get(release_id)

    def source(self, kind: EntityKind, ident: str, version: str) -> str | None:
        return self._sources.get((kind, ident, version))
```

- [ ] **Step 7: Correr las pruebas**

Run: `uv run pytest tests/m01/test_view.py -v`
Esperado: todas PASS.

- [ ] **Step 8: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01
git commit -m "feat(m1): vista del registro con rangos semver, sitios de referencia y registro de autoría

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `parse_flow` y G0-01 (T-M1-01, T-M1-07, T-M1-09, T-M1-38)

**Files:**
- Create: `agent_core/flows/schema.py`
- Test: `tests/m01/test_schema.py`

**Interfaces:**
- Consumes: `Violation`, `FlowSchemaError` (Task 1); `parse_path`, `bad_paths` (Task 1); `jsonlogic_problems` (Task 1); `Flow`, nodos, `node_kind`, `PRODUCTION_NODE_KINDS` y `RefSpec` (M0).
- Produces:
  - `parse_flow(raw: JsonValue, *, source: str | None = None) -> Flow` (lanza `FlowSchemaError`);
  - `schema_violations(flow: Flow) -> list[Violation]`: G0-01 con `node_id` y `path` = JSON Pointer `/nodes/<i>/…`, sin `flow`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_schema.py`:

```python
from collections.abc import Callable
from typing import Any

import pytest

from agent_core.flows.schema import parse_flow, schema_violations
from agent_core.flows.violations import FlowSchemaError
from tests.m01.cases import base, flow, node


def _rules(d: dict[str, Any], source: str | None = None) -> set[str]:
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d, source=source)
    return {v.rule for v in info.value.violations}


def test_base_parses() -> None:
    assert parse_flow(base()).id == "base"


# T-M1-01
def test_unknown_type_is_g0_01_with_location() -> None:
    d = base()
    d["nodes"].append({"id": "k", "type": "knowledge", "config": {}})
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d, source="flows/base@1.0.0.yaml")
    (violation,) = info.value.violations
    assert violation.rule == "G0-01"
    assert violation.flow == "base@1.0.0"
    assert violation.node_id == "k"
    assert violation.path is not None and violation.path.startswith("flows/base@1.0.0.yaml#/nodes/9")


# T-M1-07 (en el MVP un nodo agent es G0-01)
def test_production_type_rejected() -> None:
    d = base()
    d["nodes"].append({"id": "ag", "type": "agent",
                       "config": {"tools_allowed": ["leer@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"}})
    with pytest.raises(FlowSchemaError) as info:
        parse_flow(d)
    assert [(v.rule, v.message) for v in info.value.violations] == [("G0-01", "tipo de producción no habilitado")]


# T-M1-09
def test_generate_without_fallback_is_g0_09() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.verif"]}}
    assert _rules(d) == {"G0-09"}


def _dup(d: dict[str, Any]) -> None:
    d["nodes"].append({"id": "fin", "type": "end", "config": {"outcome": "resolved"}})


def _knowledge(d: dict[str, Any]) -> None:
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/gen", "fallback_template_ref": "t/hecho",
                                                "knowledge_refs": ["faq#x"]}}


def _rule_op(d: dict[str, Any]) -> None:
    d["nodes"].append({"id": "r", "type": "rule", "config": {"expr": {"+": [1, 2]}}, "next": {}})


def _predicate_arity(d: dict[str, Any]) -> None:
    node(d, "verificar")["config"]["predicate"] = {"==": [{"var": "readback.status"}]}


def _priority_expr(d: dict[str, Any]) -> None:
    node(d, "esc")["config"]["priority_expr"] = {"merge": [1]}


def _bad_arg(d: dict[str, Any]) -> None:
    node(d, "buscar")["config"]["args"] = {"q": "slots.a.b"}


def _bad_confirm_arg(d: dict[str, Any]) -> None:
    node(d, "confirmar")["config"]["action"]["args"] = {"q": "facts.X.value"}


def _allowed_not_path(d: dict[str, Any]) -> None:
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["datos"],
                                                "fallback_template_ref": "t/hecho"}}


def _output_map_literal(d: dict[str, Any]) -> None:
    node(d, "fin")["config"]["output_map"] = {"x": "USD"}


def _verify_by_fact(d: dict[str, Any]) -> None:
    node(d, "verificar")["config"]["by"] = "fact:slots.a.b"


def _regex(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "regex", "value": "("}


def _enum(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "enum", "value": []}


def _type(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "type", "value": "fecha_rara"}


def _decide_validator(d: dict[str, Any]) -> None:
    node(d, "pedir")["config"]["validator"] = {"kind": "decide", "value": "no valido@@"}


# T-M1-38, T-M1-37 (id duplicado)
@pytest.mark.parametrize(
    "mutate",
    [_dup, _knowledge, _rule_op, _predicate_arity, _priority_expr, _bad_arg, _bad_confirm_arg, _allowed_not_path,
     _output_map_literal, _verify_by_fact, _regex, _enum, _type, _decide_validator],
)
def test_schema_checks_are_g0_01(mutate: Callable[[dict[str, Any]], None]) -> None:
    d = base()
    mutate(d)
    assert _rules(d) == {"G0-01"}
    assert {v.rule for v in schema_violations(flow(d))} == {"G0-01"}


def test_schema_violations_empty_for_base() -> None:
    assert schema_violations(flow(base())) == []
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_schema.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.schema'`.

- [ ] **Step 3: Implementar `schema.py`**

```python
"""`parse_flow` y las comprobaciones de G0-01 que Pydantic no hace (M1 §3.4, §3.4.1)."""

import re
from collections.abc import Iterator

from pydantic import ValidationError

from agent_core.domain import (
    PRODUCTION_NODE_KINDS,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EndNode,
    EscalateNode,
    Flow,
    JsonValue,
    Node,
    RefSpec,
    RespondNode,
    RuleNode,
    SlotValidator,
    ToolNode,
    VerifyNode,
    node_kind,
)
from agent_core.flows.jsonlogic import jsonlogic_problems
from agent_core.flows.paths import bad_paths, parse_path
from agent_core.flows.violations import FlowSchemaError, Violation

VALIDATOR_TYPES = frozenset({"string", "integer", "decimal", "date", "boolean"})
MAX_REGEX = 200
_MESSAGES = {"missing": "campo obligatorio", "extra_forbidden": "campo no permitido"}


def _label(raw: JsonValue) -> str | None:
    if isinstance(raw, dict) and isinstance(raw.get("id"), str) and isinstance(raw.get("version"), str):
        return f"{raw['id']}@{raw['version']}"
    return None


def _raw_node_id(raw: JsonValue, loc: tuple[int | str, ...]) -> str | None:
    if len(loc) < 2 or loc[0] != "nodes" or not isinstance(loc[1], int) or not isinstance(raw, dict):
        return None
    nodes = raw.get("nodes")
    if isinstance(nodes, list) and 0 <= loc[1] < len(nodes) and isinstance(nodes[loc[1]], dict):
        ident = nodes[loc[1]].get("id")
        return ident if isinstance(ident, str) else None
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
        for err in exc.errors():
            loc = tuple(err["loc"])
            rule = "G0-09" if err["type"] == "missing" and loc[-2:] == ("generate", "fallback_template_ref") else "G0-01"
            if err["type"] in ("union_tag_invalid", "union_tag_not_found"):
                message = "nodo fuera del catálogo"
            else:
                message = _MESSAGES.get(err["type"], f"valor inválido: {err['msg']}")
            pointer = "/" + "/".join(str(part) for part in loc)
            violations.append(Violation(rule=rule, flow=label, node_id=_raw_node_id(raw, loc),
                                        path=_located(source, pointer), message=message))
        raise FlowSchemaError(violations) from exc
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
            yield (f"/config/output_map/{key}", text)
    if isinstance(node, DecideNode) and node.config.input_view:
        for i, text in enumerate(node.config.input_view):
            yield (f"/config/input_view/{i}", text)
    if isinstance(node, VerifyNode) and node.config.by.startswith("fact:"):
        yield ("/config/by", node.config.by.removeprefix("fact:"))


def _args_fields(node: Node) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(node, ToolNode):
        yield ("/config/args", dict(node.config.args))
    if isinstance(node, ConfirmNode):
        yield ("/config/action/args", dict(node.config.action.args))


def validator_problems(validator: SlotValidator) -> list[str]:
    value = validator.value
    if validator.kind == "type":
        return [] if isinstance(value, str) and value in VALIDATOR_TYPES else [f"tipo de validador desconocido: {value!r}"]
    if validator.kind == "regex":
        if not isinstance(value, str) or len(value) > MAX_REGEX:
            return [f"la regex debe ser un string de hasta {MAX_REGEX} caracteres"]
        try:
            re.compile(value)
        except re.error as exc:
            return [f"regex inválida: {exc}"]
        return []
    if validator.kind == "enum":
        items = value if isinstance(value, list) else []
        strings = [v for v in items if isinstance(v, str)]
        ok = bool(strings) and len(strings) == len(items) and len(set(strings)) == len(strings)
        return [] if ok else ["el enum debe ser una lista no vacía de strings sin repetidos"]
    if isinstance(value, str):
        try:
            RefSpec.parse(value)
            return []
        except ValueError:
            pass
    return [f"el validador decide necesita una referencia a un decision_model: {value!r}"]


def schema_violations(flow: Flow) -> list[Violation]:
    """G0-01 más allá del esquema Pydantic. `path` es un JSON Pointer dentro del flow."""
    found: list[Violation] = []
    seen: set[str] = set()
    for index, node in enumerate(flow.nodes):
        where = f"/nodes/{index}"

        def add(message: str, sub: str = "", _node: Node = node, _where: str = where) -> None:
            found.append(Violation(rule="G0-01", node_id=_node.id, path=_where + sub, message=message))

        if node.id in seen:
            add(f"id de nodo duplicado: {node.id}")
        seen.add(node.id)
        if node_kind(node) in PRODUCTION_NODE_KINDS:
            add("tipo de producción no habilitado")
            continue
        if isinstance(node, RespondNode) and node.config.generate is not None and node.config.generate.knowledge_refs:
            add("conocimiento no habilitado (tema #10)", "/config/generate/knowledge_refs")
        for sub, expr in _jsonlogic_fields(node):
            for problem in jsonlogic_problems(expr):
                add(f"JSON Logic {problem}", sub)
        for sub, text in _required_paths(node):
            try:
                path = parse_path(text)
            except ValueError:
                add(f"ruta mal formada: {text!r}", sub)
                continue
            if path is None:
                add(f"se esperaba una ruta y llegó un literal: {text!r}", sub)
        for sub, value in _args_fields(node):
            for text in bad_paths(value):
                add(f"ruta mal formada: {text!r}", sub)
        if isinstance(node, CollectNode) and node.config.validator is not None:
            for problem in validator_problems(node.config.validator):
                add(problem, "/config/validator")
    return found
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/m01/test_schema.py -v`
Esperado: todas PASS. Si `test_unknown_type_is_g0_01_with_location` falla porque Pydantic reporta otro `err["type"]` para un tag de discriminador desconocido, imprime `exc.errors()`, agrega ese tipo a la tupla `("union_tag_invalid", "union_tag_not_found")` y vuelve a correr.

- [ ] **Step 5: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows/schema.py tests/m01/test_schema.py
git commit -m "feat(m1): parse_flow con G0-01 (esquema, JSON Logic, rutas, validadores) y G0-09

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Grafo, orquestador y reglas G0-02 a G0-04 (T-M1-02…04, 30, 37, 40)

**Files:**
- Create: `agent_core/flows/graph.py`, `agent_core/flows/context.py`, `agent_core/flows/rules/structure.py`, `agent_core/flows/validate.py`
- Modify: `tests/m01/cases.py` (agregar `check` y `rules`)
- Test: `tests/m01/test_structure.py`

**Interfaces:**
- Consumes: `schema_violations` (Task 4); `flow_ref_sites`, `pointer_str` (Task 3); `RegistryView` (Task 3); `RESULTS` y nodos (M0).
- Produces:
  - `FlowGraph` con `.nodes`, `.order`, `.entry` y `.succ`; `.build(flow)`, `.reachable(sources, without=frozenset())`, `.from_entry(without=frozenset())`. `Edge = tuple[str, str]` es (origen, resultado);
  - `is_waiting(node) -> bool`;
  - `writes_by_confirm(flow) -> dict[str, list[WriteToolNode]]`;
  - `verify_of(graph, write) -> VerifyNode | None`;
  - `flow_mode(flow) -> str | None`;
  - `Ctx(flow, reg, graph, index)` con `.build(flow, reg)`, `.v(rule, node_id, message, sub="") -> Violation`, `.tool(ref)`, `.model(ref)`, `.template(ref)` y `.prompt(ref)`; `Rule = Callable[[Ctx], Iterable[Violation]]`;
  - `g0_02`, `g0_03`, `g0_04`, `decide_enum(model, field) -> list[str] | None`;
  - `FLOW_RULES: tuple[Rule, ...]`; `validate_flow(flow, reg) -> list[Violation]`.

- [ ] **Step 1: Agregar helpers a `tests/m01/cases.py`**

Al final del archivo:

```python
from agent_core.flows.validate import validate_flow  # noqa: E402
from agent_core.flows.violations import Violation  # noqa: E402


def check(d: dict[str, Any], reg: AuthoringRegistry | None = None) -> list[Violation]:
    return validate_flow(flow(d), reg or registry())


def rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}
```

(Si `ruff` reordena los imports, muévelos arriba con los demás y quita los `noqa`.)

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m01/test_structure.py`:

```python
from typing import Any

from agent_core.flows.graph import FlowGraph
from tests.m01.cases import base, check, flow, node, rules


def _rule(nid: str, true: str, false: str, expr: Any = None) -> dict[str, Any]:
    return {"id": nid, "type": "rule", "config": {"expr": expr or {"!": [{"var": "slots.desc"}]}},
            "next": {"true": true, "false": false}}


def test_base_is_valid() -> None:
    assert check(base()) == []


def test_violations_carry_flow_label() -> None:
    d = base()
    del node(d, "buscar")["next"]["timeout"]
    (violation,) = check(d)
    assert (violation.rule, violation.flow, violation.node_id, violation.path) == (
        "G0-03", "base@1.0.0", "buscar", "/nodes/1/next")


# T-M1-02, T-M1-40 (sin cascada)
def test_missing_reference_only_g0_02() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "noexiste@1"
    violations = check(d)
    assert rules(violations) == {"G0-02"}
    assert [v.node_id for v in violations] == ["buscar"]


# T-M1-03
def test_result_without_next() -> None:
    d = base()
    del node(d, "buscar")["next"]["timeout"]
    assert rules(check(d)) == {"G0-03"}


# T-M1-37
def test_next_to_missing_node() -> None:
    d = base()
    node(d, "buscar")["next"]["timeout"] = "fantasma"
    assert rules(check(d)) == {"G0-03"}


def test_unknown_result_key() -> None:
    d = base()
    node(d, "buscar")["next"]["reintento"] = "esc"
    assert rules(check(d)) == {"G0-03"}


def test_terminal_with_next() -> None:
    d = base()
    node(d, "fin")["next"] = {"next": "pedir"}
    assert rules(check(d)) == {"G0-03"}


def test_unreachable_node() -> None:
    d = base()
    d["nodes"].append({"id": "suelto", "type": "end", "config": {"outcome": "resolved"}})
    violations = check(d)
    assert rules(violations) == {"G0-03"} and violations[0].node_id == "suelto"


def _with_decide(d: dict[str, Any], model: str, results: dict[str, str]) -> dict[str, Any]:
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append({"id": "elige", "type": "decide",
                       "config": {"model": model, "branch_on": "campo", "save_as": "d"}, "next": results})
    return d


def test_decide_results_are_enum_plus_low_confidence() -> None:
    ok = _with_decide(base(), "modelo@1", {"a": "confirmar", "b": "confirmar", "low_confidence": "esc"})
    assert check(ok) == []
    missing = _with_decide(base(), "modelo@1", {"a": "confirmar", "low_confidence": "esc"})
    assert rules(check(missing)) == {"G0-03"}


def test_decide_enum_with_low_confidence_is_invalid() -> None:
    d = _with_decide(base(), "modelo_lc@1", {"a": "confirmar", "low_confidence": "esc"})
    assert rules(check(d)) == {"G0-03"}


# T-M1-04
def test_cycle_without_waiting_node() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "chk"
    d["nodes"] += [_rule("chk", "chk2", "confirmar"), _rule("chk2", "chk", "confirmar")]
    violations = check(d)
    assert rules(violations) == {"G0-04"}
    assert violations[0].node_id == "chk"


# T-M1-30
def test_scc_with_collect_but_bypassing_loop() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "a"
    d["nodes"] += [
        _rule("a", "b", "confirmar"),
        _rule("b", "a", "c"),
        {"id": "c", "type": "collect", "config": {"slot": "extra", "prompt_ref": "t/pedir"},
         "next": {"ok": "a", "max_attempts": "esc"}},
    ]
    assert rules(check(d)) == {"G0-04"}


def test_self_loop_without_waiting() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "a"
    d["nodes"].append(_rule("a", "a", "confirmar"))
    assert rules(check(d)) == {"G0-04"}


def test_confirm_self_loop_is_fine() -> None:
    assert node(base(), "confirmar")["next"]["unclear"] == "confirmar"
    assert check(base()) == []


# T-M1-07 vía validate_flow: un Flow con nodo de producción construido a mano da solo G0-01
def test_validate_flow_repeats_g0_01() -> None:
    d = base()
    d["nodes"].append({"id": "ag", "type": "agent",
                       "config": {"tools_allowed": ["leer@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"}})
    assert rules(check(d)) == {"G0-01"}


def test_reachable_excludes_edges() -> None:
    graph = FlowGraph.build(flow(base()))
    assert "escribir" in graph.from_entry()
    assert "escribir" not in graph.from_entry(frozenset({("confirmar", "yes")}))
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_structure.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.validate'`.

- [ ] **Step 4: Implementar `graph.py`**

```python
"""Grafo de un flow y consultas de alcanzabilidad (M1 §3.4, §3.5)."""

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from agent_core.domain import (
    DECLARABLE,
    CollectNode,
    ConfirmNode,
    EndNode,
    Flow,
    Node,
    RespondNode,
    VerifyNode,
    WriteToolNode,
    is_declarable,
)

Edge = tuple[str, str]  # (nodo origen, resultado)


@dataclass(frozen=True)
class FlowGraph:
    nodes: Mapping[str, Node]  # primera aparición de cada id
    order: tuple[str, ...]
    entry: str
    succ: Mapping[str, tuple[tuple[str, str], ...]]  # origen → ((resultado, destino), …); solo destinos existentes

    @classmethod
    def build(cls, flow: Flow) -> "FlowGraph":
        nodes: dict[str, Node] = {}
        for node in flow.nodes:
            nodes.setdefault(node.id, node)
        succ = {
            ident: tuple(sorted((result, dst) for result, dst in node.next.items() if dst in nodes))
            for ident, node in nodes.items()
        }
        return cls(nodes=nodes, order=tuple(nodes), entry=flow.nodes[0].id, succ=succ)

    def _next(self, ident: str, without: frozenset[Edge]) -> list[str]:
        return [dst for result, dst in self.succ.get(ident, ()) if (ident, result) not in without]

    def reachable(self, sources: Iterable[str], without: frozenset[Edge] = frozenset()) -> set[str]:
        """Nodos alcanzables por caminos de longitud ≥ 1 desde `sources`, sin usar las aristas de `without`."""
        seen: set[str] = set()
        queue: deque[str] = deque()
        for source in sources:
            queue.extend(self._next(source, without))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self._next(current, without))
        return seen

    def from_entry(self, without: frozenset[Edge] = frozenset()) -> set[str]:
        return {self.entry} | self.reachable([self.entry], without)


def is_waiting(node: Node) -> bool:
    """Nodos que esperan al principal: collect, confirm y respond con await (M1 §3.4, G0-04)."""
    return isinstance(node, CollectNode | ConfirmNode) or (isinstance(node, RespondNode) and node.config.await_)


def writes_by_confirm(flow: Flow) -> dict[str, list[WriteToolNode]]:
    grouped: dict[str, list[WriteToolNode]] = {}
    for node in flow.nodes:
        if isinstance(node, WriteToolNode):
            grouped.setdefault(node.config.action_from, []).append(node)
    return grouped


def verify_of(graph: FlowGraph, write: WriteToolNode) -> VerifyNode | None:
    """El verify enlazado por estructura: `ok` y `uncertain` van directo al mismo verify (G0-05.7)."""
    target_id = write.next.get("ok")
    target = graph.nodes.get(target_id) if target_id is not None else None
    if isinstance(target, VerifyNode) and write.next.get("uncertain") == target_id:
        return target
    return None


def flow_mode(flow: Flow) -> str | None:
    """`task` o `conversational` si todos los `end` son declarables en ese modo; None si no hay `end` o se mezclan."""
    ends = [node for node in flow.nodes if isinstance(node, EndNode)]
    if not ends:
        return None
    modes = sorted(m for m in DECLARABLE if all(is_declarable(e.config.outcome, m) for e in ends))
    return modes[0] if len(modes) == 1 else None
```

- [ ] **Step 5: Implementar `context.py`**

```python
"""Contexto que recibe cada regla (M1 §3.4)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from agent_core.domain import DecisionModelDef, EntityKind, Flow, Prompt, RefSpec, Template, ToolDef
from agent_core.flows.graph import FlowGraph
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation


@dataclass(frozen=True)
class Ctx:
    flow: Flow
    reg: RegistryView
    graph: FlowGraph
    index: Mapping[str, int]  # id → posición de su primera aparición

    @classmethod
    def build(cls, flow: Flow, reg: RegistryView) -> "Ctx":
        index: dict[str, int] = {}
        for i, node in enumerate(flow.nodes):
            index.setdefault(node.id, i)
        return cls(flow=flow, reg=reg, graph=FlowGraph.build(flow), index=index)

    def v(self, rule: str, node_id: str | None, message: str, sub: str = "") -> Violation:
        if node_id is not None and node_id in self.index:
            path: str | None = f"/nodes/{self.index[node_id]}{sub}"
        else:
            path = sub or None
        return Violation(rule=rule, node_id=node_id, path=path, message=message)

    def tool(self, ref: RefSpec) -> ToolDef | None:
        entity = self.reg.resolve(EntityKind.tool, ref)
        return entity if isinstance(entity, ToolDef) else None

    def model(self, ref: RefSpec) -> DecisionModelDef | None:
        entity = self.reg.resolve(EntityKind.decision_model, ref)
        return entity if isinstance(entity, DecisionModelDef) else None

    def template(self, ref: RefSpec) -> Template | None:
        entity = self.reg.resolve(EntityKind.template, ref)
        return entity if isinstance(entity, Template) else None

    def prompt(self, ref: RefSpec) -> Prompt | None:
        entity = self.reg.resolve(EntityKind.prompt, ref)
        return entity if isinstance(entity, Prompt) else None


Rule = Callable[[Ctx], Iterable[Violation]]
```

- [ ] **Step 6: Implementar `rules/structure.py`**

```python
"""G0-02 (referencias), G0-03 (estructura del grafo) y G0-04 (ciclos sin espera)."""

from collections.abc import Iterator, Mapping, Sequence

from agent_core.domain import RESULTS, DecideNode, DecisionModelDef, node_kind
from agent_core.flows.context import Ctx
from agent_core.flows.graph import is_waiting
from agent_core.flows.refs import flow_ref_sites, pointer_str
from agent_core.flows.violations import Violation


def g0_02(ctx: Ctx) -> Iterator[Violation]:
    for site in flow_ref_sites(ctx.flow):
        if ctx.reg.resolve(site.kind, site.ref) is None:
            yield Violation(rule="G0-02", node_id=site.node_id, path=pointer_str(site.pointer),
                            message=f"{site.kind.value} {site.ref} no existe en el registro")


def decide_enum(model: DecisionModelDef, field: str) -> list[str] | None:
    """Valores del enum de `branch_on` en `output_schema.properties`, o None si no es un enum válido (G0-03e)."""
    properties = model.output_schema.get("properties")
    spec = properties.get(field) if isinstance(properties, dict) else None
    enum = spec.get("enum") if isinstance(spec, dict) else None
    if not isinstance(enum, list) or not enum:
        return None
    values = [v for v in enum if isinstance(v, str)]
    if len(values) != len(enum) or len(set(values)) != len(values) or "low_confidence" in values:
        return None
    return values


def g0_03(ctx: Ctx) -> Iterator[Violation]:
    graph = ctx.graph
    for ident in graph.order:
        node = graph.nodes[ident]
        for key, dst in sorted(node.next.items()):
            if dst not in graph.nodes:
                yield ctx.v("G0-03", ident, f"next.{key} apunta a {dst!r}, que no existe", f"/next/{key}")
        expected: frozenset[str] | None = RESULTS.get(node_kind(node) or "", frozenset())
        if isinstance(node, DecideNode):
            model = ctx.model(node.config.model)
            if model is None:
                expected = None
            else:
                enum = decide_enum(model, node.config.branch_on)
                if enum is None:
                    yield ctx.v("G0-03", ident,
                                f"branch_on {node.config.branch_on!r} debe ser un enum de strings de "
                                "output_schema.properties, sin repetidos ni 'low_confidence'", "/config/branch_on")
                    expected = None
                else:
                    expected = frozenset(enum) | {"low_confidence"}
        if expected is None:
            continue
        for key in sorted(set(node.next) - expected):
            yield ctx.v("G0-03", ident, f"resultado desconocido {key!r} para un nodo {node.type}", f"/next/{key}")
        for key in sorted(expected - set(node.next)):
            yield ctx.v("G0-03", ident, f"el resultado {key!r} no tiene next", "/next")
    reachable = graph.from_entry()
    for ident in graph.order:
        if ident not in reachable:
            yield ctx.v("G0-03", ident, "nodo inalcanzable desde la entrada")


def _sccs(order: Sequence[str], adj: Mapping[str, list[str]]) -> list[list[str]]:
    """Componentes fuertemente conexas (Tarjan iterativo)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    counter = 0
    for root in order:
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            current, position = work.pop()
            if position == 0:
                index[current] = low[current] = counter
                counter += 1
                stack.append(current)
                on_stack.add(current)
            descended = False
            successors = adj[current]
            for j in range(position, len(successors)):
                nxt = successors[j]
                if nxt not in index:
                    work.append((current, j + 1))
                    work.append((nxt, 0))
                    descended = True
                    break
                if nxt in on_stack:
                    low[current] = min(low[current], index[nxt])
            if descended:
                continue
            if low[current] == index[current]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == current:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[current])
    return components


def g0_04(ctx: Ctx) -> Iterator[Violation]:
    """Sin los nodos que esperan, el grafo debe ser acíclico (auto-bucles incluidos)."""
    graph = ctx.graph
    keep = [ident for ident in graph.order if not is_waiting(graph.nodes[ident])]
    kept = set(keep)
    adj = {ident: [dst for _, dst in graph.succ[ident] if dst in kept] for ident in keep}
    for component in _sccs(keep, adj):
        if len(component) > 1 or component[0] in adj[component[0]]:
            members = sorted(component)
            yield ctx.v("G0-04", members[0], "ciclo sin nodo que espere al principal: " + ", ".join(members))
```

- [ ] **Step 7: Implementar `validate.py`**

```python
"""Orquestador de las reglas G0 (M1 §3.4, §4)."""

from agent_core.domain import Flow
from agent_core.flows.context import Ctx, Rule
from agent_core.flows.rules.structure import g0_02, g0_03, g0_04
from agent_core.flows.schema import schema_violations
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, sort_violations

# Reglas que necesitan un esquema sin G0-01. Agregar una regla = agregarla aquí (M1 §9).
FLOW_RULES: tuple[Rule, ...] = (g0_03, g0_04)


def validate_flow(flow: Flow, reg: RegistryView) -> list[Violation]:
    """Gate G0 de un flow aislado. Determinista y total: nunca lanza por un flow mal formado."""
    label = f"{flow.id}@{flow.version}"
    ctx = Ctx.build(flow, reg)
    found: list[Violation] = [*schema_violations(flow), *g0_02(ctx)]
    if not any(v.rule == "G0-01" for v in found):
        for rule in FLOW_RULES:
            found.extend(rule(ctx))
    return sort_violations(v if v.flow is not None else v.model_copy(update={"flow": label}) for v in found)
```

- [ ] **Step 8: Correr las pruebas**

Run: `uv run pytest tests/m01 -v`
Esperado: todas PASS.

- [ ] **Step 9: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01
git commit -m "feat(m1): grafo del flow, orquestador y reglas G0-02 a G0-04

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `derive_claims` conservador (T-M1-35)

**Files:**
- Create: `agent_core/flows/claims.py`
- Test: `tests/m01/test_claims.py`

**Interfaces:**
- Consumes: `FlowGraph`, `writes_by_confirm` y `verify_of` (Task 5); `parse_path` y `value_paths` (Task 1); `RegistryView` (Task 3).
- Produces: `derive_claims(flow: Flow, reg: RegistryView) -> Mapping[str, frozenset[str]]`, con una entrada por cada `respond` y cada `end` con `output_map`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_claims.py`:

```python
from typing import Any

from agent_core.flows.claims import derive_claims
from tests.m01.cases import base, flow, node, registry


def _claims(d: dict[str, Any]) -> dict[str, frozenset[str]]:
    return dict(derive_claims(flow(d), registry()))


def _respond(nid: str, template: str) -> dict[str, Any]:
    return {"id": nid, "type": "respond", "config": {"template_ref": template}, "next": {"next": "fin"}}


def test_base_claims() -> None:
    assert _claims(base()) == {"ok_msg": frozenset({"confirmar"})}


def test_derived_without_declaring() -> None:
    d = base()
    node(d, "ok_msg")["config"]["claims"] = []
    assert _claims(d)["ok_msg"] == frozenset({"confirmar"})


def test_safe_respond_has_empty_claims() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/seguro"))
    assert _claims(d)["aviso"] == frozenset()


def test_unrelated_fact_does_not_claim() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/lee_datos"))
    assert _claims(d)["aviso"] == frozenset()


def test_fallback_template_counts() -> None:
    d = base()
    d["nodes"].append({"id": "gen", "type": "respond",
                       "config": {"generate": {"prompt_ref": "p/gen", "fallback_template_ref": "t/lee_res"}},
                       "next": {"next": "fin"}})
    assert _claims(d)["gen"] == frozenset({"confirmar"})


def test_allowed_facts_count() -> None:
    d = base()
    d["nodes"].append({"id": "gen", "type": "respond",
                       "config": {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.res"],
                                               "fallback_template_ref": "t/seguro"}},
                       "next": {"next": "fin"}})
    assert _claims(d)["gen"] == frozenset({"confirmar"})


def test_read_tool_propagates() -> None:
    d = base()
    d["nodes"] += [
        {"id": "estado", "type": "tool",
         "config": {"tool": "leer@1", "args": {"id": "facts.res.value.id"}, "save_as": "estado"}},
        _respond("aviso", "t/lee_estado"),
    ]
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_decide_then_compute_propagates() -> None:
    d = base()
    d["nodes"] += [
        {"id": "d", "type": "decide",
         "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d", "input_view": ["facts.res.value"]}},
        {"id": "calc_n", "type": "tool",
         "config": {"tool": "calc@1", "args": {"x": "decisions.d.campo"}, "save_as": "c"}},
        _respond("aviso", "t/lee_c"),
    ]
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_end_output_map_is_a_reader() -> None:
    d = base()
    d["nodes"].append({"id": "fin_map", "type": "end",
                       "config": {"outcome": "resolved", "output_map": {"r": "facts.verif.value.id"}}})
    assert _claims(d)["fin_map"] == frozenset({"confirmar"})


def test_unresolved_template_is_tolerated() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/noexiste"))
    assert _claims(d)["aviso"] == frozenset()
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_claims.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `claims.py`**

```python
"""Reclamos de éxito declarados y derivados (M1 §3.6). M2 usa esta misma función en runtime."""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from agent_core.domain import (
    ConfirmNode,
    DecideNode,
    EndNode,
    EntityKind,
    Flow,
    Node,
    Prompt,
    RespondNode,
    Template,
    ToolNode,
    VerifyNode,
    WriteToolNode,
)
from agent_core.flows.graph import FlowGraph, verify_of, writes_by_confirm
from agent_core.flows.paths import Path, parse_path, value_paths
from agent_core.flows.view import RegistryView

Name = tuple[str, str]  # ("facts" | "decisions", nombre)


def _names(paths: Iterable[Path]) -> set[Name]:
    return {(p.ns, p.name) for p in paths if p.ns in ("facts", "decisions") and p.name is not None}


def _parsed(texts: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for text in texts:
        try:
            path = parse_path(text)
        except ValueError:
            continue
        if path is not None:
            paths.append(path)
    return paths


def _output(node: Node) -> Name | None:
    if isinstance(node, ToolNode | WriteToolNode | VerifyNode):
        return ("facts", node.config.save_as)
    if isinstance(node, DecideNode):
        return ("decisions", node.config.save_as)
    return None


def _inputs(node: Node) -> set[Name]:
    if isinstance(node, ToolNode):
        return _names(value_paths(dict(node.config.args), strict=False))
    if isinstance(node, VerifyNode) and node.config.by.startswith("fact:"):
        return _names(_parsed([node.config.by.removeprefix("fact:")]))
    if isinstance(node, DecideNode):
        return _names(_parsed(node.config.input_view or []))
    return set()


def _origin(flow: Flow, graph: FlowGraph, writes: list[WriteToolNode]) -> set[Name]:
    names: set[Name] = set()
    for write in writes:
        names.add(("facts", write.config.save_as))
        verify = verify_of(graph, write)
        if verify is not None:
            names.add(("facts", verify.config.save_as))
    producers = [(out, _inputs(node)) for node in flow.nodes if (out := _output(node)) is not None]
    changed = True
    while changed:
        changed = False
        for out, ins in producers:
            if out not in names and ins & names:
                names.add(out)
                changed = True
    return names


def _template_reads(reg: RegistryView, ref: object) -> list[str]:
    from agent_core.domain import RefSpec

    if not isinstance(ref, RefSpec):
        return []
    entity = reg.resolve(EntityKind.template, ref)
    return sorted(entity.reads) if isinstance(entity, Template) else []


def _reads(node: Node, reg: RegistryView) -> set[Name] | None:
    """Lo que lee un lector, o None si el nodo no es lector."""
    if isinstance(node, RespondNode):
        texts = _template_reads(reg, node.config.template_ref)
        generate = node.config.generate
        if generate is not None:
            texts += generate.allowed_facts
            texts += _template_reads(reg, generate.fallback_template_ref)
            prompt = reg.resolve(EntityKind.prompt, generate.prompt_ref)
            if isinstance(prompt, Prompt):
                texts += sorted(prompt.reads)
        return _names(_parsed(texts))
    if isinstance(node, EndNode) and node.config.output_map:
        return _names(_parsed(node.config.output_map.values()))
    return None


def derive_claims(flow: Flow, reg: RegistryView) -> Mapping[str, frozenset[str]]:
    """Lector (respond, end con output_map) → ids de confirm cuyo éxito afirma. Conservador y total."""
    graph = FlowGraph.build(flow)
    writes = writes_by_confirm(flow)
    origins = {n.id: _origin(flow, graph, writes.get(n.id, [])) for n in flow.nodes if isinstance(n, ConfirmNode)}
    result: dict[str, frozenset[str]] = {}
    for node in flow.nodes:
        reads = _reads(node, reg)
        if reads is None or node.id in result:
            continue
        declared = set(node.config.claims) if isinstance(node, RespondNode) else set()
        derived = {confirm for confirm, names in origins.items() if names & reads}
        result[node.id] = frozenset(declared | derived)
    return MappingProxyType(result)
```

Mueve `from agent_core.domain import RefSpec` al bloque de imports de arriba (está dentro de la función solo para que el bloque sea legible aquí; `ruff` lo marcaría).

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/m01/test_claims.py -v`
Esperado: todas PASS.

- [ ] **Step 5: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows/claims.py tests/m01/test_claims.py
git commit -m "feat(m1): derive_claims conservador (toda tool, decide, respaldo, output_map)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: G0-05, invariante de escritura (T-M1-05, 16…20, 31…34)

**Files:**
- Create: `agent_core/flows/rules/writes.py`
- Modify: `agent_core/flows/validate.py` (agregar `g0_05` a `FLOW_RULES`)
- Test: `tests/m01/test_writes.py`

**Interfaces:**
- Consumes: `Ctx` (Task 5); `writes_by_confirm` y `verify_of` (Task 5); `derive_claims` (Task 6); `RiskClass` (M0).
- Produces: `g0_05(ctx) -> Iterator[Violation]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_writes.py`:

```python
from typing import Any

from tests.m01.cases import base, check, node, rules


def _respond(nid: str, template: str, nxt: str, claims: list[str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"template_ref": template}
    if claims is not None:
        config["claims"] = claims
    return {"id": nid, "type": "respond", "config": config, "next": {"next": nxt}}


def _rule(nid: str, true: str, false: str) -> dict[str, Any]:
    return {"id": nid, "type": "rule", "config": {"expr": {"!": [{"var": "slots.desc"}]}},
            "next": {"true": true, "false": false}}


# T-M1-05 y T-M1-16 (escritura sin confirm)
def test_action_from_not_a_confirm() -> None:
    d = base()
    node(d, "escribir")["config"]["action_from"] = "buscar"
    assert rules(check(d)) == {"G0-05"}


# T-M1-16 (escritura sin verify)
def test_write_without_verify() -> None:
    d = base()
    node(d, "escribir")["next"]["ok"] = "ok_msg"
    assert rules(check(d)) == {"G0-05"}


# T-M1-17
def test_declared_claim_before_verify() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/seguro", "confirmar", ["confirmar"]))
    assert rules(check(d)) == {"G0-05"}


# T-M1-18
def test_derived_claim_before_verify() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/lee_res", "confirmar"))
    assert rules(check(d)) == {"G0-05"}


# T-M1-19
def test_compute_inherits_claim() -> None:
    d = base()
    node(d, "confirmar")["next"]["no"] = "derivar"
    d["nodes"] += [
        {"id": "derivar", "type": "tool",
         "config": {"tool": "calc@1", "args": {"x": "facts.res.value.monto"}, "save_as": "monto_calc"},
         "next": {"ok": "aviso", "error": "esc", "timeout": "esc", "denied": "esc"}},
        _respond("aviso", "t/lee_calc", "fin_cancelado"),
    ]
    assert rules(check(d)) == {"G0-05"}


# T-M1-20
def test_two_writes_claim_between_is_fine() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    d["nodes"] += [
        {"id": "confirmar2", "type": "confirm",
         "config": {"action": {"tool": "escribir@1", "args": {"q": "slots.desc"}}, "summary_template": "t/resumen"},
         "next": {"yes": "escribir2", "no": "fin_cancelado", "unclear": "confirmar2", "max_attempts": "esc"}},
        {"id": "escribir2", "type": "tool", "config": {"action_from": "confirmar2", "save_as": "res2"},
         "next": {"ok": "verificar2", "uncertain": "verificar2", "denied": "esc"}},
        {"id": "verificar2", "type": "verify",
         "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                    "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": "verif2"},
         "next": {"verified": "ok2", "failed": "esc"}},
        _respond("ok2", "t/hecho2", "fin", ["confirmar2"]),
    ]
    assert check(d) == []


# T-M1-31
def test_path_through_confirm_no_is_violation() -> None:
    d = base()
    node(d, "confirmar")["next"]["no"] = "r"
    d["nodes"].append(_rule("r", "escribir", "fin_cancelado"))
    assert rules(check(d)) == {"G0-05"}


def test_retry_write_from_failed_is_violation() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "escribir"
    assert "G0-05" in rules(check(d))


# T-M1-32
def test_plain_tool_node_with_write_tool() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "escribir@1"
    assert rules(check(d)) == {"G0-05"}


def test_readback_must_be_read() -> None:
    d = base()
    node(d, "verificar")["config"]["readback"] = "calc@1"
    assert rules(check(d)) == {"G0-05"}


def test_two_writes_for_one_confirm() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "escribir_b"
    d["nodes"].append({"id": "escribir_b", "type": "tool", "config": {"action_from": "confirmar", "save_as": "res_b"},
                       "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}})
    assert "G0-05" in rules(check(d))


def test_confirm_with_read_tool() -> None:
    d = base()
    node(d, "confirmar")["config"]["action"]["tool"] = "leer@1"
    assert rules(check(d)) == {"G0-05"}


# T-M1-33
def test_ok_and_uncertain_to_different_nodes() -> None:
    d = base()
    node(d, "escribir")["next"]["uncertain"] = "esc"
    assert "G0-05" in rules(check(d))


def test_intermediate_node_before_verify() -> None:
    d = base()
    node(d, "escribir")["next"].update({"ok": "paso", "uncertain": "paso"})
    d["nodes"].append(_respond("paso", "t/seguro", "verificar"))
    assert "G0-05" in rules(check(d))


# T-M1-34
def test_loop_back_to_confirm_then_claim_on_no() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "otra"
    node(d, "confirmar")["next"]["no"] = "aviso"
    d["nodes"] += [
        _rule("otra", "confirmar", "fin"),
        _respond("aviso", "t/seguro", "fin_cancelado", ["confirmar"]),
    ]
    assert "G0-05" in rules(check(d))


def test_claim_on_confirm_without_write() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    node(d, "ok_msg")["config"]["claims"] = ["confirmar", "confirmar2"]
    d["nodes"].append(
        {"id": "confirmar2", "type": "confirm",
         "config": {"action": {"tool": "escribir@1", "args": {"q": "slots.desc"}}, "summary_template": "t/resumen"},
         "next": {"yes": "fin", "no": "fin_cancelado", "unclear": "confirmar2", "max_attempts": "esc"}})
    assert rules(check(d)) == {"G0-05"}
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_writes.py -v`
Esperado: varias FAIL (G0-05 todavía no existe: `rules(...) == set()`).

- [ ] **Step 3: Implementar `rules/writes.py`**

```python
"""G0-05: invariante de escritura (M1 §3.5)."""

from collections.abc import Iterator

from agent_core.domain import ConfirmNode, RiskClass, ToolNode, VerifyNode
from agent_core.flows.claims import derive_claims
from agent_core.flows.context import Ctx
from agent_core.flows.graph import verify_of, writes_by_confirm
from agent_core.flows.violations import Violation

RULE = "G0-05"


def g0_05(ctx: Ctx) -> Iterator[Violation]:
    flow, graph = ctx.flow, ctx.graph
    for node in flow.nodes:
        if isinstance(node, ToolNode):
            tool = ctx.tool(node.config.tool)
            if tool is not None and tool.is_write:
                yield ctx.v(RULE, node.id, f"la tool de escritura {node.config.tool} solo se invoca con action_from",
                            "/config/tool")
        if isinstance(node, VerifyNode):
            readback = ctx.tool(node.config.readback)
            if readback is not None and readback.risk_class != RiskClass.read:
                yield ctx.v(RULE, node.id, f"el readback {node.config.readback} debe ser de clase read",
                            "/config/readback")

    writes = writes_by_confirm(flow)
    verify_writers: dict[str, list[str]] = {}
    for confirm_id, group in sorted(writes.items()):
        confirm = graph.nodes.get(confirm_id)
        if not isinstance(confirm, ConfirmNode):
            for write in group:
                yield ctx.v(RULE, write.id, f"action_from apunta a {confirm_id!r}, que no es un confirm del flow",
                            "/config/action_from")
            continue
        tool = ctx.tool(confirm.config.action.tool)
        if tool is not None and (not tool.is_write or tool.readback_by != "idempotency_key"):
            yield ctx.v(RULE, confirm.id, "la tool del confirm debe ser de escritura con readback_by: idempotency_key",
                        "/config/action/tool")
        if len(group) > 1:
            names = ", ".join(sorted(w.id for w in group))
            yield ctx.v(RULE, confirm.id, f"el confirm tiene más de una escritura: {names}")
        without_yes = frozenset({(confirm_id, "yes")})
        for write in group:
            if write.id in graph.from_entry(without_yes) or write.id in graph.reachable([write.id], without_yes):
                yield ctx.v(RULE, write.id, f"hay caminos a {write.id} que no pasan por {confirm_id}.yes")
            verify = verify_of(graph, write)
            if verify is None or verify.config.by != "idempotency_key":
                yield ctx.v(RULE, write.id, "ok y uncertain deben ir directo al mismo verify con by: idempotency_key",
                            "/next")
            else:
                verify_writers.setdefault(verify.id, []).append(write.id)
    for verify_id, writers in sorted(verify_writers.items()):
        if len(writers) > 1:
            yield ctx.v(RULE, verify_id, f"el verify lo comparten varias escrituras: {', '.join(sorted(writers))}")

    confirm_ids = {n.id for n in flow.nodes if isinstance(n, ConfirmNode)}
    for reader_id, claimed in sorted(derive_claims(flow, ctx.reg).items()):
        for action in sorted(claimed & confirm_ids):
            verifies = [v for w in writes.get(action, []) if (v := verify_of(graph, w)) is not None]
            if not verifies:
                yield ctx.v(RULE, reader_id, f"reclama {action}, que no tiene una escritura verificable")
                continue
            cut = frozenset((v.id, "verified") for v in verifies)
            if reader_id in graph.from_entry(cut) or reader_id in graph.reachable([action], cut):
                through = ", ".join(sorted(v.id for v in verifies))
                yield ctx.v(RULE, reader_id, f"reclama {action} en un camino que no pasa por verified de {through}")
```

- [ ] **Step 4: Registrar la regla**

En `agent_core/flows/validate.py`:

```python
from agent_core.flows.rules.writes import g0_05
...
FLOW_RULES: tuple[Rule, ...] = (g0_03, g0_04, g0_05)
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m01 -v`
Esperado: todas PASS.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01/test_writes.py
git commit -m "feat(m1): G0-05 (paso por confirm.yes, verify enlazado, reclamos desde el confirm)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: G0-06, salidas seguras (T-M1-06, T-M1-21)

**Files:**
- Create: `agent_core/flows/rules/exits.py`
- Modify: `agent_core/flows/validate.py` (agregar `g0_06`)
- Test: `tests/m01/test_safe_exits.py`

**Interfaces:**
- Consumes: `Ctx`, `flow_mode` (Task 5); `derive_claims` (Task 6).
- Produces: `g0_06(ctx)`, `FAILURES: Mapping[str, frozenset[str]]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_safe_exits.py`:

```python
from typing import Any

from tests.m01.cases import base, check, node, rules, task_base


def _respond(nid: str, template: str, nxt: str, claims: list[str] | None = None, wait: bool = False) -> dict[str, Any]:
    config: dict[str, Any] = {"template_ref": template, "await": wait}
    if claims is not None:
        config["claims"] = claims
    return {"id": nid, "type": "respond", "config": config, "next": {"next": nxt}}


# T-M1-06
def test_failure_to_resolved_end() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin"
    assert rules(check(d)) == {"G0-06"}


def test_failure_to_abstained_end_is_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin_abst"
    d["nodes"].append({"id": "fin_abst", "type": "end", "config": {"outcome": "abstained"}})
    assert check(d) == []


# T-M1-21
def test_claiming_respond_on_failed_branch() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "esc", ["confirmar"]))
    assert {"G0-05", "G0-06"} <= rules(check(d))


def test_safe_respond_on_failed_branch() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "esc"))
    assert check(d) == []


def test_safe_chain_ending_in_confirm_is_not_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "confirmar"))
    assert rules(check(d)) == {"G0-06"}


def test_awaiting_safe_respond_back_to_collect() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "aclarar"
    d["nodes"].append(_respond("aclarar", "t/seguro", "pedir", wait=True))
    assert check(d) == []


# T-M1-24 (lógica de G0-06; el resto de reglas de modo llega en la Task 12)
def test_task_flow_failed_end_is_safe() -> None:
    assert check(task_base()) == []
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_safe_exits.py -v`
Esperado: FAIL en `test_failure_to_resolved_end` y en `test_safe_chain_ending_in_confirm_is_not_safe`.

- [ ] **Step 3: Implementar `rules/exits.py`**

```python
"""G0-06: toda rama de fallo termina en una salida segura (M1 §3.7)."""

from collections.abc import Iterator, Mapping
from types import MappingProxyType

from agent_core.domain import CollectNode, EndNode, EscalateNode, Outcome, RespondNode, node_kind
from agent_core.flows.claims import derive_claims
from agent_core.flows.context import Ctx
from agent_core.flows.graph import FlowGraph, flow_mode
from agent_core.flows.violations import Violation

FAILURES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decide": frozenset({"low_confidence"}),
        "collect": frozenset({"max_attempts"}),
        "tool": frozenset({"error", "timeout", "denied"}),
        "tool_write": frozenset({"denied"}),
        "confirm": frozenset({"max_attempts"}),
        "verify": frozenset({"failed"}),
    }
)
SAFE_ENDS = frozenset({Outcome.abstained, Outcome.clarify_exhausted})


def _safe(graph: FlowGraph, start: str, claims: Mapping[str, frozenset[str]], task: bool) -> bool:
    seen: set[str] = set()
    current = start
    while True:
        node = graph.nodes[current]
        if isinstance(node, CollectNode | EscalateNode):
            return True
        if isinstance(node, EndNode):
            return node.config.outcome in SAFE_ENDS or (task and node.config.outcome == Outcome.failed)
        if not isinstance(node, RespondNode) or claims.get(current):
            return False
        if current in seen:
            return True
        seen.add(current)
        nxt = node.next.get("next")
        if nxt is None or nxt not in graph.nodes:
            return True  # G0-03 ya lo reporta
        current = nxt


def g0_06(ctx: Ctx) -> Iterator[Violation]:
    task = flow_mode(ctx.flow) == "task"
    claims = derive_claims(ctx.flow, ctx.reg)
    for ident in ctx.graph.order:
        node = ctx.graph.nodes[ident]
        for result in sorted(FAILURES.get(node_kind(node) or "", frozenset())):
            dst = node.next.get(result)
            if dst is None or dst not in ctx.graph.nodes:
                continue
            if not _safe(ctx.graph, dst, claims, task):
                yield ctx.v("G0-06", ident, f"la rama {result} va a {dst!r}, que no es una salida segura",
                            f"/next/{result}")
```

- [ ] **Step 4: Registrar la regla**

En `agent_core/flows/validate.py`:

```python
from agent_core.flows.rules.exits import g0_06
...
FLOW_RULES: tuple[Rule, ...] = (g0_03, g0_04, g0_05, g0_06)
```

- [ ] **Step 5: Correr, lint y commit**

```bash
uv run pytest tests/m01 -v && uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01/test_safe_exits.py
git commit -m "feat(m1): G0-06 (salidas seguras con cadena de respond seguros)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: todas PASS.

---

### Task 9: Carga del registro desde YAML y fixture `disputa-cargo` (T-M1-15, T-M1-36)

**Files:**
- Modify: `agent_core/flows/registry.py` (agregar `DIRS` y `load_registry`)
- Modify: `agent_core/flows/validate.py` (agregar `validate_registry`)
- Create: `tests/m01/fixtures/registry/**` (archivos listados abajo)
- Test: `tests/m01/test_registry.py`

**Interfaces:**
- Consumes: `load_yaml` (Task 2); `parse_flow` (Task 4); `template_vars` (Task 1); `AuthoringRegistry` (Task 3); `validate_flow` (Task 5).
- Produces:
  - `DIRS: Mapping[EntityKind, str]`;
  - `load_registry(root: Path) -> tuple[AuthoringRegistry, list[Violation]]`;
  - `validate_registry(reg: AuthoringRegistry) -> list[Violation]` (en esta tarea solo flows; la Task 13 agrega agentes y releases).

- [ ] **Step 1: Crear el registro de ejemplo**

Todos los archivos van bajo `tests/m01/fixtures/registry/`. Todos los datos son sintéticos.

`flows/disputa-cargo@1.0.0.yaml`:

```yaml
id: disputa-cargo
version: 1.0.0
priority: 50
nodes:
  - {id: pedir_cargo, type: collect, config: {slot: descripcion_cargo, prompt_ref: t/pedir_cargo, max_attempts: 2},
     next: {ok: buscar_tx, max_attempts: esc_sin_datos}}
  - {id: buscar_tx, type: tool, config: {tool: buscar_transacciones@1, args: {texto: slots.descripcion_cargo}, save_as: candidatas},
     next: {ok: coincide, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: coincide, type: decide, config: {model: match-cargo@2, branch_on: match, save_as: coincide},
     next: {unica: elegir, ninguna: aclarar, varias: aclarar, low_confidence: aclarar}}
  - {id: elegir, type: tool, config: {tool: seleccionar@1, args: {lista: facts.candidatas.value, id: decisions.coincide.transaction}, save_as: transaccion_elegida},
     next: {ok: a_usd, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: a_usd, type: tool, config: {tool: convertir_moneda@1, args: {monto: facts.transaccion_elegida.value.amount, moneda: facts.transaccion_elegida.value.currency, destino: USD}, save_as: monto_usd},
     next: {ok: umbral, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: umbral, type: rule, config: {policy: escalamiento-disputa-monto@1}, next: {true: esc_monto, false: confirmar}}
  - {id: confirmar, type: confirm, config: {action: {tool: radicar_pqr@1, args: {transaction_id: facts.transaccion_elegida.value.transaction_id, descripcion: slots.descripcion_cargo}}, summary_template: t/resumen_pqr},
     next: {yes: radicar, no: fin_cancelado, unclear: confirmar, max_attempts: no_confirmado}}
  - {id: radicar, type: tool, config: {action_from: confirmar, save_as: pqr}, next: {ok: verificar, uncertain: verificar, denied: esc_tool}}
  - {id: verificar, type: verify, config: {readback: obtener_pqr@1, by: idempotency_key, predicate: {"==": [{var: readback.status}, "Open"]}, save_as: pqr_verificada},
     next: {verified: responder_ok, failed: esc_verif}}
  - {id: responder_ok, type: respond, config: {template_ref: t/pqr_radicado, claims: [confirmar]}, next: {next: fin}}
  - {id: fin, type: end, config: {outcome: resolved}}
  - {id: aclarar, type: respond, config: {template_ref: t/aclarar_cargo, await: true}, next: {next: pedir_cargo}}
  - {id: no_confirmado, type: respond, config: {template_ref: t/no_confirmado}, next: {next: fin_abstenido}}
  - {id: fin_abstenido, type: end, config: {outcome: abstained}}
  - {id: fin_cancelado, type: end, config: {outcome: cancelled}}
  - {id: esc_sin_datos, type: escalate, config: {reason_code: low_confidence}}
  - {id: esc_tool, type: escalate, config: {reason_code: tool_failure}}
  - {id: esc_monto, type: escalate, config: {reason_code: "policy:escalamiento-disputa-monto", target_queue: disputas}}
  - {id: esc_verif, type: escalate, config: {reason_code: verification_failed}}
```

Plantillas, una por archivo con esta forma (`templates/t/<nombre>@1.0.0.yaml`):

```yaml
id: t/pedir_cargo
version: 1.0.0
locales:
  es: "¿Qué cargo quieres disputar?"
  pt: "Qual cobrança você quer contestar?"
```

Archivos y textos (`es` / `pt`):

| Archivo | `es` | `pt` |
|---|---|---|
| `templates/t/pedir_cargo@1.0.0.yaml` | ¿Qué cargo quieres disputar? | Qual cobrança você quer contestar? |
| `templates/t/resumen_pqr@1.0.0.yaml` | Voy a radicar la disputa del cargo. ¿Confirmas? | Vou registrar a contestação. Confirma? |
| `templates/t/pqr_radicado@1.0.0.yaml` | Radicamos tu disputa con el número {{ facts.pqr_verificada.value.id }}. | Registramos sua contestação com o número {{ facts.pqr_verificada.value.id }}. |
| `templates/t/aclarar_cargo@1.0.0.yaml` | No encontré ese cargo. ¿Puedes darme más detalles? | Não encontrei essa cobrança. Pode dar mais detalhes? |
| `templates/t/no_confirmado@1.0.0.yaml` | No registré la disputa. | Não registrei a contestação. |
| `templates/t/aclarar@1.0.0.yaml` | ¿Puedes aclararlo? | Pode esclarecer? |
| `templates/t/abstencion@1.0.0.yaml` | No puedo ayudarte con eso. | Não posso ajudar com isso. |
| `templates/t/traspaso@1.0.0.yaml` | Te paso con un asesor. | Vou transferir para um atendente. |
| `templates/t/acuse@1.0.0.yaml` | Lo anoto para después. | Anotei para depois. |
| `templates/t/oferta@1.0.0.yaml` | ¿Seguimos con lo pendiente? | Seguimos com o pendente? |
| `templates/t/idioma_no_soportado@1.0.0.yaml` | Atiendo en español y portugués. | Atendo em espanhol e português. |
| `templates/t/mensaje_largo@1.0.0.yaml` | El mensaje es muy largo. | A mensagem é muito longa. |

Tools (`tools/<id>@1.0.0.yaml`):

```yaml
# tools/buscar_transacciones@1.0.0.yaml
id: buscar_transacciones
version: 1.0.0
risk_class: read
min_auth_level: session
idempotent: true
```

```yaml
# tools/seleccionar@1.0.0.yaml
id: seleccionar
version: 1.0.0
risk_class: compute
min_auth_level: session
idempotent: true
```

```yaml
# tools/convertir_moneda@1.0.0.yaml
id: convertir_moneda
version: 1.0.0
risk_class: compute
min_auth_level: session
idempotent: true
```

```yaml
# tools/radicar_pqr@1.0.0.yaml
id: radicar_pqr
version: 1.0.0
risk_class: write_reversible
min_auth_level: session
idempotent: true
readback_by: idempotency_key
```

```yaml
# tools/obtener_pqr@1.0.0.yaml
id: obtener_pqr
version: 1.0.0
risk_class: read
min_auth_level: session
idempotent: true
```

`decision_models/match-cargo@2.0.0.yaml`:

```yaml
id: match-cargo
version: 2.0.0
output_schema:
  type: object
  properties:
    match: {enum: [unica, ninguna, varias]}
    transaction: {type: string}
calibrated_fields: [match]
input_view: [slots.descripcion_cargo, facts.candidatas.value]
providers: [{provider: classifier, config: {artifact: sintetico}}]
calibration: {method: none}
```

`policies/escalamiento-disputa-monto@1.0.0.yaml`:

```yaml
id: escalamiento-disputa-monto
version: 1.0.0
owner: riesgo
expr: {">": [{var: facts.monto_usd.value}, 500]}
rationale: "Disputas sobre montos altos requieren revisión humana (política sintética)."
```

`agents/atencion@1.0.0.yaml`:

```yaml
id: atencion
version: 1.0.0
mode: conversational
entry_flow: disputa-cargo@1
invocable_by: [customer]
min_auth_level: session
subject_kinds: [customer]
supported_locales: [es, pt]
default_locale: es
tools_allowed: [buscar_transacciones@1, seleccionar@1, convertir_moneda@1, radicar_pqr@1, obtener_pqr@1]
budgets: {max_nodes_per_turn: 40, max_model_calls_per_turn: 3, max_tokens_per_run: 20000, max_cost_per_run: 0.50, max_wall_ms_per_turn: 8000}
templates:
  clarify: t/aclarar
  abstain: t/abstencion
  handoff: t/traspaso
  pending_ack: t/acuse
  pending_offer: t/oferta
  unsupported_language: t/idioma_no_soportado
  input_too_large: t/mensaje_largo
max_clarifications: 2
on_clarify_exhausted: escalate
default_target_queue: general
```

`language_detection/lang-es-pt@1.0.0.yaml`:

```yaml
id: lang-es-pt
version: 1.0.0
detector: lingua@1.4.0
candidates: [es, pt]
unsupported: [en]
min_letters: 12
min_letters_unsupported: 20
```

`releases/demo.yaml`:

```yaml
id: demo
agents:
  - {agent: "atencion@^1", aliases: [prod]}
flows: ["disputa-cargo@^1"]
interrupts: []
language_detection: "lang-es-pt@1"
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m01/test_registry.py`:

```python
import shutil
from pathlib import Path

from agent_core.domain import EntityKind, Flow, RefSpec, Template
from agent_core.flows.registry import load_registry
from agent_core.flows.validate import validate_flow, validate_registry

FIXTURE = Path(__file__).parent / "fixtures" / "registry"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# T-M1-15
def test_fixture_loads_clean_and_disputa_cargo_is_valid() -> None:
    reg, violations = load_registry(FIXTURE)
    assert violations == []
    flow = reg.resolve(EntityKind.flow, RefSpec.parse("disputa-cargo"))
    assert isinstance(flow, Flow)
    assert validate_flow(flow, reg) == []
    assert validate_registry(reg) == []


def test_template_reads_derived_from_text() -> None:
    reg, _ = load_registry(FIXTURE)
    tpl = reg.resolve(EntityKind.template, RefSpec.parse("t/pqr_radicado"))
    assert isinstance(tpl, Template)
    assert tpl.reads == frozenset({"facts.pqr_verificada.value.id"})
    assert reg.source(EntityKind.template, "t/pqr_radicado", "1.0.0") == "templates/t/pqr_radicado@1.0.0.yaml"


# T-M1-36
def test_declared_reads_must_match(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "templates/t/x@1.0.0.yaml",
           'id: t/x\nversion: 1.0.0\nlocales: {es: "{{ facts.a.value }}", pt: "{{ facts.a.value }}"}\nreads: []\n')
    _, violations = load_registry(root)
    assert [(v.rule, v.path) for v in violations] == [("G0-01", "templates/t/x@1.0.0.yaml")]


def test_malformed_template_variable(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "templates/t/x@1.0.0.yaml", 'id: t/x\nversion: 1.0.0\nlocales: {es: "{{ USD }}"}\n')
    _, violations = load_registry(root)
    assert [v.rule for v in violations] == ["G0-01"]


def test_prompt_with_variables_is_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "model_profiles/perfil@1.0.0.yaml",
           "id: perfil\nversion: 1.0.0\nendpoint_alias: demo\nmodel: m\ntemperature: 0\nmax_tokens: 100\n"
           "price: {input_per_mtok: 1, output_per_mtok: 2, source: sintetico, as_of: '2026-09-28'}\n")
    _write(root, "prompts/p/x@1.0.0.yaml",
           'id: p/x\nversion: 1.0.0\nlocales: {es: "Hola {{ slots.a }}"}\nmodel_profile: perfil@1\n')
    _, violations = load_registry(root)
    assert [(v.rule, v.path) for v in violations] == [("G0-01", "prompts/p/x@1.0.0.yaml")]


def test_filename_must_match_content(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/otro@1.0.0.yaml", "id: distinto\nversion: 1.0.0\nrisk_class: read\n"
                                          "min_auth_level: session\nidempotent: true\n")
    _, violations = load_registry(root)
    assert [(v.rule, v.path) for v in violations] == [("G0-01", "tools/otro@1.0.0.yaml")]


def test_unreadable_yaml_and_invalid_flow(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/roto@1.0.0.yaml", "id: roto\nversion: [1\n")
    _write(root, "flows/malo@1.0.0.yaml", "id: malo\nversion: 1.0.0\npriority: 1\nnodes:\n"
                                          "  - {id: k, type: knowledge, config: {}}\n")
    _, violations = load_registry(root)
    assert sorted((v.rule, (v.path or "").split("#")[0]) for v in violations) == [
        ("G0-01", "flows/malo@1.0.0.yaml"), ("G0-01", "tools/roto@1.0.0.yaml")]
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_registry.py -v`
Esperado: FAIL con `ImportError: cannot import name 'load_registry'`.

- [ ] **Step 4: Implementar `load_registry` en `registry.py`**

Agrega a los imports de `registry.py`:

```python
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from agent_core.flows.schema import parse_flow
from agent_core.flows.violations import FlowSchemaError, Violation, sort_violations
from agent_core.flows.yaml_loader import YamlError, load_yaml
```

y al final del archivo:

```python
DIRS: Mapping[EntityKind, str] = MappingProxyType(
    {
        EntityKind.agent: "agents", EntityKind.flow: "flows", EntityKind.policy: "policies",
        EntityKind.template: "templates", EntityKind.prompt: "prompts", EntityKind.tool: "tools",
        EntityKind.decision_model: "decision_models", EntityKind.model_profile: "model_profiles",
        EntityKind.language_detection: "language_detection", EntityKind.injection_ruleset: "injection_rulesets",
    }
)


def _g0_01(path: str, message: str, flow: str | None = None) -> Violation:
    return Violation(rule="G0-01", flow=flow, path=path, message=message)


def _template_data(data: Mapping[str, object]) -> tuple[Mapping[str, object], str | None]:
    """Deriva `reads` de `{{ }}` y compara con lo declarado (M1 §3.3)."""
    locales = data.get("locales")
    if not isinstance(locales, dict):
        return data, None
    derived: set[str] = set()
    for locale, text in sorted(locales.items()):
        if isinstance(text, str):
            try:
                derived |= template_vars(text)
            except ValueError as exc:
                return data, f"locale {locale}: {exc}"
    declared = data.get("reads")
    if declared is not None and (not isinstance(declared, list) or set(declared) != derived):
        return data, f"reads declarado distinto del derivado de la plantilla: {sorted(derived)}"
    return {**data, "reads": sorted(derived)}, None


def _prompt_problem(data: Mapping[str, object]) -> str | None:
    locales = data.get("locales")
    if isinstance(locales, dict):
        for locale, text in sorted(locales.items()):
            if isinstance(text, str) and ("{{" in text or "}}" in text):
                return f"locale {locale}: un prompt no lleva variables (ADR 0016)"
    return None


def _load_file(reg: AuthoringRegistry, root: Path, kind: EntityKind, path: Path) -> list[Violation]:
    rel = path.relative_to(root).as_posix()
    stem = path.relative_to(root / DIRS[kind]).as_posix().removesuffix(".yaml")
    ident, sep, version = stem.rpartition("@")
    label = f"{ident}@{version}" if kind == EntityKind.flow and sep else None
    if path.is_symlink():
        return [_g0_01(rel, "enlaces simbólicos no admitidos", label)]
    if not sep:
        return [_g0_01(rel, "el nombre del archivo debe ser <id>@<versión>.yaml", label)]
    try:
        data = load_yaml(path.read_bytes())
    except YamlError as exc:
        return [_g0_01(rel, f"YAML inválido: {exc}", label)]
    if not isinstance(data, dict):
        return [_g0_01(rel, "el archivo debe contener un objeto", label)]
    if data.get("id") != ident or data.get("version") != version:
        return [_g0_01(rel, f"id y version deben coincidir con el nombre del archivo ({ident}@{version})", label)]
    if kind == EntityKind.flow:
        try:
            reg.add(parse_flow(data, source=rel), rel)
        except FlowSchemaError as exc:
            return exc.violations
        return []
    payload: Mapping[str, object] = data
    if kind == EntityKind.template:
        payload, problem = _template_data(payload)
        if problem:
            return [_g0_01(rel, problem)]
    if kind == EntityKind.prompt and (problem := _prompt_problem(payload)):
        return [_g0_01(rel, problem)]
    try:
        entity = ENTITY_TYPES[kind].model_validate(payload)
    except ValidationError as exc:
        return [
            _g0_01(f"{rel}#/" + "/".join(str(p) for p in err["loc"]), f"valor inválido: {err['msg']}")
            for err in exc.errors()
        ]
    reg.add(entity, rel)  # type: ignore[arg-type]
    return []


def load_registry(root: Path) -> tuple[AuthoringRegistry, list[Violation]]:
    """Carga `<raíz>/<tipo>/<id>@<versión>.yaml` y `releases/<id>.yaml` (M1 §3.11). Nunca lanza por un archivo."""
    reg = AuthoringRegistry()
    violations: list[Violation] = []
    for kind, folder in DIRS.items():
        base = root / folder
        if base.is_dir():
            for path in sorted(base.rglob("*.yaml")):
                violations += _load_file(reg, root, kind, path)
    releases = root / "releases"
    if releases.is_dir():
        for path in sorted(releases.glob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            try:
                data = load_yaml(path.read_bytes())
                decl = ReleaseDecl.model_validate(data)
            except (YamlError, ValidationError) as exc:
                violations.append(_g0_01(rel, f"release inválida: {exc}"))
                continue
            if decl.id != path.stem:
                violations.append(_g0_01(rel, "el id de la release debe coincidir con el nombre del archivo"))
                continue
            reg.add_release(decl, rel)
    return reg, sort_violations(violations)
```

Si `mypy` no necesita el `type: ignore[arg-type]` de `reg.add(entity, rel)`, quítalo. Si hace falta, la alternativa es `cast(RegistryEntity, entity)`.

- [ ] **Step 5: Agregar `validate_registry` a `validate.py`**

```python
from agent_core.domain import EntityKind
from agent_core.flows.registry import AuthoringRegistry


def validate_registry(reg: AuthoringRegistry) -> list[Violation]:
    """Toda versión de todo flow del registro (la Task 13 agrega agentes y releases)."""
    found: list[Violation] = []
    for entity in reg.all(EntityKind.flow):
        if isinstance(entity, Flow):
            found += validate_flow(entity, reg)
    return sort_violations(found)
```

- [ ] **Step 6: Correr las pruebas**

Run: `uv run pytest tests/m01 -v`
Esperado: todas PASS. Si `test_fixture_loads_clean_and_disputa_cargo_is_valid` falla con violaciones del fixture, léelas: el fixture debe ser válido tal cual (spec §12). Corrige el YAML del fixture, nunca la regla, salvo que la regla contradiga el spec.

- [ ] **Step 7: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows tests/m01
git commit -m "feat(m1): carga del registro desde YAML (reads derivado, prompts sin variables) y fixture disputa-cargo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `pin_release`, `release_view` y `registry_from_directory` (T-M1-43, T-M1-45)

**Files:**
- Create: `agent_core/flows/pin.py`, `testing/fakes/registry_dir.py`
- Modify: `testing/fakes/__init__.py` (exportar `registry_from_directory`)
- Test: `tests/m01/test_pin.py`

**Interfaces:**
- Consumes: `AuthoringRegistry`, `ReleaseDecl` (Task 3); `entity_ref_sites`, `pointer_str` (Task 3); `best_version`, `release_view` (Task 3); `load_registry`, `validate_registry` (Task 9); `Release`, `Interrupt`, `StartFlowAction`, `require_exact_refs` (M0); `InMemoryRegistry` (M0, `testing.fakes`).
- Produces:
  - `PinnedRelease(release: Release, entities: list[RegistryEntity], aliases: dict[str, list[str]])` (dataclass congelada);
  - `pin_release(reg, release_id) -> PinnedRelease` (lanza `SchemaError`);
  - `registry_from_directory(root: Path, release_id: str) -> InMemoryRegistry`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_pin.py`:

```python
from pathlib import Path

import pytest

from agent_core.domain import EntityKind, EntityRef, Flow, RefSpec, SchemaError, require_exact_refs
from agent_core.flows.claims import derive_claims
from agent_core.flows.pin import pin_release
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl, load_registry
from agent_core.flows.view import release_view
from testing.fakes import InMemoryRegistry, registry_from_directory
from tests.m01.cases import ENTITIES, base, flow

FIXTURE = Path(__file__).parent / "fixtures" / "registry"


# T-M1-43
def test_pin_fixture_release() -> None:
    reg, violations = load_registry(FIXTURE)
    assert violations == []
    pinned = pin_release(reg, "demo")
    assert pinned.release.status == "active"
    assert pinned.release.entities[EntityKind.flow] == {"disputa-cargo": "1.0.0"}
    assert pinned.release.entities[EntityKind.decision_model] == {"match-cargo": "2.0.0"}
    assert str(pinned.release.language_detection) == "lang-es-pt@1.0.0"
    assert pinned.aliases == {"atencion": ["prod"]}
    for entity in pinned.entities:
        require_exact_refs(entity)
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    loaded = memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow)
    assert str(loaded.nodes[1].config.tool) == "buscar_transacciones@1.0.0"  # type: ignore[union-attr]


def test_pin_conflicting_versions() -> None:
    v1 = flow(base())
    v11 = flow({**base(), "version": "1.1.0"})
    decl = ReleaseDecl.model_validate(
        {"id": "r", "agents": [{"agent": "atencion@1"}], "flows": ["base@1.0.0"], "language_detection": "lang@1"}
    )
    from tests.m01.cases import agent

    reg = AuthoringRegistry.from_entities([*ENTITIES, v1, v11, agent(entry_flow="base@^1")], [decl])
    with pytest.raises(SchemaError, match="base"):
        pin_release(reg, "r")


def test_pin_unknown_release() -> None:
    with pytest.raises(SchemaError):
        pin_release(AuthoringRegistry(), "nada")


# T-M1-45
def test_claims_same_with_release_view() -> None:
    reg, _ = load_registry(FIXTURE)
    pinned = pin_release(reg, "demo")
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    authoring = reg.resolve(EntityKind.flow, RefSpec.parse("disputa-cargo"))
    assert isinstance(authoring, Flow)
    runtime = memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow)
    view = release_view(memory, pinned.release)
    assert dict(derive_claims(runtime, view)) == dict(derive_claims(authoring, reg))
    assert dict(derive_claims(runtime, view)) == {
        "responder_ok": frozenset({"confirmar"}), "aclarar": frozenset(), "no_confirmado": frozenset()}


def test_registry_from_directory() -> None:
    memory = registry_from_directory(FIXTURE, "demo")
    assert memory.release_status("demo") == "active"
    assert memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow).id == "disputa-cargo"
```

En `test_pin_conflicting_versions`, mueve `from tests.m01.cases import agent` al bloque de imports de arriba. `lang@1` no existe; aun así el conflicto aparece en el mensaje, porque `pin_release` junta todos los errores antes de lanzar.

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_pin.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.pin'`.

- [ ] **Step 3: Implementar `pin.py`**

```python
"""Publicación simulada para la demo y las pruebas (M1 §3.11). No es la unidad 2: no valida."""

from collections import deque
from dataclasses import dataclass
from typing import Any

from agent_core.domain import (
    EntityKind,
    Interrupt,
    RefSpec,
    RegistryEntity,
    Release,
    SchemaError,
    StartFlowAction,
    require_exact_refs,
)
from agent_core.flows.refs import Pointer, entity_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry
from agent_core.flows.view import best_version

Chosen = dict[tuple[EntityKind, str], str]


@dataclass(frozen=True)
class PinnedRelease:
    release: Release
    entities: list[RegistryEntity]
    aliases: dict[str, list[str]]


def _set(doc: Any, pointer: Pointer, value: str) -> None:
    current = doc
    for part in pointer[:-1]:
        current = current[part]
    current[pointer[-1]] = value


def _exact(chosen: Chosen, kind: EntityKind, ref: RefSpec) -> str:
    return f"{ref.id}@{chosen[(kind, ref.id)]}"


def _pinned(entity: RegistryEntity, chosen: Chosen) -> RegistryEntity:
    sites = entity_ref_sites(entity)
    if not sites:
        return entity
    doc = entity.model_dump(mode="python", by_alias=True)
    for site in sites:
        _set(doc, site.pointer, _exact(chosen, site.kind, site.ref))
    return type(entity).model_validate(doc)


def _pinned_interrupt(interrupt: Interrupt, chosen: Chosen) -> Interrupt:
    doc = interrupt.model_dump(mode="python")
    if isinstance(interrupt.action, StartFlowAction):
        doc["action"]["flow"] = _exact(chosen, EntityKind.flow, interrupt.action.flow)
    if interrupt.signal_policy is not None:
        doc["signal_policy"] = _exact(chosen, EntityKind.policy, interrupt.signal_policy)
    return Interrupt.model_validate(doc)


def pin_release(reg: AuthoringRegistry, release_id: str) -> PinnedRelease:
    decl = reg.release(release_id)
    if decl is None:
        raise SchemaError(f"la release {release_id!r} no existe en el registro")
    chosen: Chosen = {}
    errors: set[str] = set()
    pending: deque[tuple[EntityKind, str, str]] = deque()

    def want(kind: EntityKind, ref: RefSpec, where: str) -> None:
        best = best_version(ref.spec, reg.versions(kind, ref.id))
        if best is None:
            errors.add(f"{where}: {kind.value} {ref} no resuelve")
            return
        previous = chosen.get((kind, ref.id))
        if previous is None:
            chosen[(kind, ref.id)] = best
            pending.append((kind, ref.id, best))
        elif previous != best:
            errors.add(f"{where}: {kind.value} {ref.id} resuelve a {best} y a {previous}")

    for i, entry in enumerate(decl.agents):
        want(EntityKind.agent, entry.agent, f"agents/{i}")
    for i, ref in enumerate(decl.flows):
        want(EntityKind.flow, ref, f"flows/{i}")
    for i, interrupt in enumerate(decl.interrupts):
        if isinstance(interrupt.action, StartFlowAction):
            want(EntityKind.flow, interrupt.action.flow, f"interrupts/{i}/action/flow")
        if interrupt.signal_policy is not None:
            want(EntityKind.policy, interrupt.signal_policy, f"interrupts/{i}/signal_policy")
    want(EntityKind.language_detection, decl.language_detection, "language_detection")
    if decl.injection_ruleset is not None:
        want(EntityKind.injection_ruleset, decl.injection_ruleset, "injection_ruleset")
    while pending:
        kind, ident, version = pending.popleft()
        for site in entity_ref_sites(reg.get_exact(kind, ident, version)):
            want(site.kind, site.ref, f"{kind.value} {ident}@{version} {pointer_str(site.pointer)}")
    if errors:
        raise SchemaError("pin_release: " + "; ".join(sorted(errors)))

    ordered = sorted(chosen.items())
    entities = [_pinned(reg.get_exact(kind, ident, version), chosen) for (kind, ident), version in ordered]
    table: dict[EntityKind, dict[str, str]] = {}
    for (kind, ident), version in ordered:
        table.setdefault(kind, {})[ident] = version
    release = Release.model_validate(
        {
            "id": decl.id,
            "status": "active",
            "entities": table,
            "interrupts": [_pinned_interrupt(i, chosen) for i in decl.interrupts],
            "language_detection": _exact(chosen, EntityKind.language_detection, decl.language_detection),
            "injection_ruleset": (
                _exact(chosen, EntityKind.injection_ruleset, decl.injection_ruleset)
                if decl.injection_ruleset is not None else None
            ),
            "max_input_chars": decl.max_input_chars,
        }
    )
    require_exact_refs(release)
    for entity in entities:
        require_exact_refs(entity)
    return PinnedRelease(release=release, entities=entities,
                         aliases={entry.agent.id: list(entry.aliases) for entry in decl.agents})
```

- [ ] **Step 4: Implementar `testing/fakes/registry_dir.py`**

```python
"""Carga un registro de autoría, lo valida, fija una release y lo deja en un InMemoryRegistry (M1 §2)."""

from pathlib import Path

from agent_core.domain import SchemaError
from agent_core.flows.pin import pin_release
from agent_core.flows.registry import load_registry
from agent_core.flows.validate import validate_registry
from testing.fakes.registry import InMemoryRegistry


def registry_from_directory(root: Path, release_id: str) -> InMemoryRegistry:
    reg, violations = load_registry(root)
    problems = [*violations, *validate_registry(reg)]
    if problems:
        raise SchemaError("registro inválido: " + "; ".join(f"{v.rule} {v.path}: {v.message}" for v in problems))
    pinned = pin_release(reg, release_id)
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    for agent_id, aliases in pinned.aliases.items():
        for alias in aliases:
            memory.add_release(pinned.release, agent_id, alias=alias)
    return memory
```

Si `InMemoryRegistry` no vive en `testing/fakes/registry.py`, importa desde el módulo donde M0 lo definió (búscalo con `grep -rn "class InMemoryRegistry" testing`).

Agrega en `testing/fakes/__init__.py` la línea de import y el nombre a `__all__` (si existe):

```python
from testing.fakes.registry_dir import registry_from_directory
```

- [ ] **Step 5: Correr, lint y commit**

```bash
uv run pytest tests/m01 -v && uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/flows/pin.py testing/fakes tests/m01/test_pin.py
git commit -m "feat(m1): pin_release (publicación simulada) y registry_from_directory

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: todas PASS.

---

### Task 11: CLI `agentcore validate`, interfaz pública y determinismo (T-M1-41, T-M1-44)

**Files:**
- Create: `agent_core/flows/cli_validate.py`
- Modify: `agent_core/cli.py`, `agent_core/flows/__init__.py`, `.github/workflows/ci.yml`
- Test: `tests/m01/test_cli.py`, `tests/m01/test_determinism.py`, `tests/m01/test_public_api.py`

**Interfaces:**
- Consumes: `load_registry` (Task 9), `validate_registry` (Task 9), `sort_violations` (Task 1); `SystemClock` (M0).
- Produces:
  - `run_validate(root: Path, *, as_json: bool) -> tuple[int, str]` (código, texto para stdout);
  - `format_violation(v) -> str`;
  - subcomando `agentcore validate <ruta> [--json]`;
  - `agent_core.flows` exporta la interfaz pública de M1 §2.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_cli.py`:

```python
import json
import shutil
from pathlib import Path

import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.cli import main
from agent_core.flows.validate import validate_flow
from tests.m01.cases import flow, registry

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
ROTO = """id: roto
version: 1.0.0
priority: 1
nodes:
  - {id: leer, type: tool, config: {tool: buscar_transacciones@1, args: {texto: slots.x}, save_as: d}, next: {ok: fin, error: esc, timeout: esc}}
  - {id: fin, type: end, config: {outcome: resolved}}
  - {id: esc, type: escalate, config: {reason_code: tool_failure}}
"""


# T-M1-44
def test_cli_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(FIXTURE)]) == 0
    assert "sin violaciones" in capsys.readouterr().out


def test_cli_json_with_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    (root / "flows" / "roto@1.0.0.yaml").write_text(ROTO, encoding="utf-8")
    assert main(["validate", str(root), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == 1 and data["ok"] is False
    assert data["counts"] == {"G0-03": 1}
    assert data["violations"][0]["flow"] == "roto@1.0.0"
    assert data["violations"][0]["node_id"] == "leer"


def test_cli_text_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    (root / "flows" / "roto@1.0.0.yaml").write_text(ROTO, encoding="utf-8")
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    assert "G0-03 roto@1.0.0 leer /nodes/0/next: el resultado 'denied' no tiene next" in out


def test_cli_missing_root(tmp_path: Path) -> None:
    assert main(["validate", str(tmp_path / "nada")]) == 2


def _long_flow(k: int) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {"id": f"r{i}", "type": "respond", "config": {"template_ref": "t/seguro"}, "next": {"next": f"r{i + 1}"}}
        for i in range(99)
    ]
    nodes.append({"id": "r99", "type": "end", "config": {"outcome": "resolved"}})
    return {"id": f"perf-{k}", "version": "1.0.0", "priority": 1, "nodes": nodes}


def test_validation_time_budget() -> None:
    flows = [flow(_long_flow(k)) for k in range(50)]
    reg = registry(*flows)
    clock = SystemClock()
    start = clock.monotonic_ns()
    for item in flows:
        assert validate_flow(item, reg) == []
    assert (clock.monotonic_ns() - start) / 1e9 < 2.0
```

`tests/m01/test_determinism.py` (no importa `random`, que `ruff` prohíbe; el barajado usa el `Random` que entrega `hypothesis`):

```python
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_core.flows.validate import validate_flow
from agent_core.flows.violations import Violation
from tests.m01.cases import base, flow, registry

IDS = [n["id"] for n in base()["nodes"]] + ["fantasma"]
KEYS = ["ok", "error", "yes", "no", "next", "verified", "failed", "uncertain", "denied", "x"]
EDIT = st.tuples(st.integers(0, 8), st.sampled_from(KEYS), st.one_of(st.none(), st.sampled_from(IDS)))


def _strip(violations: list[Violation]) -> list[tuple[str, str | None, str | None, str]]:
    return sorted((v.rule, v.flow, v.node_id, v.message) for v in violations)


# T-M1-41
@settings(max_examples=200, deadline=None)
@given(st.lists(EDIT, max_size=12), st.randoms(use_true_random=False))
def test_validate_is_total_sorted_and_order_independent(edits: list[tuple[int, str, str | None]], rnd: Any) -> None:
    d = base()
    for index, key, target in edits:
        nxt = d["nodes"][index].setdefault("next", {})
        if target is None:
            nxt.pop(key, None)
        else:
            nxt[key] = target
    reg = registry()
    first = validate_flow(flow(d), reg)
    assert first == sorted(first, key=Violation.sort_key)
    rest = d["nodes"][1:]
    rnd.shuffle(rest)
    second = validate_flow(flow({**d, "nodes": [d["nodes"][0], *rest]}), reg)
    assert _strip(first) == _strip(second)
```

`tests/m01/test_public_api.py`:

```python
import agent_core.flows as flows

PUBLIC = [
    "Violation", "FlowSchemaError", "parse_flow", "RegistryView", "release_view", "validate_flow",
    "derive_claims", "JSONLOGIC_OPS", "jsonlogic_problems", "Path", "parse_path", "value_paths", "template_vars",
    "ReleaseDecl", "AuthoringRegistry", "load_yaml", "load_registry", "PinnedRelease", "pin_release",
    "validate_registry",
]


def test_public_interface() -> None:
    assert [name for name in PUBLIC if not hasattr(flows, name)] == []
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_cli.py tests/m01/test_public_api.py tests/m01/test_determinism.py -v`
Esperado: FAIL en las pruebas de CLI (`invalid choice: 'validate'`) y en la de interfaz pública. La de determinismo debería pasar; si falla, es un bug real de orden o de totalidad: corrígelo antes de seguir.

- [ ] **Step 3: Implementar `cli_validate.py`**

```python
"""Lógica de `agentcore validate` (M1 §3.12). Sin I/O de consola: devuelve el código y el texto."""

import json
from collections import Counter
from pathlib import Path

from agent_core.flows.registry import load_registry
from agent_core.flows.validate import validate_registry
from agent_core.flows.violations import Violation, sort_violations


def format_violation(v: Violation) -> str:
    return f"{v.rule} {v.flow or '-'} {v.node_id or '-'} {v.path or '-'}: {v.message}"


def run_validate(root: Path, *, as_json: bool) -> tuple[int, str]:
    if not root.is_dir():
        return 2, f"no existe el directorio del registro: {root}"
    reg, load_violations = load_registry(root)
    violations = sort_violations([*load_violations, *validate_registry(reg)])
    counts = dict(sorted(Counter(v.rule for v in violations).items()))
    code = 1 if violations else 0
    if as_json:
        payload = {"format": 1, "ok": not violations, "violations": [v.model_dump() for v in violations],
                   "counts": counts}
        return code, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [format_violation(v) for v in violations]
    if violations:
        lines.append(f"{len(violations)} violaciones: " + ", ".join(f"{r}={n}" for r, n in counts.items()))
    else:
        lines.append("sin violaciones")
    return code, "\n".join(lines)
```

- [ ] **Step 4: Agregar el subcomando en `agent_core/cli.py`**

Agrega a los imports:

```python
from agent_core.adapters.system_clock import SystemClock
from agent_core.flows.cli_validate import run_validate
```

Después del subparser `contracts`:

```python
    validate = sub.add_parser("validate", help="valida un registro de autoría (M1)")
    validate.add_argument("root", type=Path)
    validate.add_argument("--json", action="store_true", help="salida JSON estable")
```

Y antes de `return 2`:

```python
    if args.command == "validate":
        clock = SystemClock()
        start = clock.monotonic_ns()
        code, output = run_validate(args.root, as_json=args.json)
        print(output)
        if not args.json:
            print(f"validado en {(clock.monotonic_ns() - start) // 1_000_000} ms", file=sys.stderr)
        return code
```

Actualiza el docstring del módulo: `"""CLI `agentcore`: `contracts [--check] [--out DIR]` y `validate <ruta> [--json]`."""`.

- [ ] **Step 5: Interfaz pública**

`agent_core/flows/__init__.py`:

```python
"""M1 — esquema de flows y validación estática (docs/specs/motor/m01-validacion-estatica.md)."""

from agent_core.flows.claims import derive_claims
from agent_core.flows.jsonlogic import JSONLOGIC_OPS, expr_paths, jsonlogic_problems
from agent_core.flows.paths import Path, parse_path, template_vars, value_paths
from agent_core.flows.pin import PinnedRelease, pin_release
from agent_core.flows.registry import AuthoringRegistry, ReleaseAgent, ReleaseDecl, load_registry
from agent_core.flows.schema import parse_flow
from agent_core.flows.validate import validate_flow, validate_registry
from agent_core.flows.view import RegistryView, release_view
from agent_core.flows.violations import FlowSchemaError, Violation
from agent_core.flows.yaml_loader import YamlError, load_yaml

__all__ = [
    "JSONLOGIC_OPS", "AuthoringRegistry", "FlowSchemaError", "Path", "PinnedRelease", "RegistryView",
    "ReleaseAgent", "ReleaseDecl", "Violation", "YamlError", "derive_claims", "expr_paths", "jsonlogic_problems",
    "load_registry", "load_yaml", "parse_flow", "parse_path", "pin_release", "release_view", "template_vars",
    "validate_flow", "validate_registry", "value_paths",
]
```

- [ ] **Step 6: CI**

En `.github/workflows/ci.yml`, después de `- run: uv run agentcore contracts --check`:

```yaml
      - run: uv run agentcore validate tests/m01/fixtures/registry
```

- [ ] **Step 7: Correr todo, lint y commit**

```bash
uv run pytest && uv run ruff check . && uv run mypy && uv run lint-imports && uv run agentcore validate tests/m01/fixtures/registry
git add agent_core tests/m01 .github/workflows/ci.yml
git commit -m "feat(m1): CLI agentcore validate (texto y --json), interfaz pública y prueba de determinismo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: todo en verde; la CLI imprime `sin violaciones`.

**Fin de la fase 1.** Antes de seguir, marca los puntos de fase 1 de la definición de terminado del spec (§10) en el reporte de la tarea.

---

### Task 12 (fase 5): G0-07, G0-08, G0-10, G0-11, G0-13…G0-16

**Files:**
- Create: `agent_core/flows/rules/phase5.py`
- Modify: `agent_core/flows/validate.py` (agregar las reglas a `FLOW_RULES`)
- Test: `tests/m01/test_rules_phase5.py`

**Interfaces:**
- Consumes: `Ctx` (Task 5), `flow_mode` e `is_waiting` (Task 5), `expr_paths` y `expr_literals` (Task 1), `parse_path` y `value_paths` (Task 1), `is_declarable` (M0).
- Produces: `g0_07`, `g0_08`, `g0_10`, `g0_11`, `g0_13`, `g0_14`, `g0_15`, `g0_16`. G0-09 ya lo cubre `parse_flow` (Task 4).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_rules_phase5.py`:

```python
from typing import Any

from agent_core.domain import Flow
from agent_core.flows.context import Ctx
from agent_core.flows.rules.phase5 import g0_07
from tests.m01.cases import base, check, node, registry, rules, task_base


def _with_rule(expr: Any) -> dict[str, Any]:
    d = base()
    node(d, "buscar")["next"]["ok"] = "r"
    d["nodes"].append({"id": "r", "type": "rule", "config": {"expr": expr},
                       "next": {"true": "confirmar", "false": "confirmar"}})
    return d


def _with_decide(model: str) -> dict[str, Any]:
    d = base()
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append({"id": "elige", "type": "decide", "config": {"model": model, "branch_on": "campo", "save_as": "d"},
                       "next": {"a": "confirmar", "b": "confirmar", "low_confidence": "esc"}})
    return d


def test_base_still_valid() -> None:
    assert check(base()) == []


# T-M1-07 (regla de producción, probada directo)
def test_g0_07_agent_node_with_write_tool() -> None:
    d = base()
    d["nodes"].append({"id": "ag", "type": "agent",
                       "config": {"tools_allowed": ["escribir@1"], "max_steps": 3, "prompt_ref": "p/gen", "goal": "x"}})
    found = list(g0_07(Ctx.build(Flow.model_validate(d), registry())))
    assert [v.rule for v in found] == ["G0-07"]


# T-M1-08, T-M1-25
def test_business_literal_in_rule() -> None:
    assert rules(check(_with_rule({">": [{"var": "facts.datos.value.n"}, 500]}))) == {"G0-08"}
    assert rules(check(_with_rule({">": [{"var": "facts.datos.value.n"}, 0]}))) == {"G0-08"}


def test_enum_value_of_collect_is_allowed() -> None:
    d = _with_rule({"==": [{"var": "slots.desc"}, "alta"]})
    node(d, "pedir")["config"]["validator"] = {"kind": "enum", "value": ["alta", "baja"]}
    assert check(d) == []
    d_in = _with_rule({"in": [{"var": "slots.desc"}, ["alta", "otra"]]})
    node(d_in, "pedir")["config"]["validator"] = {"kind": "enum", "value": ["alta", "baja"]}
    assert rules(check(d_in)) == {"G0-08"}


def test_var_default_is_a_literal() -> None:
    assert rules(check(_with_rule({"==": [{"var": ["slots.desc", "x"]}, None]}))) == {"G0-08"}


# T-M1-10
def test_decisions_in_read_tool() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "decisions.d.campo"}
    assert rules(check(d)) == {"G0-10"}


# T-M1-39
def test_rule_reading_decisions() -> None:
    assert rules(check(_with_rule({"==": [{"var": "decisions.d.campo"}, None]}))) == {"G0-10"}


def test_confirm_args_with_decisions() -> None:
    d = base()
    node(d, "confirmar")["config"]["action"]["args"] = {"q": "decisions.d.campo"}
    assert rules(check(d)) == {"G0-10"}


def test_template_reading_decisions() -> None:
    d = base()
    node(d, "ok_msg")["config"]["template_ref"] = "t/lee_dec"
    assert rules(check(d)) == {"G0-10"}


def test_whole_fact_outside_allowed_facts() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "facts.datos"}
    assert rules(check(d)) == {"G0-10"}


def test_readback_outside_verify() -> None:
    d = base()
    node(d, "buscar")["config"]["args"] = {"q": "readback.status"}
    assert rules(check(d)) == {"G0-10"}


# T-M1-11
def test_decide_on_uncalibrated_field() -> None:
    assert check(_with_decide("modelo@1")) == []
    assert rules(check(_with_decide("modelo_nc@1"))) == {"G0-11"}


# T-M1-13
def test_claims_with_non_confirm_id() -> None:
    d = base()
    node(d, "ok_msg")["config"]["claims"] = ["confirmar", "buscar"]
    assert rules(check(d)) == {"G0-13"}


# T-M1-14, T-M1-22
def test_undeclarable_outcomes() -> None:
    for outcome in ("abandoned", "escalated"):
        d = base()
        node(d, "fin")["config"]["outcome"] = outcome
        assert rules(check(d)) == {"G0-14"}


def test_mixed_modes() -> None:
    d = base()
    node(d, "fin_cancelado")["config"]["outcome"] = "completed"
    assert rules(check(d)) == {"G0-14"}


# T-M1-24
def test_task_flow_failed_end_is_valid() -> None:
    assert check(task_base()) == []


# T-M1-27
def test_prompt_without_model_profile() -> None:
    d = base()
    node(d, "ok_msg")["config"] = {"generate": {"prompt_ref": "p/sinperfil@1", "allowed_facts": ["facts.verif"],
                                                "fallback_template_ref": "t/hecho"}, "claims": ["confirmar"]}
    assert rules(check(d)) == {"G0-15"}
    node(d, "ok_msg")["config"]["generate"]["prompt_ref"] = "p/gen@1"
    assert check(d) == []


# T-M1-28
def test_task_flow_with_waiting_node() -> None:
    d = task_base()
    d["nodes"].insert(0, {"id": "pedir", "type": "collect", "config": {"slot": "q", "prompt_ref": "t/pedir"},
                          "next": {"ok": "buscar", "max_attempts": "fin_fallo"}})
    assert rules(check(d)) == {"G0-16"}
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_rules_phase5.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.rules.phase5'`.

- [ ] **Step 3: Implementar `rules/phase5.py`**

```python
"""Reglas de la fase 5: G0-07, G0-08, G0-10, G0-11, G0-13, G0-14, G0-15 y G0-16 (M1 §3.4, §3.9)."""

from collections.abc import Iterable, Iterator

from agent_core.domain import (
    DECLARABLE,
    AgentNode,
    CollectNode,
    ConfirmNode,
    DecideNode,
    EndNode,
    EntityKind,
    EscalateNode,
    RespondNode,
    RiskClass,
    RuleNode,
    ToolNode,
    VerifyNode,
    is_declarable,
)
from agent_core.flows.context import Ctx
from agent_core.flows.graph import flow_mode, is_waiting
from agent_core.flows.jsonlogic import expr_literals, expr_paths
from agent_core.flows.paths import Path, parse_path, value_paths
from agent_core.flows.violations import Violation

READ_CLASSES = frozenset({RiskClass.read, RiskClass.compute})
SLOTS_FACTS = frozenset({"slots", "facts"})


def g0_07(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, AgentNode):
            for i, ref in enumerate(node.config.tools_allowed):
                tool = ctx.tool(ref)
                if tool is not None and tool.risk_class not in READ_CLASSES:
                    yield ctx.v("G0-07", node.id, f"el nodo agent solo usa tools read o compute; {ref} no lo es",
                                f"/config/tools_allowed/{i}")


def g0_08(ctx: Ctx) -> Iterator[Violation]:
    enums: set[str] = set()
    for node in ctx.flow.nodes:
        if isinstance(node, CollectNode) and node.config.validator is not None:
            value = node.config.validator.value
            if node.config.validator.kind == "enum" and isinstance(value, list):
                enums |= {v for v in value if isinstance(v, str)}
    for node in ctx.flow.nodes:
        if not isinstance(node, RuleNode) or node.config.expr is None:
            continue
        for where, literal in expr_literals(node.config.expr):
            if literal is None or isinstance(literal, bool) or (isinstance(literal, str) and literal in enums):
                continue
            yield ctx.v("G0-08", node.id, f"literal de negocio {literal!r}: usa una policy protegida (ADR 0009)",
                        f"/config/expr{where}")


def _parsed(texts: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for text in texts:
        try:
            path = parse_path(text)
        except ValueError:
            continue
        if path is not None:
            paths.append(path)
    return paths


def _template_paths(ctx: Ctx, ref: object) -> list[Path]:
    from agent_core.domain import RefSpec

    if not isinstance(ref, RefSpec):
        return []
    template = ctx.template(ref)
    return _parsed(sorted(template.reads)) if template is not None else []


def _read_sites(ctx: Ctx, node: object) -> Iterator[tuple[str, list[Path], frozenset[str], bool]]:
    """(subruta, rutas, espacios permitidos, admite facts.<n> sin .value) por lugar de la tabla M1 §3.2."""
    if isinstance(node, ToolNode):
        tool = ctx.tool(node.config.tool)
        if tool is None:
            return
        allowed = SLOTS_FACTS | {"decisions"} if tool.risk_class == RiskClass.compute else SLOTS_FACTS
        yield ("/config/args", value_paths(dict(node.config.args), strict=False), allowed, False)
    elif isinstance(node, ConfirmNode):
        yield ("/config/action/args", value_paths(dict(node.config.action.args), strict=False), SLOTS_FACTS, False)
        yield ("/config/summary_template", _template_paths(ctx, node.config.summary_template), SLOTS_FACTS, False)
        yield ("/config/reprompt_template", _template_paths(ctx, node.config.reprompt_template), SLOTS_FACTS, False)
    elif isinstance(node, RuleNode) and node.config.expr is not None:
        yield ("/config/expr", expr_paths(node.config.expr), SLOTS_FACTS, False)
    elif isinstance(node, VerifyNode):
        yield ("/config/predicate", expr_paths(node.config.predicate), SLOTS_FACTS | {"readback"}, False)
        if node.config.by.startswith("fact:"):
            yield ("/config/by", _parsed([node.config.by.removeprefix("fact:")]), frozenset({"facts"}), False)
    elif isinstance(node, EscalateNode) and node.config.priority_expr is not None:
        yield ("/config/priority_expr", expr_paths(node.config.priority_expr), SLOTS_FACTS, False)
    elif isinstance(node, EndNode) and node.config.output_map:
        yield ("/config/output_map", _parsed(sorted(node.config.output_map.values())), SLOTS_FACTS, False)
    elif isinstance(node, DecideNode) and node.config.input_view:
        yield ("/config/input_view", _parsed(node.config.input_view), SLOTS_FACTS, False)
    elif isinstance(node, CollectNode):
        yield ("/config/prompt_ref", _template_paths(ctx, node.config.prompt_ref), SLOTS_FACTS, False)
    elif isinstance(node, RespondNode):
        yield ("/config/template_ref", _template_paths(ctx, node.config.template_ref), SLOTS_FACTS, False)
        if node.config.generate is not None:
            generate = node.config.generate
            yield ("/config/generate/allowed_facts", _parsed(generate.allowed_facts), frozenset({"facts"}), True)
            yield ("/config/generate/fallback_template_ref", _template_paths(ctx, generate.fallback_template_ref),
                   SLOTS_FACTS, False)


def g0_10(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        for sub, paths, allowed, whole_ok in _read_sites(ctx, node):
            for path in paths:
                if path.ns not in allowed:
                    yield ctx.v("G0-10", node.id, f"{path.raw} no se puede leer aquí", sub)
                elif path.whole_fact and not whole_ok:
                    yield ctx.v("G0-10", node.id, f"{path.raw}: falta .value", sub)


def g0_11(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, DecideNode):
            model = ctx.model(node.config.model)
            if model is not None and node.config.branch_on not in model.calibrated_fields:
                yield ctx.v("G0-11", node.id, f"branch_on {node.config.branch_on!r} no está en calibrated_fields",
                            "/config/branch_on")


def g0_13(ctx: Ctx) -> Iterator[Violation]:
    confirms = {n.id for n in ctx.flow.nodes if isinstance(n, ConfirmNode)}
    for node in ctx.flow.nodes:
        if isinstance(node, RespondNode):
            for i, claim in enumerate(node.config.claims):
                if claim not in confirms:
                    yield ctx.v("G0-13", node.id, f"claims lista {claim!r}, que no es un confirm del flow",
                                f"/config/claims/{i}")


def g0_14(ctx: Ctx) -> Iterator[Violation]:
    modes: set[str] = set()
    for node in ctx.flow.nodes:
        if isinstance(node, EndNode):
            declarable = [m for m in DECLARABLE if is_declarable(node.config.outcome, m)]
            if not declarable:
                yield ctx.v("G0-14", node.id, f"el outcome {node.config.outcome.value} no es declarable: lo asigna el motor",
                            "/config/outcome")
            modes |= set(declarable)
    if len(modes) > 1:
        yield ctx.v("G0-14", None, "los end del flow mezclan outcomes de modo conversacional y de modo task")


def g0_15(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if isinstance(node, RespondNode) and node.config.generate is not None:
            prompt = ctx.prompt(node.config.generate.prompt_ref)
            if prompt is not None and ctx.reg.resolve(EntityKind.model_profile, prompt.model_profile) is None:
                yield ctx.v("G0-15", node.id, f"el prompt {prompt.id} referencia el model_profile "
                            f"{prompt.model_profile}, que no existe", "/config/generate/prompt_ref")


def g0_16(ctx: Ctx) -> Iterator[Violation]:
    if flow_mode(ctx.flow) != "task":
        return
    for node in ctx.flow.nodes:
        if is_waiting(node):
            yield ctx.v("G0-16", node.id, "un flow de modo task no tiene nodos que esperan al principal")
```

Mueve `from agent_core.domain import RefSpec` al bloque de imports de arriba.

- [ ] **Step 4: Registrar las reglas**

En `agent_core/flows/validate.py`:

```python
from agent_core.flows.rules.phase5 import g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16
...
FLOW_RULES: tuple[Rule, ...] = (
    g0_03, g0_04, g0_05, g0_06, g0_07, g0_08, g0_10, g0_11, g0_13, g0_14, g0_15, g0_16,
)
```

- [ ] **Step 5: Correr, lint y commit**

```bash
uv run pytest tests/m01 -v && uv run ruff check . && uv run mypy && uv run lint-imports && uv run agentcore validate tests/m01/fixtures/registry
git add agent_core/flows tests/m01/test_rules_phase5.py
git commit -m "feat(m1): reglas de fase 5 (G0-07, 08, 10, 11, 13, 14, 15, 16)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: todo en verde, incluido el registro de ejemplo (el fixture ya usa `facts.candidatas.value`).

---

### Task 13 (fase 5): chequeos por agente y propiedad de reclamos (T-M1-12, T-M1-23, T-M1-26)

**Files:**
- Create: `agent_core/flows/agent.py`
- Modify: `agent_core/flows/validate.py` (`validate_registry` con agentes y releases), `agent_core/flows/__init__.py`
- Test: `tests/m01/test_agent.py`, `tests/m01/test_property.py`

**Interfaces:**
- Consumes: `flow_ref_sites`, `agent_ref_sites`, `TEMPLATE_KINDS` y `pointer_str` (Task 3); `flow_mode` (Task 5); `AuthoringRegistry`, `ReleaseDecl` (Task 3).
- Produces:
  - `validate_flow_for_agent(flow, agent, reg) -> list[Violation]` (G0-12, AG-01);
  - `validate_agent(agent, reg) -> list[Violation]` (G0-02, G0-12);
  - `release_flows(reg, decl, agent) -> tuple[list[Flow], list[str]]` (flows resueltos, referencias sin resolver).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m01/test_agent.py`:

```python
import shutil
from pathlib import Path

import yaml

from agent_core.cli import main
from agent_core.flows.agent import validate_agent, validate_flow_for_agent
from agent_core.flows.registry import AuthoringRegistry
from tests.m01.cases import agent, base, flow, node, registry, task_base

FIXTURE = Path(__file__).parent / "fixtures" / "registry"


def _reg() -> AuthoringRegistry:
    return registry(flow(base()))  # agent().entry_flow = base@1


def test_agent_and_base_are_valid() -> None:
    assert validate_agent(agent(), _reg()) == []
    assert validate_flow_for_agent(flow(base()), agent(), _reg()) == []


# T-M1-23
def test_mode_mismatch_is_ag_01() -> None:
    found = validate_flow_for_agent(flow(base()), agent(mode="task"), registry())
    assert [(v.rule, v.flow) for v in found] == [("AG-01", "base@1.0.0")]
    assert validate_flow_for_agent(flow(task_base()), agent(mode="task"), registry()) == []


# T-M1-12
def test_missing_locale_in_flow_template() -> None:
    d = base()
    node(d, "pedir")["config"]["prompt_ref"] = "t/solo_es"
    found = validate_flow_for_agent(flow(d), agent(), registry())
    assert [(v.rule, v.node_id) for v in found] == [("G0-12", "pedir")]


def test_agent_engine_templates() -> None:
    templates = dict(agent().templates.model_dump(mode="json"))
    missing = validate_agent(agent(templates={**templates, "clarify": "t/noexiste"}), _reg())
    assert [v.rule for v in missing] == ["G0-02"]
    partial = validate_agent(agent(templates={**templates, "clarify": "t/solo_es"}), _reg())
    assert [v.rule for v in partial] == ["G0-12"]


def test_cli_runs_agent_checks(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    path = root / "agents" / "atencion@1.0.0.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["supported_locales"] = ["es", "pt", "en"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert main(["validate", str(root)]) == 1
```

(`yaml.safe_load` y `safe_dump` en la prueba solo editan el fixture: `budgets.max_cost_per_run` se vuelve `float` en el archivo reescrito y `load_yaml` lo vuelve a leer como `Decimal`.)

`tests/m01/test_property.py`:

```python
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.m01.cases import base, check, node, rules

EDGES = [("buscar", "ok"), ("confirmar", "no"), ("verificar", "verified"), ("ok_msg", "next")]


def _insert_chains(d: dict[str, Any], chains: list[tuple[tuple[str, str], int]]) -> None:
    counter = 0
    for (source, result), length in chains:
        origin = node(d, source)
        target = origin["next"][result]
        ids = [f"s{counter + i}" for i in range(length)]
        counter += length
        for i, sid in enumerate(ids):
            nxt = ids[i + 1] if i + 1 < len(ids) else target
            d["nodes"].append({"id": sid, "type": "respond", "config": {"template_ref": "t/seguro"},
                               "next": {"next": nxt}})
        origin["next"][result] = ids[0]


# T-M1-26
@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(EDGES), st.integers(1, 3)), max_size=4, unique_by=lambda t: t[0]))
def test_bypassing_verified_always_breaks_g0_05(chains: list[tuple[tuple[str, str], int]]) -> None:
    d = base()
    _insert_chains(d, chains)
    assert check(d) == []
    verify = node(d, "verificar")
    verify["next"]["failed"] = verify["next"]["verified"]
    assert "G0-05" in rules(check(d))
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m01/test_agent.py tests/m01/test_property.py -v`
Esperado: `test_agent.py` FAIL con `ModuleNotFoundError: No module named 'agent_core.flows.agent'`; `test_property.py` debería pasar (usa reglas que ya existen). Si falla, revisa G0-05 antes de seguir.

- [ ] **Step 3: Implementar `agent.py`**

```python
"""Chequeos por agente: G0-12 (locales), AG-01 (modo) y referencias del agente (M1 §3.8)."""

from agent_core.domain import Agent, EntityKind, Flow, Prompt, StartFlowAction, Template
from agent_core.flows.graph import flow_mode
from agent_core.flows.refs import TEMPLATE_KINDS, agent_ref_sites, flow_ref_sites, pointer_str
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, sort_violations


def _agent_label(agent: Agent) -> str:
    return f"{agent.id}@{agent.version}"


def _missing_locales(entity: object, agent: Agent) -> list[str]:
    if not isinstance(entity, Template | Prompt):
        return []
    return sorted(set(agent.supported_locales) - set(entity.locales))


def validate_flow_for_agent(flow: Flow, agent: Agent, reg: RegistryView) -> list[Violation]:
    label = f"{flow.id}@{flow.version}"
    found: list[Violation] = []
    mode = flow_mode(flow)
    if mode is not None and mode != agent.mode:
        found.append(Violation(rule="AG-01", flow=label,
                               message=f"el flow es de modo {mode} y el agente {_agent_label(agent)} es {agent.mode}"))
    for site in flow_ref_sites(flow):
        if site.kind not in TEMPLATE_KINDS:
            continue
        missing = _missing_locales(reg.resolve(site.kind, site.ref), agent)
        if missing:
            found.append(Violation(rule="G0-12", flow=label, node_id=site.node_id, path=pointer_str(site.pointer),
                                   message=f"{site.ref} no tiene los locales {', '.join(missing)} "
                                           f"del agente {_agent_label(agent)}"))
    return sort_violations(found)


def validate_agent(agent: Agent, reg: RegistryView) -> list[Violation]:
    found: list[Violation] = []
    where = f"agents/{_agent_label(agent)}"
    for site in agent_ref_sites(agent):
        path = f"{where}#{pointer_str(site.pointer)}"
        entity = reg.resolve(site.kind, site.ref)
        if entity is None:
            found.append(Violation(rule="G0-02", path=path,
                                   message=f"{site.kind.value} {site.ref} no existe en el registro"))
        elif site.kind == EntityKind.template and (missing := _missing_locales(entity, agent)):
            found.append(Violation(rule="G0-12", path=path,
                                   message=f"{site.ref} no tiene los locales {', '.join(missing)} "
                                           f"del agente {_agent_label(agent)}"))
    return sort_violations(found)


def release_flows(reg: AuthoringRegistry, decl: ReleaseDecl, agent: Agent) -> tuple[list[Flow], list[str]]:
    """Flows que usa el agente en la release: entry_flow ∪ release.flows ∪ interrupciones start_flow."""
    refs = [agent.entry_flow, *decl.flows,
            *(i.action.flow for i in decl.interrupts if isinstance(i.action, StartFlowAction))]
    flows: dict[tuple[str, str], Flow] = {}
    missing: list[str] = []
    for ref in refs:
        entity = reg.resolve(EntityKind.flow, ref)
        if isinstance(entity, Flow):
            flows.setdefault((entity.id, entity.version), entity)
        else:
            missing.append(str(ref))
    return [flows[key] for key in sorted(flows)], sorted(set(missing))
```

- [ ] **Step 4: Extender `validate_registry`**

Reemplaza `validate_registry` en `validate.py`:

```python
def validate_registry(reg: AuthoringRegistry) -> list[Violation]:
    """Flows, agentes y, por release, cada agente con sus flows (M1 §3.12)."""
    from agent_core.flows.agent import release_flows, validate_agent, validate_flow_for_agent

    found: list[Violation] = []
    for entity in reg.all(EntityKind.flow):
        if isinstance(entity, Flow):
            found += validate_flow(entity, reg)
    for entity in reg.all(EntityKind.agent):
        if isinstance(entity, Agent):
            found += validate_agent(entity, reg)
    for decl in reg.releases():
        where = f"releases/{decl.id}.yaml"
        for entry in decl.agents:
            agent = reg.resolve(EntityKind.agent, entry.agent)
            if not isinstance(agent, Agent):
                found.append(Violation(rule="G0-02", path=where, message=f"agent {entry.agent} no existe en el registro"))
                continue
            flows, missing = release_flows(reg, decl, agent)
            found += [Violation(rule="G0-02", path=where, message=f"flow {ref} no existe en el registro")
                      for ref in missing]
            for flow in flows:
                found += validate_flow_for_agent(flow, agent, reg)
    return sort_violations(found)
```

Mueve el import de `agent_core.flows.agent` arriba si no hay ciclo (`agent.py` no importa `validate.py`, así que no lo hay) y agrega `Agent` a los imports de `agent_core.domain`.

- [ ] **Step 5: Exportar en `__init__.py`**

Agrega a `agent_core/flows/__init__.py`:

```python
from agent_core.flows.agent import validate_agent, validate_flow_for_agent
```

y `"validate_agent", "validate_flow_for_agent"` a `__all__`. En `tests/m01/test_public_api.py`, agrega los mismos dos nombres a `PUBLIC`.

- [ ] **Step 6: Correr todo, lint y commit**

```bash
uv run pytest && uv run ruff check . && uv run mypy && uv run lint-imports && uv run agentcore validate tests/m01/fixtures/registry
git add agent_core/flows tests/m01
git commit -m "feat(m1): chequeos por agente (G0-12, AG-01), releases en validate_registry y propiedad de reclamos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: todo en verde.

---

### Task 14: Definición de terminado y cierre

**Files:**
- Modify: `docs/specs/motor/m01-validacion-estatica.md` (estado), `.superpowers/sdd/…/progress.md` si se usa el ledger

- [ ] **Step 1: Verificación completa desde cero**

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
uv run lint-imports
uv run agentcore contracts --check
uv run agentcore validate tests/m01/fixtures/registry
uv run agentcore validate tests/m01/fixtures/registry --json
```

Esperado: todo en verde; la validación imprime `sin violaciones` y `"ok": true`.

- [ ] **Step 2: Revisar la trazabilidad de pruebas**

```bash
grep -rhoE "T-M1-[0-9]{2}" tests/m01 | sort -u
```

Esperado: T-M1-01 a T-M1-45 aparecen como comentario sobre su prueba. T-M1-01…14 se cubren así:
- 01, 07 y 09 en `test_schema.py` y `test_structure.py`;
- 02, 03 y 04 en `test_structure.py`;
- 05 en `test_writes.py`;
- 06 en `test_safe_exits.py`;
- 08, 10, 11, 13 y 14 en `test_rules_phase5.py`;
- 12 en `test_agent.py`.

Si falta un ID, agrega el comentario en la prueba que lo cubre o escribe la prueba que falta.

- [ ] **Step 3: Marcar el estado del spec**

En `docs/specs/motor/m01-validacion-estatica.md`, cambia la línea de estado a:

```markdown
- Estado: **rev. 2 implementada** (fases 1 y 5) · Fase 1 (carga, G0-01 a G0-06, `derive_claims`, CLI) y fase 5 (G0-07 a G0-16, chequeos por agente)
```

- [ ] **Step 4: Definición de terminado, punto por punto**

En el reporte final, marca cada punto de §10 del spec y de la "Definición de terminado común" del índice §8:

- interfaz pública exportada y con tipos;
- todas las pruebas `T-M1-*` en verde;
- `import-linter` en verde;
- sin TODO sin issue;
- M1 no emite eventos.

Lista también las decisiones que tomaste y que el spec no cubría.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/motor/m01-validacion-estatica.md
git commit -m "docs(m1): rev. 2 implementada

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
