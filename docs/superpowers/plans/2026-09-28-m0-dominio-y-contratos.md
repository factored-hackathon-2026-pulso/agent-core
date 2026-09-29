# M0 — Dominio y contratos: plan de implementación (incluye setup del repo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar `agent-core` como repo Python funcional (uv, pytest, ruff, mypy, import-linter, CI) y construir M0: tipos, esquemas de nodos, eventos, errores, serialización canónica, puertos, adaptadores de sistema, los 9 dobles de la fase 1 con sus suites de contrato y `contracts/` generado.

**Architecture:** `agent_core.domain` contiene solo datos (modelos Pydantic v2 inmutables) y funciones puras (`loads`/`dumps`/`canonical_bytes`, parseo de referencias, `is_declarable`). `agent_core.ports` define `typing.Protocol` síncronos. Los únicos que leen hora o aleatoriedad son `agent_core/adapters/system_clock.py` y `system_ids.py`, y `ruff` lo hace cumplir. `testing/fakes/` trae dobles en memoria que pasan las mismas suites de contrato (`tests/contracts/`) que después correrán los adaptadores reales.

**Tech Stack:** Python 3.12, Pydantic v2, `rfc8785` (JCS), uv, pytest, ruff, mypy (strict, plugin de Pydantic), import-linter, GitHub Actions, docker-compose (Postgres 16, todavía sin uso en M0).

**Spec:** `docs/specs/motor/m00-dominio-y-contratos.md` (rev. 4). Contexto: `docs/specs/motor/00-indice.md` (§4 puertos, §5 dueños del estado, §6 eventos), ADR 0001, 0002, 0006, 0007, 0008. Léelos antes de empezar: el spec manda sobre este plan.

## Global Constraints

- Python `>=3.12,<3.13` (ADR 0001). Pydantic v2. Sin colas, Redis ni vector DB.
- Todos los modelos de M0: `extra="forbid"`; `frozen=True` salvo `RunState`.
- Todo `datetime` es UTC con zona; un `datetime` sin zona se rechaza (`AwareDatetime`).
- Cifras en `Decimal`, nunca `float`. Única excepción: probabilidades en `[0, 1]`, nunca NaN.
- Nunca `datetime.now()`, `datetime.utcnow()`, `date.today()`, `time.time()`, `time.time_ns()`, `time.monotonic()`, `time.monotonic_ns()`, `time.perf_counter()`, `uuid.uuid1()`, `uuid.uuid4()`, `random` ni `secrets` fuera de `agent_core/adapters/system_clock.py` y `agent_core/adapters/system_ids.py`.
- `agent_core.domain` y `agent_core.ports` no importan ningún otro paquete de `agent_core` (`.importlinter`).
- Ningún tipo de M0 nombra conceptos de negocio (cliente bancario, tarjeta, disputa). Los fixtures pueden usar nombres del ejemplo `disputa-cargo` con **datos sintéticos**.
- Nunca copiar datos reales del dataset ni las credenciales AWS del diccionario de datos.
- `canonical_bytes` es la **única** canonización del repo (JCS, RFC 8785).
- `contracts/` es generado: nunca se edita a mano.
- Comandos: `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore contracts [--check]`.
- Commits: mensajes en español, terminan con `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `pyproject.toml`, `uv.lock`, `.python-version` | proyecto, dependencias, config de pytest, ruff, mypy |
| `.importlinter` | fronteras (se agregan `adapters`, `cli`, `contracts`) |
| `.gitattributes`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml` | setup del repo |
| `agent_core/{flows,interpreter,…,knowledge}/__init__.py` | paquetes vacíos de M1–M12 (para que import-linter los encuentre) |
| `agent_core/domain/base.py` | `Model`, `MutableModel`, tipos anotados comunes (`UtcDatetime`, `Locale`, `EntityId`, `ExactVersion`, `Probability`, `NodeId`) |
| `agent_core/domain/errors.py` | `DomainError` y subclases, `ProblemCode`, `PROBLEM_STATUS`, `EngineError` |
| `agent_core/domain/json.py` | `JsonValue`, `to_jsonable`, `loads`, `dumps`, `canonical_bytes`, `sha256_hex` |
| `agent_core/domain/refs.py` | `EntityKind`, `EntityRef`, `RefSpec`, `AgentSelector`, `iter_refspecs`, `require_exact_refs` |
| `agent_core/domain/identity.py` | `PrincipalType`, `AuthLevel`, `AuthInfo`, `PrincipalKey`, `Principal`, `SubjectRef`, `OnBehalfOf` |
| `agent_core/domain/outcomes.py` | `Outcome`, `DECLARABLE`, `is_declarable`, `ReasonCode`, `ReasonCodeStr`, `Awaiting`, `Command`, `Mode` |
| `agent_core/domain/nodes.py` | configs y modelos de nodo, `node_kind`, `Node`, `RESULTS`, `TERMINAL`, `WAITING`, `MVP_NODE_KINDS` |
| `agent_core/domain/entities.py` | entidades del registro (`Agent`, `Flow`, `Release`, `ToolDef`, `DecisionModelDef`…) |
| `agent_core/domain/shared.py` | tipos compartidos entre módulos (`EscalationRequest`, `RejectedDraft`, `Fingerprint`, `ToolStatus`…) |
| `agent_core/domain/state.py` | `RunState` y sus partes, con validadores de coherencia |
| `agent_core/domain/turn.py` | entrada y salida de turnos y runs |
| `agent_core/domain/events.py` | `EngineEvent`, payloads, `AnyEvent`, `EVENT_TYPES`, `EVENT_EMITTERS`, `MEASURED_FIELDS` |
| `agent_core/domain/version.py` | `SCHEMA_VERSION` |
| `agent_core/domain/__init__.py` | reexporta la interfaz pública de `domain` |
| `agent_core/ports/*.py` | un archivo por puerto (Protocols + sus tipos de datos) |
| `agent_core/adapters/system_clock.py`, `system_ids.py`, `env_keys.py` | `SystemClock`, `SystemIds`, `EnvKeyProvider` |
| `agent_core/contracts.py`, `agent_core/cli.py` | generación y chequeo de `contracts/`; CLI `agentcore` |
| `testing/builders.py` | constructores de datos sintéticos (`principal`, `run_state`…) para pruebas de todos los módulos |
| `testing/fakes/*.py` | `FakeClock`, `FakeIds`, `FakeKeyProvider`, `InMemoryRegistry`, `FakeToolExecutor`, `InMemoryStore`/`InMemoryUoW`/`InMemoryAuditSink`/`InMemoryOutbox` |
| `tests/m00/*.py` | T-M0-01…15 |
| `tests/contracts/*.py` | T-M0-C-* |
| `contracts/` | JSON Schemas generados + `VERSION` |

---

### Task 0: Setup del repo

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitattributes`, `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`
- Create: `agent_core/__init__.py`, `agent_core/domain/__init__.py`, `agent_core/ports/__init__.py`, `agent_core/adapters/__init__.py`, `agent_core/{flows,interpreter,actions,turn,decision,guards,views,response,api,handoff,audit,knowledge}/__init__.py`
- Create: `agent_core/cli.py`, `agent_core/contracts.py` (stubs)
- Create: `testing/__init__.py`, `testing/fakes/__init__.py`, `tests/__init__.py`, `tests/m00/__init__.py`, `tests/contracts/__init__.py`, `tests/m00/test_smoke.py`
- Modify: `.importlinter` (contrato `m0` y contrato nuevo `adapters`)

**Interfaces:**
- Produces: paquete `agent_core` importable; comandos `uv run pytest|ruff|mypy|lint-imports` funcionando; repo git en `main`.

- [ ] **Step 1: Inicializar git y commitear el diseño existente**

Desde `C:\Users\USUARIO\Documents\factored\agent-core` (en Git Bash):

```bash
git init -b main
printf '* text=auto eol=lf\n*.png binary\n' > .gitattributes
git add .
git commit -m "docs: diseño del motor (spec general rev. 14, M0 rev. 4) y ADRs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Esperado: un commit con `docs/`, `.claude/`, `CLAUDE.md`, `.gitignore`, `.importlinter`, `.gitattributes`.

- [ ] **Step 2: Fijar Python 3.12**

```bash
uv python install 3.12
uv python pin 3.12
```

Esperado: `.python-version` con `3.12`.

- [ ] **Step 3: Crear `pyproject.toml`**

```toml
[project]
name = "agent-core"
version = "0.1.0"
description = "Núcleo de agentes: motor de decisión que ejecuta agentes descritos como datos versionados."
requires-python = ">=3.12,<3.13"
dependencies = [
    "pydantic>=2.9,<3",
    "rfc8785>=0.1.4",
]

[project.scripts]
agentcore = "agent_core.cli:main"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "hypothesis>=6.100",
    "mypy>=1.13",
    "ruff>=0.7",
    "import-linter>=2.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["agent_core"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q --strict-markers"

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
files = ["agent_core", "testing"]

[[tool.mypy.overrides]]
module = ["rfc8785"]
ignore_missing_imports = true

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "TID", "DTZ", "RUF"]
ignore = ["RUF001", "RUF002", "RUF003"]  # textos en español y delimitadores ⟦⟧

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"datetime.datetime.now".msg = "Usa el Clock inyectado (M0 §2.9)."
"datetime.datetime.utcnow".msg = "Usa el Clock inyectado (M0 §2.9)."
"datetime.date.today".msg = "Usa el Clock inyectado (M0 §2.9)."
"time.time".msg = "Usa el Clock inyectado (M0 §2.9)."
"time.time_ns".msg = "Usa el Clock inyectado (M0 §2.9)."
"time.monotonic".msg = "Usa Clock.monotonic_ns() (M0 §2.9)."
"time.monotonic_ns".msg = "Usa Clock.monotonic_ns() (M0 §2.9)."
"time.perf_counter".msg = "Usa Clock.monotonic_ns() (M0 §2.9)."
"uuid.uuid1".msg = "Usa el IdSource inyectado (M0 §2.9)."
"uuid.uuid4".msg = "Usa el IdSource inyectado (M0 §2.9)."
"random".msg = "Usa el IdSource inyectado (M0 §2.9)."
"secrets".msg = "Usa IdSource.secret_token() (M0 §2.9)."

[tool.ruff.lint.per-file-ignores]
"agent_core/adapters/system_clock.py" = ["TID251"]
"agent_core/adapters/system_ids.py" = ["TID251"]
```

- [ ] **Step 4: Crear los paquetes**

`agent_core/__init__.py`:

```python
"""agent-core: motor de decisión (ver docs/specs/motor/00-indice.md)."""
```

`agent_core/domain/__init__.py`:

```python
"""M0 — dominio: tipos, esquemas de nodos, eventos, errores y serialización canónica."""
```

`agent_core/ports/__init__.py`:

```python
"""M0 — puertos hacia las unidades 2–7 (typing.Protocol)."""
```

`agent_core/adapters/__init__.py`:

```python
"""Adaptadores concretos: sistema (hora, IDs), entorno (claves de demo) y, más adelante, reales."""
```

Para cada paquete de M1–M12, un `__init__.py` con una línea. Los nombres salen del índice §2:

| Archivo | Contenido |
|---|---|
| `agent_core/flows/__init__.py` | `"""M1 — esquema de flows y validación estática (pendiente)."""` |
| `agent_core/interpreter/__init__.py` | `"""M2 — intérprete de nodos (pendiente)."""` |
| `agent_core/actions/__init__.py` | `"""M3 — protocolo de escritura (pendiente)."""` |
| `agent_core/turn/__init__.py` | `"""M4 — ciclo del turno (pendiente)."""` |
| `agent_core/decision/__init__.py` | `"""M5 — DecisionModel y Understand (pendiente)."""` |
| `agent_core/guards/__init__.py` | `"""M6 — guardas de entrada (pendiente)."""` |
| `agent_core/views/__init__.py` | `"""M7 — vistas y tokenización (pendiente)."""` |
| `agent_core/response/__init__.py` | `"""M8 — validador de respuesta (pendiente)."""` |
| `agent_core/api/__init__.py` | `"""M9 — acceso y API (pendiente)."""` |
| `agent_core/handoff/__init__.py` | `"""M10 — escalamiento y handoff (pendiente)."""` |
| `agent_core/audit/__init__.py` | `"""M11 — auditoría, transcript y replay (pendiente)."""` |
| `agent_core/knowledge/__init__.py` | `"""M12 — conocimiento (pendiente, tema #10)."""` |

`testing/__init__.py`:

```python
"""Utilidades de prueba compartidas por todos los módulos (datos sintéticos)."""
```

`testing/fakes/__init__.py`:

```python
"""Dobles en memoria de los puertos de M0 (índice §4)."""
```

`agent_core/cli.py` y `agent_core/contracts.py` (se completan en la Task 12; existen ya para que import-linter los encuentre):

```python
"""CLI `agentcore` (se completa en la Task 12)."""
```

```python
"""Generación de `contracts/` (se completa en la Task 12)."""
```

`tests/__init__.py`, `tests/m00/__init__.py` y `tests/contracts/__init__.py`: archivos vacíos.

- [ ] **Step 5: Escribir la prueba de humo**

`tests/m00/test_smoke.py`:

```python
import agent_core
import agent_core.domain
import agent_core.ports


def test_packages_import() -> None:
    assert agent_core.__doc__
    assert agent_core.domain.__doc__
    assert agent_core.ports.__doc__
```

- [ ] **Step 6: Actualizar `.importlinter`**

En `[importlinter:contract:m0]`, agregar al final de `forbidden_modules`:

```ini
    agent_core.adapters
    agent_core.cli
    agent_core.contracts
```

Agregar al final del archivo:

```ini
[importlinter:contract:adapters]
name = adapters solo usa domain, ports
type = forbidden
source_modules =
    agent_core.adapters
forbidden_modules =
    agent_core.flows
    agent_core.views
    agent_core.guards
    agent_core.actions
    agent_core.decision
    agent_core.response
    agent_core.interpreter
    agent_core.handoff
    agent_core.audit
    agent_core.knowledge
    agent_core.turn
    agent_core.api
    agent_core.cli
    agent_core.contracts
```

- [ ] **Step 7: Archivos de entorno y CI**

`.env.example`:

```dotenv
# Claves de DEMO (EnvKeyProvider). Nunca claves reales. Formato: kid:base64(>=32 bytes), la primera es la vigente.
AGENTCORE_KEYS_FINGERPRINT=demo-fp-1:REPLACE_WITH_BASE64_32_BYTES
AGENTCORE_KEYS_TOKEN_MAP=demo-tm-1:REPLACE_WITH_BASE64_32_BYTES
```

`docker-compose.yml` (Postgres para M3, M4, M9 y M11; M0 no lo usa):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: agentcore
      POSTGRES_PASSWORD: agentcore-dev-only   # credencial de desarrollo local, no es secreta
      POSTGRES_DB: agentcore
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentcore"]
      interval: 5s
      timeout: 3s
      retries: 10
```

`.github/workflows/ci.yml`:

```yaml
name: ci
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run mypy
      - run: uv run lint-imports
      - run: uv run pytest
```

(El paso `agentcore contracts --check` se agrega en la Task 12, cuando exista el comando.)

- [ ] **Step 8: Instalar y verificar**

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
uv run lint-imports
```

Esperado: `1 passed`; ruff sin errores; mypy `Success`; import-linter `Contracts: 14 kept, 0 broken.`

- [ ] **Step 9: Commit**

```bash
git add .
git commit -m "build: setup del repo (uv, pytest, ruff, mypy, import-linter, CI, compose)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Errores y serialización canónica

**Files:**
- Create: `agent_core/domain/errors.py`, `agent_core/domain/json.py`
- Test: `tests/m00/test_errors.py`, `tests/m00/test_json.py`

**Interfaces:**
- Produces: `DomainError`, `InvalidRuntimeRef`, `SchemaError`, `IllegalTransition`, `VersionConflict`, `TurnInProgress`, `CredentialsInvalid`, `ProblemCode`, `PROBLEM_STATUS: Mapping[ProblemCode, int]`, `EngineError(code, detail)` con `.status`; `type JsonValue`, `to_jsonable(value: object) -> Any`, `loads(raw: str | bytes) -> JsonValue`, `dumps(value: object) -> str`, `canonical_bytes(value: object) -> bytes`, `sha256_hex(data: bytes) -> str`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_errors.py`:

```python
from agent_core.domain.errors import PROBLEM_STATUS, DomainError, EngineError, InvalidRuntimeRef, ProblemCode


def test_every_problem_code_has_status() -> None:
    assert set(PROBLEM_STATUS) == set(ProblemCode)


def test_status_by_family() -> None:
    assert PROBLEM_STATUS[ProblemCode.credentials_invalid] == 401
    assert PROBLEM_STATUS[ProblemCode.delegation_mismatch] == 403
    assert PROBLEM_STATUS[ProblemCode.agent_forbidden] == 403
    assert PROBLEM_STATUS[ProblemCode.not_found] == 404
    assert PROBLEM_STATUS[ProblemCode.idempotency_conflict] == 409
    assert PROBLEM_STATUS[ProblemCode.run_closed] == 410
    assert PROBLEM_STATUS[ProblemCode.invalid_request] == 422
    assert PROBLEM_STATUS[ProblemCode.cost_budget_exceeded] == 429
    assert PROBLEM_STATUS[ProblemCode.internal_error] == 500


def test_engine_error_carries_code_and_status() -> None:
    err = EngineError(ProblemCode.run_closed, "el run fue escalado")
    assert err.code is ProblemCode.run_closed
    assert err.status == 410
    assert err.detail == "el run fue escalado"


def test_domain_errors_are_not_engine_errors() -> None:
    assert issubclass(InvalidRuntimeRef, DomainError)
    assert not issubclass(InvalidRuntimeRef, EngineError)
```

`tests/m00/test_json.py`:

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

import pytest

from agent_core.domain.json import canonical_bytes, dumps, loads, sha256_hex, to_jsonable


class Color(StrEnum):
    red = "red"


# T-M0-07
def test_decimal_keeps_scale_as_string_in_jcs() -> None:
    assert canonical_bytes({"monto": Decimal("500.00")}) == b'{"monto":"500.00"}'


def test_jcs_sorts_keys() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1}) == b'{"a":2,"b":1}'


def test_jcs_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        canonical_bytes({"p": float("nan")})
    with pytest.raises(ValueError):
        canonical_bytes({"p": float("inf")})
    with pytest.raises(ValueError):
        canonical_bytes({"m": Decimal("NaN")})


def test_jcs_big_int_as_string() -> None:
    assert canonical_bytes({"n": 2**53 - 1}) == b'{"n":9007199254740991}'
    assert canonical_bytes({"n": 2**53}) == b'{"n":"9007199254740992"}'


def test_jcs_probability_float() -> None:
    assert canonical_bytes({"p": 0.5}) == b'{"p":0.5}'


def test_jcs_datetime_enum_timedelta_set() -> None:
    value = {
        "ts": datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
        "c": Color.red,
        "ttl": timedelta(minutes=30),
        "s": frozenset({"b", "a"}),
    }
    assert canonical_bytes(value) == b'{"c":"red","s":["a","b"],"ts":"2026-09-28T12:00:00Z","ttl":"PT30M"}'


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        to_jsonable(datetime(2026, 9, 28, 12, 0))  # noqa: DTZ001


def test_loads_reads_decimals() -> None:
    value = loads('{"monto": 1.10, "n": 3}')
    assert value == {"monto": Decimal("1.10"), "n": 3}
    assert isinstance(value, dict)
    assert isinstance(value["monto"], Decimal)


def test_loads_rejects_nan() -> None:
    with pytest.raises(ValueError):
        loads('{"x": NaN}')


def test_dumps_round_trip_is_exact() -> None:
    raw = '{"a":500.00,"b":[1,"x",null,true]}'
    assert dumps(loads(raw)) == raw


def test_sha256_hex() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_errors.py tests/m00/test_json.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.domain.errors'`.

- [ ] **Step 3: Implementar `errors.py`**

`agent_core/domain/errors.py`:

```python
"""Errores del motor (M0 §2.11).

`DomainError` son errores internos (no HTTP). `EngineError` lleva un `ProblemCode` estable que M9 traduce a
`application/problem+json`.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class DomainError(Exception):
    """Error interno del motor; no es un error HTTP."""


class InvalidRuntimeRef(DomainError):
    """Una referencia no exacta (rango o sin versión) llegó a runtime."""


class SchemaError(DomainError):
    """Un dato del registro no valida contra su modelo."""


class IllegalTransition(DomainError):
    """Transición no permitida en una máquina de estados (bug, no error de usuario)."""


class VersionConflict(DomainError):
    """`save_run` con una `state_version` que no es la vigente."""


class TurnInProgress(DomainError):
    """`acquire_turn` encontró un lease vigente de otro turno."""


class CredentialsInvalid(DomainError):
    """`IdentityVerifier`: la firma de la credencial no valida."""


class ProblemCode(StrEnum):
    credentials_invalid = "credentials_invalid"
    principal_expired = "principal_expired"
    subject_forbidden = "subject_forbidden"
    agent_forbidden = "agent_forbidden"
    version_pin_forbidden = "version_pin_forbidden"
    delegation_expired = "delegation_expired"
    delegation_mismatch = "delegation_mismatch"
    principal_mismatch = "principal_mismatch"
    not_found = "not_found"
    turn_in_progress = "turn_in_progress"
    handoff_already_resolved = "handoff_already_resolved"
    idempotency_conflict = "idempotency_conflict"
    run_closed = "run_closed"
    invalid_request = "invalid_request"
    rate_limited = "rate_limited"
    cost_budget_exceeded = "cost_budget_exceeded"
    internal_error = "internal_error"


PROBLEM_STATUS: Mapping[ProblemCode, int] = MappingProxyType(
    {
        ProblemCode.credentials_invalid: 401,
        ProblemCode.principal_expired: 401,
        ProblemCode.subject_forbidden: 403,
        ProblemCode.agent_forbidden: 403,
        ProblemCode.version_pin_forbidden: 403,
        ProblemCode.delegation_expired: 403,
        ProblemCode.delegation_mismatch: 403,
        ProblemCode.principal_mismatch: 403,
        ProblemCode.not_found: 404,
        ProblemCode.turn_in_progress: 409,
        ProblemCode.handoff_already_resolved: 409,
        ProblemCode.idempotency_conflict: 409,
        ProblemCode.run_closed: 410,
        ProblemCode.invalid_request: 422,
        ProblemCode.rate_limited: 429,
        ProblemCode.cost_budget_exceeded: 429,
        ProblemCode.internal_error: 500,
    }
)


class EngineError(Exception):
    """Error con código HTTP estable. Solo lo lanzan los módulos; M9 lo traduce."""

    def __init__(self, code: ProblemCode, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def status(self) -> int:
        return PROBLEM_STATUS[self.code]
```

- [ ] **Step 4: Implementar `json.py`**

`agent_core/domain/json.py`:

```python
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

type JsonValue = None | bool | int | Decimal | str | list[JsonValue] | dict[str, JsonValue]

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
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m00/test_errors.py tests/m00/test_json.py -v`
Esperado: todas PASS.

Si `test_jcs_datetime_enum_timedelta_set` falla por el formato de `timedelta`, imprime `TypeAdapter(timedelta).dump_python(timedelta(minutes=30), mode="json")`. Ajusta **la prueba** al formato ISO 8601 que devuelve Pydantic (es el mismo que Pydantic acepta al leer) y deja una nota en el commit.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/errors.py agent_core/domain/json.py tests/m00/test_errors.py tests/m00/test_json.py
git commit -m "feat(m0): errores del dominio y serialización canónica (JCS, Decimal)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Tipos base y referencias

**Files:**
- Create: `agent_core/domain/base.py`, `agent_core/domain/refs.py`
- Test: `tests/m00/test_base.py`, `tests/m00/test_refs.py`

**Interfaces:**
- Consumes: `InvalidRuntimeRef` (Task 1).
- Produces:
  - `Model` (frozen, `extra="forbid"`, `populate_by_name=True`) y `MutableModel`;
  - tipos anotados `UtcDatetime`, `Locale`, `EntityId`, `ExactVersion`, `Probability`, `NodeId`;
  - `EntityKind`;
  - `EntityRef(id, version)` con `.parse(text)` y `str()` → `"id@X.Y.Z"`;
  - `RefSpec(id, spec)` con `.parse(text)`, `.is_exact` y `.require_exact() -> EntityRef`;
  - `AgentSelector(id, alias, version)` con `.parse(text)`;
  - `iter_refspecs(value) -> Iterator[RefSpec]`;
  - `require_exact_refs(value) -> None`.

  `EntityRef`, `RefSpec` y `AgentSelector` también aceptan un string al validar.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_base.py`:

```python
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_core.domain.base import Model, Probability, UtcDatetime


class Stamped(Model):
    at: UtcDatetime
    p: Probability | None = None


# T-M0-10 (parte base; el resto en test_state.py)
def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        Stamped(at=datetime(2026, 9, 28, 12, 0))  # noqa: DTZ001


def test_aware_datetime_normalized_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    stamped = Stamped(at=datetime(2026, 9, 28, 7, 0, tzinfo=bogota))
    assert stamped.at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert stamped.at.utcoffset() == timedelta(0)


def test_models_are_frozen_and_forbid_extra() -> None:
    stamped = Stamped(at=datetime(2026, 9, 28, 12, 0, tzinfo=UTC))
    with pytest.raises(ValidationError):
        stamped.at = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Stamped.model_validate({"at": "2026-09-28T12:00:00Z", "extra": 1})


def test_probability_bounds() -> None:
    at = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Stamped(at=at, p=1.5)
    with pytest.raises(ValidationError):
        Stamped(at=at, p=float("nan"))
```

`tests/m00/test_refs.py`:

```python
import pytest
from pydantic import BaseModel, ValidationError

from agent_core.domain.base import Model
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.refs import AgentSelector, EntityRef, RefSpec, iter_refspecs, require_exact_refs


# T-M0-01
@pytest.mark.parametrize("text", ["tool@^1", "tool@1", "tool", "tool@~1.2", "tool@1.2"])
def test_entity_ref_parse_rejects_non_exact(text: str) -> None:
    with pytest.raises(InvalidRuntimeRef):
        EntityRef.parse(text)


def test_entity_ref_parse_exact() -> None:
    ref = EntityRef.parse("tool@1.2.0")
    assert (ref.id, ref.version) == ("tool", "1.2.0")
    assert str(ref) == "tool@1.2.0"


def test_refspec_require_exact() -> None:
    with pytest.raises(InvalidRuntimeRef):
        RefSpec.parse("tool@^1").require_exact()
    with pytest.raises(InvalidRuntimeRef):
        RefSpec.parse("t/pedir_cargo").require_exact()
    assert RefSpec.parse("tool@1.2.0").require_exact() == EntityRef(id="tool", version="1.2.0")


def test_entity_ref_field_accepts_string_but_not_ranges() -> None:
    class Holder(Model):
        ref: EntityRef

    assert Holder.model_validate({"ref": "flow-a@2.0.1"}).ref == EntityRef(id="flow-a", version="2.0.1")
    with pytest.raises(ValidationError):
        Holder.model_validate({"ref": "flow-a@^2"})


# T-M0-11
@pytest.mark.parametrize(
    ("text", "ident", "spec", "exact"),
    [
        ("tool", "tool", None, False),
        ("tool@^1", "tool", "^1", False),
        ("tool@~1.2", "tool", "~1.2", False),
        ("tool@1", "tool", "1", False),
        ("tool@1.2.0", "tool", "1.2.0", True),
        ("t/pedir_cargo", "t/pedir_cargo", None, False),
        ("buscar_transacciones@1", "buscar_transacciones", "1", False),
    ],
)
def test_refspec_parse_accepts(text: str, ident: str, spec: str | None, exact: bool) -> None:
    ref = RefSpec.parse(text)
    assert (ref.id, ref.spec, ref.is_exact) == (ident, spec, exact)
    assert str(ref) == text


@pytest.mark.parametrize("text", ["Tool@1", "mi tool", "tool@", "tool@latest", "@1.0.0", "-tool"])
def test_refspec_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        RefSpec.parse(text)


def test_agent_selector() -> None:
    assert AgentSelector.parse("atencion") == AgentSelector(id="atencion", alias="prod", version=None)
    assert AgentSelector.parse("atencion@canary") == AgentSelector(id="atencion", alias="canary", version=None)
    assert AgentSelector.parse("atencion@1.2.0") == AgentSelector(id="atencion", alias=None, version="1.2.0")
    with pytest.raises(ValueError):
        AgentSelector.parse("atencion@^1")


def test_require_exact_refs_walks_nested_models() -> None:
    class Inner(Model):
        refs: list[RefSpec]

    class Outer(Model):
        inner: Inner
        by_name: dict[str, RefSpec]

    ok = Outer.model_validate({"inner": {"refs": ["a@1.0.0"]}, "by_name": {"x": "b@2.0.0"}})
    require_exact_refs(ok)
    assert [str(r) for r in iter_refspecs(ok)] == ["a@1.0.0", "b@2.0.0"]

    ranged = Outer.model_validate({"inner": {"refs": ["a@1.0.0"]}, "by_name": {"x": "b@^2"}})
    with pytest.raises(InvalidRuntimeRef):
        require_exact_refs(ranged)
    assert isinstance(ranged, BaseModel)
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_base.py tests/m00/test_refs.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `base.py`**

`agent_core/domain/base.py`:

```python
"""Bases y tipos anotados comunes de M0 (convenciones transversales de §2)."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


class Model(BaseModel):
    """Tipo de valor de M0: inmutable y sin campos extra."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class MutableModel(BaseModel):
    """Solo para `RunState`: se actualiza con `model_copy(update=...)` (índice §5)."""

    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Locale = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_/-]*$")]
ExactVersion = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_]*$")]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
```

- [ ] **Step 4: Implementar `refs.py`**

`agent_core/domain/refs.py`:

```python
"""Referencias a entidades (M0 §2.2).

`RefSpec` es de autoría (admite rangos o ninguna versión). `EntityRef` es de runtime (siempre exacta). El motor
nunca resuelve rangos: el registro exige referencias exactas al cargar una release (`require_exact_refs`).
"""

import re
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from agent_core.domain.base import EntityId, ExactVersion, Model
from agent_core.domain.errors import InvalidRuntimeRef

_ID = r"[a-z0-9][a-z0-9_/-]*"
_EXACT = r"\d+\.\d+\.\d+"
_SPEC = rf"(?:{_EXACT}|\^\d+(?:\.\d+){{0,2}}|~\d+(?:\.\d+){{0,2}}|\d+(?:\.\d+)?)"
_ALIAS = r"[a-z][a-z0-9_-]*"
_REF_RE = re.compile(rf"^(?P<id>{_ID})(?:@(?P<spec>[^@]+))?$")
_SPEC_RE = re.compile(rf"^{_SPEC}$")
_EXACT_RE = re.compile(rf"^{_EXACT}$")
_ALIAS_RE = re.compile(rf"^{_ALIAS}$")


class EntityKind(StrEnum):
    agent = "agent"
    flow = "flow"
    decision_model = "decision_model"
    policy = "policy"
    template = "template"
    prompt = "prompt"
    tool = "tool"
    language_detection = "language_detection"
    injection_ruleset = "injection_ruleset"


def _split(text: str) -> tuple[str, str | None]:
    match = _REF_RE.match(text)
    if match is None:
        raise ValueError(f"referencia inválida: {text!r}")
    return match["id"], match["spec"]


class EntityRef(Model):
    """Referencia exacta de runtime: `id@X.Y.Z`."""

    id: EntityId
    version: ExactVersion

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, _, version = data.partition("@")
            return {"id": ident, "version": version}
        return data

    @classmethod
    def parse(cls, text: str) -> "EntityRef":
        """Lanza `InvalidRuntimeRef` si la referencia no es exacta."""
        match = _REF_RE.match(text)
        spec = match["spec"] if match else None
        if match is None or spec is None or not _EXACT_RE.match(spec):
            raise InvalidRuntimeRef(f"referencia no exacta en runtime: {text!r}")
        return cls(id=match["id"], version=spec)

    def __str__(self) -> str:
        return f"{self.id}@{self.version}"


class RefSpec(Model):
    """Referencia de autoría: exacta, rango (`^1`, `~1.2`, `1`) o sin versión."""

    id: EntityId
    spec: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, spec = _split(data)
            return {"id": ident, "spec": spec}
        return data

    @field_validator("spec")
    @classmethod
    def _check_spec(cls, value: str | None) -> str | None:
        if value is not None and not _SPEC_RE.match(value):
            raise ValueError(f"versión o rango inválido: {value!r}")
        return value

    @classmethod
    def parse(cls, text: str) -> "RefSpec":
        return cls.model_validate(text)

    @property
    def is_exact(self) -> bool:
        return self.spec is not None and _EXACT_RE.match(self.spec) is not None

    def require_exact(self) -> EntityRef:
        if not self.is_exact or self.spec is None:
            raise InvalidRuntimeRef(f"referencia no exacta en runtime: {self}")
        return EntityRef(id=self.id, version=self.spec)

    def __str__(self) -> str:
        return self.id if self.spec is None else f"{self.id}@{self.spec}"


class AgentSelector(Model):
    """Selector de agente del request: `id`, `id@alias` o `id@X.Y.Z`. Sin nada, alias `prod`."""

    id: EntityId
    alias: str | None = None
    version: ExactVersion | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, data: Any) -> Any:
        if isinstance(data, str):
            ident, _, tail = data.partition("@")
            if not tail:
                return {"id": ident, "alias": "prod"}
            if _EXACT_RE.match(tail):
                return {"id": ident, "version": tail}
            if _ALIAS_RE.match(tail):
                return {"id": ident, "alias": tail}
            raise ValueError(f"selector de agente inválido: {data!r}")
        if isinstance(data, dict) and data.get("alias") is None and data.get("version") is None:
            return {**data, "alias": "prod"}
        return data

    @model_validator(mode="after")
    def _one_of(self) -> "AgentSelector":
        if (self.alias is None) == (self.version is None):
            raise ValueError("indica alias o versión, no ambos")
        return self

    @classmethod
    def parse(cls, text: str) -> "AgentSelector":
        return cls.model_validate(text)


def iter_refspecs(value: object) -> Iterator[RefSpec]:
    """Recorre modelos, mapas y secuencias y devuelve cada `RefSpec` en orden de campos."""
    if isinstance(value, RefSpec):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from iter_refspecs(getattr(value, name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from iter_refspecs(item)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            yield from iter_refspecs(item)


def require_exact_refs(value: object) -> None:
    """Lanza `InvalidRuntimeRef` ante la primera referencia no exacta (M0 §2.2, §13.11)."""
    for ref in iter_refspecs(value):
        ref.require_exact()
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m00/test_base.py tests/m00/test_refs.py -v`
Esperado: todas PASS.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/base.py agent_core/domain/refs.py tests/m00/test_base.py tests/m00/test_refs.py
git commit -m "feat(m0): tipos base y referencias (RefSpec de autoría, EntityRef exacta)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Identidad y resultados

**Files:**
- Create: `agent_core/domain/identity.py`, `agent_core/domain/outcomes.py`
- Test: `tests/m00/test_identity.py`, `tests/m00/test_outcomes.py`

**Interfaces:**
- Consumes: `Model`, `UtcDatetime`, `Locale` (Task 2).
- Produces:
  - `PrincipalType`;
  - `AuthLevel`, ordenado con `<`, `<=`, `>`, `>=` y `.rank`;
  - `AuthInfo(level, at, simulated=False)`;
  - `PrincipalKey(type, id)`;
  - `Principal(type, id, roles, scopes, attrs, auth, exp)` con `.key`;
  - `SubjectRef(kind, ref)`;
  - `OnBehalfOf(subject, grant_ref, grantee, scopes, exp)`;
  - `Outcome`, `DECLARABLE`, `is_declarable(outcome, mode) -> bool`;
  - `ReasonCode`, `ReasonCodeStr`;
  - `Awaiting`, `Command` (el miembro `continue_` vale `"continue"`), `Mode`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_identity.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.identity import AuthLevel, OnBehalfOf, Principal, PrincipalKey, PrincipalType

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _principal(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "customer",
        "id": "cust-001",
        "auth": {"level": "session", "at": NOW},
        "exp": NOW + timedelta(hours=1),
    }
    return base | over


# T-M0-08
def test_session_customer_valid_and_key() -> None:
    principal = Principal.model_validate(_principal())
    assert principal.key == PrincipalKey(type=PrincipalType.customer, id="cust-001")


def test_anonymous_customer_valid() -> None:
    Principal.model_validate(_principal(id=None, auth={"level": "anonymous", "at": NOW}))


def test_anonymous_with_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(auth={"level": "anonymous", "at": NOW}))


def test_session_without_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(id=None))


def test_anonymous_advisor_rejected() -> None:
    with pytest.raises(ValidationError):
        Principal.model_validate(_principal(type="advisor", id=None, auth={"level": "anonymous", "at": NOW}))


def test_auth_level_ordering_is_by_rank_not_alphabet() -> None:
    assert AuthLevel.session >= AuthLevel.anonymous
    assert AuthLevel.step_up > AuthLevel.session
    assert AuthLevel.anonymous < AuthLevel.step_up
    assert not (AuthLevel.session < AuthLevel.anonymous)
    assert AuthLevel.step_up.rank == 2
    assert sorted([AuthLevel.step_up, AuthLevel.anonymous, AuthLevel.session]) == [
        AuthLevel.anonymous,
        AuthLevel.session,
        AuthLevel.step_up,
    ]


def _obo(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "subject": {"kind": "customer", "ref": "cust-001"},
        "grant_ref": "grant-9",
        "grantee": {"type": "advisor", "id": "adv-7"},
        "scopes": ["read"],
        "exp": NOW + timedelta(hours=1),
    }
    return base | over


def test_on_behalf_of_requires_advisor_grantee() -> None:
    OnBehalfOf.model_validate(_obo())
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grantee={"type": "customer", "id": "cust-001"}))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate(_obo(grantee={"type": "advisor", "id": ""}))
    with pytest.raises(ValidationError):
        OnBehalfOf.model_validate({k: v for k, v in _obo().items() if k != "grantee"})
```

`tests/m00/test_outcomes.py`:

```python
import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.outcomes import Command, Outcome, ReasonCodeStr, is_declarable

REASON = TypeAdapter(ReasonCodeStr)


# T-M0-02
@pytest.mark.parametrize("mode", ["conversational", "task"])
@pytest.mark.parametrize("outcome", [Outcome.abandoned, Outcome.escalated])
def test_engine_only_outcomes_never_declarable(mode: str, outcome: Outcome) -> None:
    assert not is_declarable(outcome, mode)


def test_declarable_by_mode() -> None:
    for outcome in (Outcome.resolved, Outcome.abstained, Outcome.cancelled, Outcome.clarify_exhausted):
        assert is_declarable(outcome, "conversational")
        assert not is_declarable(outcome, "task")
    for outcome in (Outcome.completed, Outcome.failed):
        assert is_declarable(outcome, "task")
        assert not is_declarable(outcome, "conversational")


def test_unknown_mode_declares_nothing() -> None:
    assert not is_declarable(Outcome.resolved, "batch")


# T-M0-13
@pytest.mark.parametrize(
    "code",
    ["low_confidence", "release_revoked", "rule:umbral", "policy:escalamiento-disputa-monto", "interrupt:fraude"],
)
def test_reason_code_accepts(code: str) -> None:
    assert REASON.validate_python(code) == code


@pytest.mark.parametrize("code", ["", "whatever", "rule:", "policy:Mayus", "note:x", "interrupt: fraude"])
def test_reason_code_rejects(code: str) -> None:
    with pytest.raises(ValidationError):
        REASON.validate_python(code)


def test_command_continue_value() -> None:
    assert Command("continue") is Command.continue_
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_identity.py tests/m00/test_outcomes.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `identity.py`**

`agent_core/domain/identity.py`:

```python
"""Identidad y acceso (M0 §2.3, ADR 0006, ADR 0010)."""

from enum import StrEnum

from pydantic import Field, model_validator

from agent_core.domain.base import Model, UtcDatetime


class PrincipalType(StrEnum):
    customer = "customer"
    advisor = "advisor"
    service = "service"
    builder = "builder"


_AUTH_RANK = {"anonymous": 0, "session": 1, "step_up": 2}


class AuthLevel(StrEnum):
    """Nivel de autenticación; se compara por rango (`anonymous < session < step_up`)."""

    anonymous = "anonymous"
    session = "session"
    step_up = "step_up"

    @property
    def rank(self) -> int:
        return _AUTH_RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self.rank >= other.rank


class AuthInfo(Model):
    level: AuthLevel
    at: UtcDatetime
    simulated: bool = False  # OTP de prueba etiquetado (ADR 0010)


class PrincipalKey(Model):
    type: PrincipalType
    id: str | None


class SubjectRef(Model):
    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class Principal(Model):
    """Quien invoca, ya verificado por M9. Snapshot sin credencial ni secretos."""

    type: PrincipalType
    id: str | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    auth: AuthInfo
    exp: UtcDatetime

    @model_validator(mode="after")
    def _anonymous_rules(self) -> "Principal":
        anonymous = self.auth.level is AuthLevel.anonymous
        if anonymous != (self.id is None):
            raise ValueError("un principal es anónimo si y solo si no tiene id")
        if anonymous and self.type is not PrincipalType.customer:
            raise ValueError("solo un customer puede ser anónimo")
        return self

    @property
    def key(self) -> PrincipalKey:
        return PrincipalKey(type=self.type, id=self.id)


class OnBehalfOf(Model):
    """Delegación firmada por el emisor de asignaciones, atada al asesor `grantee` (ADR 0006)."""

    subject: SubjectRef
    grant_ref: str = Field(min_length=1)
    grantee: PrincipalKey
    scopes: list[str] = Field(default_factory=list)
    exp: UtcDatetime

    @model_validator(mode="after")
    def _grantee_is_advisor(self) -> "OnBehalfOf":
        if self.grantee.type is not PrincipalType.advisor or not self.grantee.id:
            raise ValueError("grantee debe ser un advisor con id")
        return self
```

- [ ] **Step 4: Implementar `outcomes.py`**

`agent_core/domain/outcomes.py`:

```python
"""Resultados, códigos de motivo y enums del turno (M0 §2.7)."""

import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator

Mode = Literal["conversational", "task"]


class Outcome(StrEnum):
    resolved = "resolved"
    abstained = "abstained"
    cancelled = "cancelled"
    clarify_exhausted = "clarify_exhausted"
    completed = "completed"
    failed = "failed"
    abandoned = "abandoned"
    escalated = "escalated"


DECLARABLE: Mapping[str, frozenset[Outcome]] = MappingProxyType(
    {
        "conversational": frozenset(
            {Outcome.resolved, Outcome.abstained, Outcome.cancelled, Outcome.clarify_exhausted}
        ),
        "task": frozenset({Outcome.completed, Outcome.failed}),
    }
)


def is_declarable(outcome: Outcome, mode: str) -> bool:
    """Única fuente de outcomes declarables por un `end` (M1 G0-14, M2). `abandoned`/`escalated` nunca."""
    return outcome in DECLARABLE.get(mode, frozenset())


class ReasonCode(StrEnum):
    low_confidence = "low_confidence"
    budget_exceeded = "budget_exceeded"
    tool_failure = "tool_failure"
    customer_request = "customer_request"
    verification_failed = "verification_failed"
    validation_failed = "validation_failed"
    release_revoked = "release_revoked"
    auth_insufficient = "auth_insufficient"


_REASON_PREFIX = re.compile(r"^(rule|policy|interrupt):[a-z0-9][a-z0-9_/-]*$")


def _check_reason(value: str) -> str:
    if value in ReasonCode.__members__ or _REASON_PREFIX.match(value):
        return value
    raise ValueError(f"reason_code inválido: {value!r}")


ReasonCodeStr = Annotated[str, AfterValidator(_check_reason)]


class Awaiting(StrEnum):
    none = "none"
    slot = "slot"
    confirmation = "confirmation"
    step_up = "step_up"
    input = "input"


class Command(StrEnum):
    """Comandos de Understand (spec general §4.4)."""

    start_flow = "start_flow"
    continue_ = "continue"
    affirm = "affirm"
    deny = "deny"
    clarify = "clarify"
    cancel = "cancel"
    handoff = "handoff"
    out_of_scope = "out_of_scope"
    interrupt = "interrupt"
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m00/test_identity.py tests/m00/test_outcomes.py -v`
Esperado: todas PASS.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/identity.py agent_core/domain/outcomes.py tests/m00/test_identity.py tests/m00/test_outcomes.py
git commit -m "feat(m0): identidad (grantee, AuthLevel ordenado) y outcomes declarables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Esquemas de nodos

**Files:**
- Create: `agent_core/domain/nodes.py`
- Test: `tests/m00/test_nodes.py`

**Interfaces:**
- Consumes: `Model`, `NodeId` (Task 2), `RefSpec` (Task 2), `JsonValue` (Task 1), `Outcome`, `ReasonCodeStr` (Task 3), `PrincipalType` (Task 3).
- Produces:
  - Configs: `DecideConfig`, `RuleConfig`, `CollectConfig`, `SlotValidator`, `ToolConfig`, `WriteToolConfig`, `ActionSpec`, `ConfirmConfig`, `VerifyConfig`, `GenerateConfig`, `RespondConfig` (campo `await_` con alias `"await"`), `EscalateConfig`, `EndConfig`, `AgentNodeConfig`, `SubflowConfig`, `Approver`, `AwaitApprovalConfig`.
  - Nodos: `DecideNode`, `RuleNode`, `CollectNode`, `ToolNode`, `WriteToolNode`, `ConfirmNode`, `VerifyNode`, `RespondNode`, `EscalateNode`, `EndNode`, `AgentNode`, `SubflowNode`, `AwaitApprovalNode`, todos con `id`, `type`, `config` y `next: dict[str, str]`.
  - `node_kind(value) -> str | None`, `Node`, `RESULTS: Mapping[str, frozenset[str]]`, `TERMINAL`, `WAITING`, `MVP_NODE_KINDS`, `PRODUCTION_NODE_KINDS`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_nodes.py`:

```python
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.nodes import (
    MVP_NODE_KINDS,
    PRODUCTION_NODE_KINDS,
    RESULTS,
    CollectNode,
    DecideNode,
    Node,
    RespondNode,
    ToolNode,
    WriteToolNode,
    node_kind,
)

NODE = TypeAdapter(Node)


def _tool(config: dict[str, Any]) -> dict[str, Any]:
    return {"id": "n1", "type": "tool", "config": config, "next": {"ok": "fin"}}


# T-M0-12
def test_tool_with_action_from_is_write_node() -> None:
    node = NODE.validate_python(_tool({"action_from": "confirmar", "save_as": "pqr"}))
    assert isinstance(node, WriteToolNode)
    assert node_kind(node) == "tool_write"


def test_tool_without_action_from_is_read_node() -> None:
    node = NODE.validate_python(_tool({"tool": "buscar@1", "args": {"texto": "slots.x"}, "save_as": "c"}))
    assert isinstance(node, ToolNode)
    assert node_kind(node) == "tool"


def test_write_node_rejects_own_args() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"action_from": "confirmar", "save_as": "pqr", "args": {"a": 1}}))


def test_respond_exactly_one_of_template_or_generate() -> None:
    generate = {"prompt_ref": "p/x", "fallback_template_ref": "t/y"}
    with pytest.raises(ValidationError):
        NODE.validate_python(
            {"id": "r", "type": "respond", "config": {"template_ref": "t/x", "generate": generate}}
        )
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "respond", "config": {}})


def test_respond_await_alias_and_claims() -> None:
    node = NODE.validate_python(
        {"id": "r", "type": "respond", "config": {"template_ref": "t/x", "await": True, "claims": ["confirmar"]}}
    )
    assert isinstance(node, RespondNode)
    assert node.config.await_ is True
    assert node.config.claims == ["confirmar"]
    assert node.model_dump(by_alias=True)["config"]["await"] is True


def test_decide_has_no_branches() -> None:
    raw = {
        "id": "coincide",
        "type": "decide",
        "config": {"model": "match-cargo@2", "branch_on": "match", "save_as": "coincide"},
        "next": {"unica": "elegir", "low_confidence": "aclarar"},
    }
    assert isinstance(NODE.validate_python(raw), DecideNode)
    raw["config"] = {**raw["config"], "branches": {"unica": "elegir"}}
    with pytest.raises(ValidationError):
        NODE.validate_python(raw)


def test_collect_validator_optional() -> None:
    node = NODE.validate_python(
        {"id": "c", "type": "collect", "config": {"slot": "s", "prompt_ref": "t/p", "max_attempts": 2}}
    )
    assert isinstance(node, CollectNode)
    assert node.config.validator is None


def test_rule_exactly_one_of_policy_or_expr() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "rule", "config": {}})
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "r", "type": "rule", "config": {"policy": "p@1", "expr": {"==": [1, 1]}}})


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "k", "type": "knowledge", "config": {}})


def test_escalate_reason_code_validated() -> None:
    NODE.validate_python({"id": "e", "type": "escalate", "config": {"reason_code": "policy:monto"}})
    with pytest.raises(ValidationError):
        NODE.validate_python({"id": "e", "type": "escalate", "config": {"reason_code": "porque si"}})


def test_results_cover_mvp_kinds() -> None:
    assert frozenset(
        {"decide", "rule", "collect", "tool", "tool_write", "confirm", "verify", "respond", "escalate", "end"}
    ) == MVP_NODE_KINDS
    assert MVP_NODE_KINDS | PRODUCTION_NODE_KINDS == frozenset(RESULTS)
    assert RESULTS["tool_write"] == frozenset({"ok", "denied", "uncertain"})
    assert RESULTS["confirm"] == frozenset({"yes", "no", "unclear", "max_attempts"})
    assert RESULTS["decide"] == frozenset({"low_confidence"})
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_nodes.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `nodes.py`**

`agent_core/domain/nodes.py`:

```python
"""Esquemas del catálogo cerrado de nodos (M0 §2.5; spec general §5).

Validan forma, no semántica del grafo: alcanzabilidad, dominancia, reclamos y cobertura de `next` son reglas G0
de M1. En YAML el nodo de escritura sigue siendo `type: tool`; el discriminador lo reconoce por `action_from`.
"""

from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, PositiveInt, StringConstraints, Tag, model_validator

from agent_core.domain.base import Model, NodeId
from agent_core.domain.identity import PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Outcome, ReasonCodeStr
from agent_core.domain.refs import RefSpec

SaveAs = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class SlotValidator(Model):
    kind: Literal["type", "regex", "enum", "decide"]
    value: JsonValue


class DecideConfig(Model):
    """Resultados = valores del enum de `branch_on` + `low_confidence`, cableados en `next` (rev. 4)."""

    model: RefSpec
    input_view: list[str] | None = None
    branch_on: str
    save_as: SaveAs


class RuleConfig(Model):
    policy: RefSpec | None = None
    expr: JsonValue = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "RuleConfig":
        if (self.policy is None) == (self.expr is None):
            raise ValueError("rule declara exactamente uno: policy o expr")
        return self


class CollectConfig(Model):
    slot: str
    prompt_ref: RefSpec
    validator: SlotValidator | None = None  # None: texto no vacío
    max_attempts: PositiveInt = 2


class ToolConfig(Model):
    tool: RefSpec
    args: dict[str, JsonValue] = Field(default_factory=dict)
    save_as: SaveAs
    step_up_max_attempts: PositiveInt = 2


class WriteToolConfig(Model):
    """Escritura: sin `args` propios; usa la acción congelada del `confirm` (ADR 0007)."""

    action_from: NodeId
    save_as: SaveAs
    step_up_max_attempts: PositiveInt = 2


class ActionSpec(Model):
    tool: RefSpec
    args: dict[str, JsonValue] = Field(default_factory=dict)


class ConfirmConfig(Model):
    action: ActionSpec
    summary_template: RefSpec
    reprompt_template: RefSpec | None = None
    max_attempts: PositiveInt = 2


class VerifyConfig(Model):
    readback: RefSpec
    by: Annotated[str, StringConstraints(pattern=r"^(idempotency_key|fact:.+)$")]
    predicate: JsonValue
    save_as: SaveAs


class GenerateConfig(Model):
    prompt_ref: RefSpec
    allowed_facts: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)  # se reemplaza al cerrar el tema #10
    fallback_template_ref: RefSpec


class RespondConfig(Model):
    template_ref: RefSpec | None = None
    generate: GenerateConfig | None = None
    await_: bool = Field(default=False, alias="await")
    claims: list[NodeId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one(self) -> "RespondConfig":
        if (self.template_ref is None) == (self.generate is None):
            raise ValueError("respond declara exactamente uno: template_ref o generate")
        return self


class EscalateConfig(Model):
    reason_code: ReasonCodeStr
    target_queue: str | None = None  # None: agent.default_target_queue
    priority_expr: JsonValue = None


class EndConfig(Model):
    outcome: Outcome
    output_map: dict[str, str] | None = None


class AgentNodeConfig(Model):
    tools_allowed: list[RefSpec]
    max_steps: PositiveInt
    prompt_ref: RefSpec
    goal: str


class SubflowConfig(Model):
    flow: RefSpec
    map_in: dict[str, str] = Field(default_factory=dict)
    map_out: dict[str, str] = Field(default_factory=dict)


class Approver(Model):
    principal_type: PrincipalType
    roles: list[str] = Field(default_factory=list)


class AwaitApprovalConfig(Model):
    approver: Approver
    summary_template: RefSpec
    timeout: timedelta


class _NodeBase(Model):
    id: NodeId
    next: dict[str, NodeId] = Field(default_factory=dict)


class DecideNode(_NodeBase):
    type: Literal["decide"]
    config: DecideConfig


class RuleNode(_NodeBase):
    type: Literal["rule"]
    config: RuleConfig


class CollectNode(_NodeBase):
    type: Literal["collect"]
    config: CollectConfig


class ToolNode(_NodeBase):
    type: Literal["tool"]
    config: ToolConfig


class WriteToolNode(_NodeBase):
    type: Literal["tool"]
    config: WriteToolConfig


class ConfirmNode(_NodeBase):
    type: Literal["confirm"]
    config: ConfirmConfig


class VerifyNode(_NodeBase):
    type: Literal["verify"]
    config: VerifyConfig


class RespondNode(_NodeBase):
    type: Literal["respond"]
    config: RespondConfig


class EscalateNode(_NodeBase):
    type: Literal["escalate"]
    config: EscalateConfig


class EndNode(_NodeBase):
    type: Literal["end"]
    config: EndConfig


class AgentNode(_NodeBase):
    type: Literal["agent"]
    config: AgentNodeConfig


class SubflowNode(_NodeBase):
    type: Literal["subflow"]
    config: SubflowConfig


class AwaitApprovalNode(_NodeBase):
    type: Literal["await_approval"]
    config: AwaitApprovalConfig


def node_kind(value: Any) -> str | None:
    """Discriminador: `type`, salvo `tool` con `action_from` → `tool_write`."""
    if isinstance(value, dict):
        kind = value.get("type")
        config = value.get("config")
        if kind == "tool" and isinstance(config, dict) and "action_from" in config:
            return "tool_write"
        return kind if isinstance(kind, str) else None
    if isinstance(value, WriteToolNode):
        return "tool_write"
    kind_attr = getattr(value, "type", None)
    return kind_attr if isinstance(kind_attr, str) else None


Node = Annotated[
    Annotated[DecideNode, Tag("decide")]
    | Annotated[RuleNode, Tag("rule")]
    | Annotated[CollectNode, Tag("collect")]
    | Annotated[ToolNode, Tag("tool")]
    | Annotated[WriteToolNode, Tag("tool_write")]
    | Annotated[ConfirmNode, Tag("confirm")]
    | Annotated[VerifyNode, Tag("verify")]
    | Annotated[RespondNode, Tag("respond")]
    | Annotated[EscalateNode, Tag("escalate")]
    | Annotated[EndNode, Tag("end")]
    | Annotated[AgentNode, Tag("agent")]
    | Annotated[SubflowNode, Tag("subflow")]
    | Annotated[AwaitApprovalNode, Tag("await_approval")],
    Discriminator(node_kind),
]

RESULTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decide": frozenset({"low_confidence"}),  # + valores del enum de branch_on (M1 G0-03)
        "rule": frozenset({"true", "false"}),
        "collect": frozenset({"ok", "max_attempts"}),
        "tool": frozenset({"ok", "error", "timeout", "denied"}),
        "tool_write": frozenset({"ok", "denied", "uncertain"}),
        "confirm": frozenset({"yes", "no", "unclear", "max_attempts"}),
        "verify": frozenset({"verified", "failed"}),
        "respond": frozenset({"next"}),
        "escalate": frozenset(),
        "end": frozenset(),
        "agent": frozenset({"answered", "gave_up"}),
        "subflow": frozenset(),  # los declara el subflow
        "await_approval": frozenset({"approved", "rejected", "timeout"}),
    }
)
TERMINAL: frozenset[str] = frozenset({"escalate", "end"})
WAITING: frozenset[str] = frozenset({"collect", "confirm"})  # más respond con await: true
PRODUCTION_NODE_KINDS: frozenset[str] = frozenset({"agent", "subflow", "await_approval"})
MVP_NODE_KINDS: frozenset[str] = frozenset(RESULTS) - PRODUCTION_NODE_KINDS
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/m00/test_nodes.py -v`
Esperado: todas PASS.

- [ ] **Step 5: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/nodes.py tests/m00/test_nodes.py
git commit -m "feat(m0): esquemas del catálogo de nodos y discriminador de escritura

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Entidades del registro

**Files:**
- Create: `agent_core/domain/entities.py`, `tests/m00/fixtures.py`
- Test: `tests/m00/test_entities.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `Budgets`, `EngineTemplates`, `Agent`, `Flow`;
  - `EscalateAction`, `StartFlowAction`, `Interrupt`;
  - `LanguageDetection`, `InjectionRule`, `InjectionRuleset`, `Release`;
  - `Policy`, `Template`, `Prompt`;
  - `RiskClass`, `ToolDef` (con `.is_write`);
  - `ProviderSpec`, `CalibrationRef`, `DecisionModelDef`;
  - `RegistryEntity` (unión) y `ENTITY_KIND: Mapping[type, EntityKind]`;
  - `tests/m00/fixtures.py` con `DISPUTA_CARGO: dict` (flow de autoría sintético).

- [ ] **Step 1: Escribir el fixture del flow de ejemplo**

`tests/m00/fixtures.py`, con el flow `disputa-cargo` de la spec general §5 completo y datos sintéticos:

```python
"""Flow de ejemplo `disputa-cargo` (spec general §5), completado con los nodos omitidos. Datos sintéticos."""

from typing import Any

DISPUTA_CARGO: dict[str, Any] = {
    "id": "disputa-cargo",
    "version": "1.0.0",
    "priority": 50,
    "nodes": [
        {"id": "pedir_cargo", "type": "collect",
         "config": {"slot": "descripcion_cargo", "prompt_ref": "t/pedir_cargo", "max_attempts": 2},
         "next": {"ok": "buscar_tx", "max_attempts": "esc_sin_datos"}},
        {"id": "buscar_tx", "type": "tool",
         "config": {"tool": "buscar_transacciones@1", "args": {"texto": "slots.descripcion_cargo"},
                    "save_as": "candidatas"},
         "next": {"ok": "coincide", "error": "esc_tool", "timeout": "esc_tool", "denied": "esc_tool"}},
        {"id": "coincide", "type": "decide",
         "config": {"model": "match-cargo@2", "branch_on": "match", "save_as": "coincide"},
         "next": {"unica": "elegir", "ninguna": "aclarar", "varias": "aclarar", "low_confidence": "aclarar"}},
        {"id": "elegir", "type": "tool",
         "config": {"tool": "seleccionar@1",
                    "args": {"lista": "facts.candidatas", "id": "decisions.coincide.transaction"},
                    "save_as": "transaccion_elegida"},
         "next": {"ok": "a_usd", "error": "esc_tool", "timeout": "esc_tool", "denied": "esc_tool"}},
        {"id": "a_usd", "type": "tool",
         "config": {"tool": "convertir_moneda@1",
                    "args": {"monto": "facts.transaccion_elegida.value.amount",
                             "moneda": "facts.transaccion_elegida.value.currency", "destino": "USD"},
                    "save_as": "monto_usd"},
         "next": {"ok": "umbral", "error": "esc_tool", "timeout": "esc_tool", "denied": "esc_tool"}},
        {"id": "umbral", "type": "rule", "config": {"policy": "escalamiento-disputa-monto@1"},
         "next": {"true": "esc_monto", "false": "confirmar"}},
        {"id": "confirmar", "type": "confirm",
         "config": {"action": {"tool": "radicar_pqr@1",
                               "args": {"transaction_id": "facts.transaccion_elegida.value.transaction_id",
                                        "descripcion": "slots.descripcion_cargo"}},
                    "summary_template": "t/resumen_pqr"},
         "next": {"yes": "radicar", "no": "fin_cancelado", "unclear": "confirmar",
                  "max_attempts": "no_confirmado"}},
        {"id": "radicar", "type": "tool", "config": {"action_from": "confirmar", "save_as": "pqr"},
         "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc_tool"}},
        {"id": "verificar", "type": "verify",
         "config": {"readback": "obtener_pqr@1", "by": "idempotency_key",
                    "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "pqr_verificada"},
         "next": {"verified": "responder_ok", "failed": "esc_verif"}},
        {"id": "responder_ok", "type": "respond",
         "config": {"template_ref": "t/pqr_radicado", "claims": ["confirmar"]}, "next": {"next": "fin"}},
        {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "aclarar", "type": "respond", "config": {"template_ref": "t/aclarar_cargo", "await": True},
         "next": {"next": "pedir_cargo"}},
        {"id": "no_confirmado", "type": "respond", "config": {"template_ref": "t/no_confirmado"},
         "next": {"next": "fin_cancelado"}},
        {"id": "fin_cancelado", "type": "end", "config": {"outcome": "cancelled"}},
        {"id": "esc_sin_datos", "type": "escalate", "config": {"reason_code": "low_confidence"}},
        {"id": "esc_tool", "type": "escalate", "config": {"reason_code": "tool_failure"}},
        {"id": "esc_monto", "type": "escalate",
         "config": {"reason_code": "policy:escalamiento-disputa-monto", "target_queue": "disputas"}},
        {"id": "esc_verif", "type": "escalate", "config": {"reason_code": "verification_failed"}},
    ],
}
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m00/test_entities.py`:

```python
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.entities import (
    Agent,
    Budgets,
    EscalateAction,
    Flow,
    Interrupt,
    Release,
    StartFlowAction,
    ToolDef,
)
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.nodes import WriteToolNode
from agent_core.domain.refs import require_exact_refs
from tests.m00.fixtures import DISPUTA_CARGO

TEMPLATES = {
    "clarify": "t/aclarar", "abstain": "t/abstencion", "handoff": "t/traspaso", "pending_ack": "t/acuse",
    "pending_offer": "t/oferta", "unsupported_language": "t/idioma_no_soportado",
    "input_too_large": "t/mensaje_largo",
}
BUDGETS = {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
           "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000}


def _agent(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "disputa-cargo@1",
        "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"], "default_locale": "es", "tools_allowed": ["buscar_transacciones@1"],
        "budgets": BUDGETS, "understand": "understand@1", "templates": TEMPLATES, "max_clarifications": 2,
        "on_clarify_exhausted": "escalate", "default_target_queue": "general",
    }
    return base | over


def test_example_flow_parses() -> None:
    flow = Flow.model_validate(DISPUTA_CARGO)
    assert flow.nodes[0].id == "pedir_cargo"
    assert isinstance(next(n for n in flow.nodes if n.id == "radicar"), WriteToolNode)


def test_authoring_flow_is_not_runtime_ready() -> None:
    with pytest.raises(InvalidRuntimeRef):
        require_exact_refs(Flow.model_validate(DISPUTA_CARGO))


def test_flow_requires_nodes() -> None:
    with pytest.raises(ValidationError):
        Flow.model_validate({**DISPUTA_CARGO, "nodes": []})


def test_agent_defaults_and_locale_rule() -> None:
    agent = Agent.model_validate(_agent())
    assert agent.inactivity_ttl == timedelta(minutes=30)
    assert agent.max_repair_turns_per_run == 8
    assert agent.budgets.max_cost_per_run == Decimal("0.50")
    with pytest.raises(ValidationError):
        Agent.model_validate(_agent(default_locale="en"))


def test_budgets_positive() -> None:
    with pytest.raises(ValidationError):
        Budgets.model_validate({**BUDGETS, "max_nodes_per_turn": 0})
    with pytest.raises(ValidationError):
        Budgets.model_validate({**BUDGETS, "max_cost_per_run": "0"})


def _tool(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible",
                            "min_auth_level": "session", "idempotent": False,
                            "readback_by": "idempotency_key"}
    return base | over


def test_write_tool_requires_readback() -> None:
    tool = ToolDef.model_validate(_tool())
    assert tool.is_write
    assert tool.confirmation_ttl == timedelta(minutes=5)
    with pytest.raises(ValidationError):
        ToolDef.model_validate(_tool(readback_by=None))
    assert not ToolDef.model_validate(_tool(risk_class="read", readback_by=None)).is_write


def test_interrupt_action_discriminated() -> None:
    esc = Interrupt.model_validate(
        {"id": "fraude", "priority": 100,
         "action": {"type": "escalate", "target_queue": "fraude", "priority": "critical"}}
    )
    assert isinstance(esc.action, EscalateAction)
    start = Interrupt.model_validate(
        {"id": "bloqueo", "priority": 90, "action": {"type": "start_flow", "flow": "bloquear@1.0.0"}}
    )
    assert isinstance(start.action, StartFlowAction)


def test_release_entities_must_be_exact() -> None:
    base: dict[str, Any] = {"id": "rel-1", "status": "active", "interrupts": [],
                            "language_detection": "lang-detect@1.0.0"}
    release = Release.model_validate({**base, "entities": {"flow": {"disputa-cargo": "1.0.0"}}})
    assert release.max_input_chars == 4000
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "entities": {"flow": {"disputa-cargo": "^1"}}})
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_entities.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.domain.entities'`.

- [ ] **Step 4: Implementar `entities.py`**

`agent_core/domain/entities.py`:

```python
"""Entidades del registro (M0 §2.4). Datos puros: la lógica vive en los módulos que las usan."""

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from agent_core.domain.base import EntityId, ExactVersion, Locale, Model
from agent_core.domain.identity import AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.nodes import Node
from agent_core.domain.outcomes import Mode
from agent_core.domain.refs import EntityKind, EntityRef, RefSpec


class Budgets(Model):
    max_nodes_per_turn: PositiveInt
    max_model_calls_per_turn: PositiveInt
    max_tokens_per_run: PositiveInt
    max_cost_per_run: Annotated[Decimal, Field(gt=0)]
    max_wall_ms_per_turn: PositiveInt


class EngineTemplates(Model):
    """Plantillas que usa el motor fuera de los flows (M4, M6, M10)."""

    clarify: RefSpec
    abstain: RefSpec
    handoff: RefSpec
    pending_ack: RefSpec
    pending_offer: RefSpec
    unsupported_language: RefSpec
    input_too_large: RefSpec


class Agent(Model):
    id: EntityId
    version: ExactVersion
    mode: Mode
    entry_flow: RefSpec
    invocable_by: list[PrincipalType] = Field(min_length=1)
    min_auth_level: AuthLevel
    subject_kinds: list[str]
    supported_locales: list[Locale] = Field(min_length=1)
    default_locale: Locale
    tools_allowed: list[RefSpec] = Field(default_factory=list)
    budgets: Budgets
    inactivity_ttl: timedelta = timedelta(minutes=30)
    understand: RefSpec | None = None
    templates: EngineTemplates
    max_clarifications: NonNegativeInt
    on_clarify_exhausted: Literal["end", "escalate"]
    default_target_queue: str = Field(min_length=1)
    max_repair_turns_per_run: PositiveInt = 8

    @model_validator(mode="after")
    def _default_locale_supported(self) -> "Agent":
        if self.default_locale not in self.supported_locales:
            raise ValueError("default_locale debe estar en supported_locales")
        return self


class Flow(Model):
    id: EntityId
    version: ExactVersion
    priority: int
    nodes: list[Node] = Field(min_length=1)  # el primer nodo es la entrada


class EscalateAction(Model):
    type: Literal["escalate"]
    target_queue: str
    priority: str


class StartFlowAction(Model):
    type: Literal["start_flow"]
    flow: RefSpec


class Interrupt(Model):
    id: EntityId
    priority: int
    action: Annotated[EscalateAction | StartFlowAction, Field(discriminator="type")]
    signal_policy: RefSpec | None = None


class LanguageDetection(Model):
    id: EntityId
    version: ExactVersion
    detector: str  # "lingua@<versión exacta>"
    candidates: list[Locale]
    unsupported: list[str] = Field(default_factory=list)
    min_letters: NonNegativeInt
    min_letters_unsupported: NonNegativeInt
    thresholds_from: str | None = None


class InjectionRule(Model):
    id: str
    pattern: str
    kind: Literal["regex", "phrase"]


class InjectionRuleset(Model):
    id: EntityId
    version: ExactVersion
    rules: list[InjectionRule]


class Release(Model):
    id: str = Field(min_length=1)
    status: Literal["active", "revoked"]
    entities: dict[EntityKind, dict[EntityId, ExactVersion]] = Field(default_factory=dict)
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: EntityRef
    injection_ruleset: EntityRef | None = None
    max_input_chars: PositiveInt = 4000


class Policy(Model):
    id: EntityId
    version: ExactVersion
    owner: str
    expr: JsonValue
    rationale: str


class Template(Model):
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()  # variables de hecho que lee (derive_claims de M1)


class Prompt(Model):
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()


class RiskClass(StrEnum):
    read = "read"
    compute = "compute"
    write_reversible = "write_reversible"
    write_irreversible = "write_irreversible"
    money_movement = "money_movement"


class ToolDef(Model):
    id: EntityId
    version: ExactVersion
    risk_class: RiskClass
    min_auth_level: AuthLevel
    max_auth_age: timedelta | None = None
    idempotent: bool
    readback_by: Literal["idempotency_key"] | None = None
    untrusted_fields: list[str] = Field(default_factory=list)
    source: str | None = None  # tabla de origen para M7
    confirmation_ttl: timedelta = timedelta(minutes=5)

    @property
    def is_write(self) -> bool:
        return self.risk_class not in (RiskClass.read, RiskClass.compute)

    @model_validator(mode="after")
    def _write_needs_readback(self) -> "ToolDef":
        if self.is_write and self.readback_by is None:
            raise ValueError("una tool de escritura declara readback_by (ADR 0007)")
        return self


class ProviderSpec(Model):
    provider: Literal["jev", "classifier", "llm_structured", "rule"]
    config: dict[str, JsonValue] = Field(default_factory=dict)


class CalibrationRef(Model):
    method: Literal["none", "isotonic", "platt", "temperature"]
    run: str | None = None


class DecisionModelDef(Model):
    id: EntityId
    version: ExactVersion
    output_schema: dict[str, JsonValue]
    calibrated_fields: list[str]
    input_view: list[str] = Field(default_factory=list)
    providers: list[ProviderSpec] = Field(min_length=1)
    calibration: CalibrationRef
    thresholds_from: str | None = None


RegistryEntity = (
    Agent | Flow | Policy | Template | Prompt | ToolDef | DecisionModelDef | LanguageDetection | InjectionRuleset
)

ENTITY_KIND: Mapping[type, EntityKind] = MappingProxyType(
    {
        Agent: EntityKind.agent,
        Flow: EntityKind.flow,
        Policy: EntityKind.policy,
        Template: EntityKind.template,
        Prompt: EntityKind.prompt,
        ToolDef: EntityKind.tool,
        DecisionModelDef: EntityKind.decision_model,
        LanguageDetection: EntityKind.language_detection,
        InjectionRuleset: EntityKind.injection_ruleset,
    }
)
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m00/test_entities.py -v`
Esperado: todas PASS.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/entities.py tests/m00/fixtures.py tests/m00/test_entities.py
git commit -m "feat(m0): entidades del registro (Agent con plantillas del motor, ToolDef, Release)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Estado del run, turnos y tipos compartidos

**Files:**
- Create: `agent_core/domain/shared.py`, `agent_core/domain/state.py`, `agent_core/domain/turn.py`, `testing/builders.py`
- Test: `tests/m00/test_state.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `shared.py`: `ToolStatus`, `EscalationRequest`, `RejectedDraft`, `Fingerprint`, `TranscriptEntry`, `TranscriptRef`, `OutboxMessage`.
  - `state.py`: `RunStatus`, `Slot`, `FactSource`, `Fact`, `Decision`, `ActionState`, `InvalidationReason`, `Action`, `ActiveFlow`, `PendingIntent`, `BudgetsUsed`, `EncryptedBlob`, `RunState`.
  - `turn.py`: `ConfirmAnswer`, `TurnInput`, `RunInput`, `Message`, `ConfirmationPrompt`, `StepUpPrompt`, `TurnResult`, `RunResult`.
  - `testing/builders.py`: `NOW`, `principal(**over) -> Principal`, `advisor_with_delegation() -> tuple[Principal, OnBehalfOf]`, `action(**over) -> Action`, `run_state(**over) -> RunState` (mínimo abierto), `full_run_state() -> RunState` (todas las partes pobladas).

- [ ] **Step 1: Implementar `shared.py` y `turn.py` (son solo datos; los cubre la prueba de la Task 7 y la generación de contratos)**

`agent_core/domain/shared.py`:

```python
"""Tipos que cruzan fronteras entre módulos que no pueden importarse (M0 §2.8, regla de ubicación)."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_core.domain.base import Model, UtcDatetime
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import ReasonCodeStr


class ToolStatus(StrEnum):
    ok = "ok"
    error = "error"
    timeout = "timeout"
    denied = "denied"
    uncertain = "uncertain"
    step_up_required = "step_up_required"


class EscalationRequest(Model):
    """M2/M4 → M10."""

    reason_code: ReasonCodeStr
    target_queue: str
    priority: str


class RejectedDraft(Model):
    """M8 → M11 (transcript)."""

    text_model: str
    reason: str
    failures: list[str] = Field(default_factory=list)


class Fingerprint(Model):
    """Huella con clave (ADR 0008): `value = HMAC-SHA256(k[kid], JCS(dato))`."""

    alg: Literal["HMAC-SHA256"]
    kid: str
    value: str


class TranscriptEntry(Model):
    run_id: str
    turn_id: str
    role: Literal["user", "assistant", "rejected_draft"]
    text_model: str
    reason: str | None = None


class TranscriptRef(Model):
    entry_id: str
    fingerprint: Fingerprint


class OutboxMessage(Model):
    message_id: str
    type: Literal["handoff_created"]
    run_id: str
    payload: dict[str, JsonValue]
    created_at: UtcDatetime
```

`agent_core/domain/turn.py`:

```python
"""Entrada y salida de turnos y runs (M0 §2.8)."""

from typing import Literal

from pydantic import model_validator

from agent_core.domain.base import Locale, Model, UtcDatetime
from agent_core.domain.identity import AuthLevel, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Outcome
from agent_core.domain.refs import AgentSelector
from agent_core.domain.state import RunStatus


class ConfirmAnswer(Model):
    token: str
    answer: Literal["yes", "no"]


class TurnInput(Model):
    session_id: str
    text: str = ""
    channel: str
    lang: Locale | None = None
    client_turn_id: str
    confirm: ConfirmAnswer | None = None

    @model_validator(mode="after")
    def _text_or_confirm(self) -> "TurnInput":
        if not self.text.strip() and self.confirm is None:
            raise ValueError("un turno trae texto o una respuesta de confirmación")
        return self


class RunInput(Model):
    agent: AgentSelector
    subject: SubjectRef | None = None
    input: dict[str, JsonValue] | None = None
    lang: Locale | None = None
    idempotency_key: str


class Message(Model):
    kind: Literal["template", "generated"]
    text: str
    locale: Locale


class ConfirmationPrompt(Model):
    """Forma interna; la API publica `summary.text` como `action_summary`."""

    action_id: str
    token: str
    expires_at: UtcDatetime
    summary: Message


class StepUpPrompt(Model):
    required_level: AuthLevel
    reason: str


class TurnResult(Model):
    run_id: str
    turn_id: str
    messages: list[Message]
    locale: Locale
    awaiting: Awaiting
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    status: RunStatus
    outcome: Outcome | None = None
    handoff_ref: str | None = None
    trace_id: str


class RunResult(Model):
    run_id: str
    session_id: str | None = None
    release: str
    output: dict[str, JsonValue] | None = None
    status: RunStatus
    outcome: Outcome | None = None
    handoff_ref: str | None = None
    first_turn: TurnResult | None = None
    trace_id: str
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m00/test_state.py`:

```python
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.json import dumps, loads
from agent_core.domain.state import RunState
from agent_core.domain.turn import TurnInput
from testing.builders import NOW, action, full_run_state, run_state


# T-M0-03
def test_run_state_round_trip_is_lossless() -> None:
    state = full_run_state()
    restored = RunState.model_validate(loads(dumps(state)))
    assert restored == state
    fact_value = restored.facts["monto_usd"].value
    assert isinstance(fact_value, dict)
    assert fact_value["amount"] == Decimal("120.50")
    assert isinstance(fact_value["amount"], Decimal)
    assert restored.actions[0].args["monto"] == Decimal("500.00")
    assert restored.budgets_used.run_cost == Decimal("0.0123")


# T-M0-09
def _invalid(**over: Any) -> None:
    with pytest.raises(ValidationError):
        RunState.model_validate(run_state().model_dump() | over)


def test_open_run_has_no_outcome_nor_closed_at() -> None:
    _invalid(outcome="resolved")
    _invalid(closed_at=NOW)


def test_closed_run_rules() -> None:
    closed = {"status": "closed", "outcome": "resolved", "closed_at": NOW, "inactive_after": None}
    RunState.model_validate(run_state().model_dump() | closed)
    _invalid(**(closed | {"inactive_after": NOW}))
    _invalid(**(closed | {"outcome": None}))
    _invalid(**(closed | {"outcome": "escalated"}))


def test_escalated_run_rules() -> None:
    escalated = {"status": "escalated", "outcome": "escalated", "closed_at": NOW, "inactive_after": None,
                 "handoff_ref": "handoff-0001"}
    RunState.model_validate(run_state().model_dump() | escalated)
    _invalid(**(escalated | {"handoff_ref": None}))
    _invalid(**(escalated | {"outcome": "resolved"}))


def test_awaiting_needs_node_except_pending_offer() -> None:
    _invalid(awaiting="slot")
    RunState.model_validate(run_state().model_dump() | {"awaiting": "slot", "awaiting_node_id": "pedir_cargo"})
    offer = {"awaiting": "input", "pending_offer": "bloquear-tarjeta", "active_flow": None}
    RunState.model_validate(run_state().model_dump() | offer)


def test_at_most_one_proposed_action_per_confirm() -> None:
    first = action(action_id="action-0001").model_dump()
    second = action(action_id="action-0002").model_dump()
    _invalid(actions=[first, second])


def test_task_run_has_no_session() -> None:
    _invalid(mode="task")  # run_state() trae session_id


# T-M0-10 (modelos de estado)
def test_naive_datetimes_rejected_in_state() -> None:
    naive = NOW.replace(tzinfo=None)
    _invalid(created_at=naive)
    with pytest.raises(ValidationError):
        action(created_at=naive)


def test_turn_input_needs_text_or_confirm() -> None:
    with pytest.raises(ValidationError):
        TurnInput(session_id="s", text="  ", channel="web", client_turn_id="c-1")
    TurnInput.model_validate(
        {"session_id": "s", "channel": "web", "client_turn_id": "c-1", "confirm": {"token": "t", "answer": "yes"}}
    )


def test_token_expiry_is_timezone_aware() -> None:
    assert action().token_exp - NOW == timedelta(minutes=5)
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_state.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.domain.state'`.

- [ ] **Step 4: Implementar `state.py`**

`agent_core/domain/state.py`:

```python
"""Estado del run (M0 §2.6). Cada parte tiene un solo módulo que la escribe (índice §5)."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, NonNegativeInt, model_validator

from agent_core.domain.base import Locale, Model, MutableModel, NodeId, Probability, UtcDatetime
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Mode, Outcome
from agent_core.domain.refs import EntityRef

RunStatus = Literal["open", "closed", "escalated"]


class Slot(Model):
    value: JsonValue
    status: Literal["claimed", "validated"]
    source_turn: NonNegativeInt


class FactSource(Model):
    kind: Literal["tool", "compute", "identity", "knowledge"]
    ref: str
    inputs: list[str] = Field(default_factory=list)


class Fact(Model):
    fact_id: str
    value: JsonValue  # vista full
    source: FactSource
    ts: UtcDatetime


class Decision(Model):
    decision_id: str
    value: dict[str, JsonValue]
    p_cal: dict[str, Probability | None]
    provider_used: str
    model_version: str


class ActionState(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    executing = "executing"
    executed = "executed"
    uncertain = "uncertain"
    denied = "denied"
    verified = "verified"
    failed = "failed"
    cancelled = "cancelled"


class InvalidationReason(StrEnum):
    cancel = "cancel"
    abandoned = "abandoned"
    interrupt = "interrupt"
    escalated = "escalated"
    token_expired = "token_expired"
    max_attempts = "max_attempts"
    denied_by_user = "denied_by_user"


class Action(Model):
    action_id: str
    confirm_node_id: NodeId
    flow: EntityRef
    tool: EntityRef
    args: dict[str, JsonValue]
    args_hash: str
    state: ActionState
    confirmation_token_hash: str
    token_exp: UtcDatetime
    idempotency_key: str
    created_at: UtcDatetime
    cancel_reason: InvalidationReason | None = None


class ActiveFlow(Model):
    flow: EntityRef
    node_id: NodeId
    local_slots: dict[str, JsonValue] = Field(default_factory=dict)


class PendingIntent(Model):
    flow: str
    priority: int
    mention_order: NonNegativeInt


class BudgetsUsed(Model):
    run_tokens: NonNegativeInt = 0
    run_cost: Decimal = Decimal("0")
    turn_nodes: NonNegativeInt = 0
    turn_model_calls: NonNegativeInt = 0
    turn_started_at: UtcDatetime | None = None


class EncryptedBlob(Model):
    """`token_map` cifrado (lo produce M7). Base64."""

    kid: str
    nonce: str
    ciphertext: str


class RunState(MutableModel):
    run_id: str
    session_id: str | None = None
    state_version: NonNegativeInt = 0
    release: str
    agent: EntityRef
    principal: Principal
    on_behalf_of: OnBehalfOf | None = None
    subject: SubjectRef | None = None
    mode: Mode
    locale: Locale
    status: RunStatus = "open"
    outcome: Outcome | None = None
    created_at: UtcDatetime
    last_activity_at: UtcDatetime
    closed_at: UtcDatetime | None = None
    inactive_after: UtcDatetime | None = None
    awaiting: Awaiting = Awaiting.none
    awaiting_node_id: NodeId | None = None
    active_flow: ActiveFlow | None = None
    pending_intents: list[PendingIntent] = Field(default_factory=list)
    pending_offer: str | None = None
    slots: dict[str, Slot] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    decisions: dict[str, Decision] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list)
    token_map: EncryptedBlob | None = None
    open_questions: list[str] = Field(default_factory=list)
    budgets_used: BudgetsUsed = Field(default_factory=BudgetsUsed)
    turn_count: NonNegativeInt = 0
    clarifications_used: NonNegativeInt = 0
    node_attempts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    repair_turns_used: NonNegativeInt = 0
    degraded_turns: list[NonNegativeInt] = Field(default_factory=list)
    handoff_ref: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> "RunState":
        """Detecta bugs; no reemplaza la lógica de los módulos dueños (M0 §2.6)."""
        if self.status == "open":
            if self.outcome is not None or self.closed_at is not None:
                raise ValueError("un run abierto no tiene outcome ni closed_at")
        elif self.inactive_after is not None:
            raise ValueError("un run cerrado no tiene inactive_after")
        if self.status == "escalated" and (self.outcome is not Outcome.escalated or self.handoff_ref is None):
            raise ValueError("un run escalado tiene outcome escalated y handoff_ref")
        if self.status == "closed" and (self.outcome is None or self.outcome is Outcome.escalated):
            raise ValueError("un run cerrado tiene un outcome distinto de escalated")
        if self.awaiting is not Awaiting.none and self.awaiting_node_id is None:
            offer = self.awaiting is Awaiting.input and self.pending_offer is not None and self.active_flow is None
            if not offer:
                raise ValueError("awaiting requiere awaiting_node_id, salvo la oferta de intención")
        proposed = [a.confirm_node_id for a in self.actions if a.state is ActionState.proposed]
        if len(proposed) != len(set(proposed)):
            raise ValueError("a lo sumo una acción proposed por confirm")
        if self.mode == "task" and self.session_id is not None:
            raise ValueError("un run task no tiene session_id")
        return self
```

- [ ] **Step 5: Implementar `testing/builders.py`**

`testing/builders.py`:

```python
"""Constructores de datos sintéticos para pruebas de todos los módulos. Nunca datos reales del dataset."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from agent_core.domain.identity import OnBehalfOf, Principal
from agent_core.domain.state import Action, RunState

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def principal(**over: Any) -> Principal:
    base: dict[str, Any] = {
        "type": "customer",
        "id": "cust-001",
        "attrs": {"country": "CO", "segment": "demo"},
        "auth": {"level": "session", "at": NOW},
        "exp": NOW + timedelta(hours=1),
    }
    return Principal.model_validate(base | over)


def advisor_with_delegation() -> tuple[Principal, OnBehalfOf]:
    advisor = principal(type="advisor", id="adv-7", attrs={})
    obo = OnBehalfOf.model_validate(
        {
            "subject": {"kind": "customer", "ref": "cust-001"},
            "grant_ref": "grant-9",
            "grantee": {"type": "advisor", "id": "adv-7"},
            "scopes": ["read"],
            "exp": NOW + timedelta(hours=1),
        }
    )
    return advisor, obo


def action(**over: Any) -> Action:
    base: dict[str, Any] = {
        "action_id": "action-0001",
        "confirm_node_id": "confirmar",
        "flow": "disputa-cargo@1.0.0",
        "tool": "radicar_pqr@1.0.0",
        "args": {"transaction_id": "tx-demo-1", "monto": Decimal("500.00")},
        "args_hash": "0" * 64,
        "state": "proposed",
        "confirmation_token_hash": "f" * 64,
        "token_exp": NOW + timedelta(minutes=5),
        "idempotency_key": "action-0001",
        "created_at": NOW,
    }
    return Action.model_validate(base | over)


def run_state(**over: Any) -> RunState:
    base: dict[str, Any] = {
        "run_id": "run-0001",
        "session_id": "session-0001",
        "release": "rel-2026-09-28",
        "agent": "atencion@1.0.0",
        "principal": principal(),
        "subject": {"kind": "customer", "ref": "cust-001"},
        "mode": "conversational",
        "locale": "es",
        "created_at": NOW,
        "last_activity_at": NOW,
        "inactive_after": NOW + timedelta(minutes=30),
    }
    return RunState.model_validate(base | over)


def full_run_state() -> RunState:
    advisor, obo = advisor_with_delegation()
    return run_state(
        principal=advisor,
        on_behalf_of=obo,
        awaiting="confirmation",
        awaiting_node_id="confirmar",
        active_flow={"flow": "disputa-cargo@1.0.0", "node_id": "confirmar", "local_slots": {"k": 1}},
        pending_intents=[{"flow": "bloquear-tarjeta", "priority": 80, "mention_order": 1}],
        slots={"descripcion_cargo": {"value": "cargo desconocido", "status": "validated", "source_turn": 1}},
        facts={
            "monto_usd": {
                "fact_id": "fact-0001",
                "value": {"amount": Decimal("120.50"), "currency": "USD", "items": [1, "x", None, True]},
                "source": {"kind": "compute", "ref": "convertir_moneda@1.0.0", "inputs": ["fact-0000"]},
                "ts": NOW,
            }
        },
        decisions={
            "coincide": {
                "decision_id": "decision-0001",
                "value": {"match": "unica", "transaction": "⟦tx:1⟧"},
                "p_cal": {"match": 0.93, "otro": None},
                "provider_used": "classifier",
                "model_version": "clf-demo-1",
            }
        },
        actions=[action(), action(action_id="action-0000", confirm_node_id="otro_confirm", state="verified")],
        token_map={"kid": "demo-tm-1", "nonce": "bm9uY2U=", "ciphertext": "Y2lwaGVy"},
        open_questions=["¿fecha exacta del cargo?"],
        budgets_used={"run_tokens": 420, "run_cost": Decimal("0.0123"), "turn_nodes": 3,
                      "turn_model_calls": 1, "turn_started_at": NOW},
        turn_count=3,
        clarifications_used=1,
        node_attempts={"confirmar": 1},
        repair_turns_used=1,
        degraded_turns=[2],
    )
```

- [ ] **Step 6: Correr las pruebas**

Run: `uv run pytest tests/m00/test_state.py -v`
Esperado: todas PASS.

Si `test_run_state_round_trip_is_lossless` falla en `p_cal` (Pydantic no acepta un `Decimal` en un campo `float`): en `state.py`, cambia `Probability` por `Annotated[float, BeforeValidator(lambda v: float(v) if isinstance(v, Decimal) else v), Field(ge=0.0, le=1.0, allow_inf_nan=False)]`. Hazlo directamente en `base.py`, para que aplique a todos los campos de probabilidad, y vuelve a correr las pruebas de la Task 2.

- [ ] **Step 7: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/shared.py agent_core/domain/state.py agent_core/domain/turn.py testing/builders.py tests/m00/test_state.py
git commit -m "feat(m0): RunState con coherencia, turnos y tipos compartidos entre módulos

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Eventos

**Files:**
- Create: `agent_core/domain/events.py`, `tests/m00/samples.py`
- Test: `tests/m00/test_events.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `EngineEvent` (base) y un payload por evento: `RunStartedPayload`, `TurnStartedPayload` (+ `GuardsOutput`, `LangGuard`, `LangScore`, `InjectionGuard`), `CommandEmittedPayload`, `NodeEnteredPayload`, `DecisionMadePayload` (+ `LabelScore`), `RuleEvaluatedPayload`, `ToolCalledPayload`, `StepUpRequestedPayload`, `ActionConfirmedPayload`, `ActionCancelledPayload`, `ActionDispatchedPayload`, `ActionVerifiedPayload`, `ExpiryEvaluatedPayload`, `ResponseEmittedPayload` (+ `ValidatorOutcome`, `LlmUsage`), `TurnCompletedPayload` (+ `TurnStages`), `InjectionFlaggedPayload`, `AccessDeniedPayload` (+ `AccessDeniedReason`), `EscalatedPayload`, `HandoffResolvedPayload`, `RunClosedPayload`, `HandoffCreatedPayload`.
  - Clases de evento: `RunStarted`, `TurnStarted`, `CommandEmitted`, `NodeEntered`, `DecisionMade`, `RuleEvaluated`, `ToolCalled`, `StepUpRequested`, `ActionConfirmed`, `ActionCancelled`, `ActionDispatched`, `ActionVerified`, `ExpiryEvaluated`, `ResponseEmitted`, `TurnCompleted`, `InjectionFlagged`, `AccessDenied`, `Escalated`, `HandoffResolved`, `RunClosed`.
  - `AnyEvent`, `EVENT_TYPES: Mapping[str, type[EngineEvent]]`, `EVENT_EMITTERS: Mapping[str, frozenset[str]]`, `MEASURED_FIELDS: Mapping[str, frozenset[str]]`.

- [ ] **Step 1: Escribir las muestras**

`tests/m00/samples.py`:

```python
"""Un payload de ejemplo por tipo de evento (datos sintéticos)."""

from decimal import Decimal
from typing import Any

TS = "2026-09-28T12:00:00Z"
FP = {"alg": "HMAC-SHA256", "kid": "demo-fp-1", "value": "ab" * 32}

SAMPLE_PAYLOADS: dict[str, dict[str, Any]] = {
    "run_started": {"agent": "atencion@1.0.0", "mode": "conversational", "subject_kind": "customer",
                    "principal_type": "customer", "locale": "es", "reportable_attrs": {"country": "CO"}},
    "turn_started": {"client_turn_id": "c-1", "guards": {
        "lang": {"detector": "lingua@1.3.5", "letters": 20,
                 "top2": [{"lang": "es", "score": 0.9}, {"lang": "pt", "score": 0.1}],
                 "decision": "kept", "locale_prior": "es", "locale": "es"},
        "injection": {"flagged": False, "signals": [], "ruleset": "injection-rules@1.0.0"},
        "size_ok": True}},
    "command_emitted": {"command": "start_flow", "flow": "disputa-cargo", "interrupt": None,
                        "additional_flows": [], "above_threshold": {"command": True, "flow": True},
                        "decision_id": "decision-0001", "source": "understand"},
    "node_entered": {"flow": "disputa-cargo@1.0.0", "node_id": "pedir_cargo", "node_type": "collect",
                     "resume_kind": "none"},
    "decision_made": {"decision_id": "decision-0001", "model": "match-cargo@2.0.0",
                      "provider_used": "classifier", "model_version": "clf-demo-1", "fallback_depth": 1,
                      "value": {"match": "unica"}, "p_cal": {"match": 0.91}, "p_raw": {"match": 0.88},
                      "top_k": {"match": [{"label": "unica", "p": 0.91}]}, "above_threshold": {"match": True},
                      "latency_ms": 12, "tokens": 0, "cost_usd": "0.0000", "locale": "es"},
    "rule_evaluated": {"node_id": "umbral", "policy": "escalamiento-disputa-monto@1.0.0",
                       "inputs": {"facts.monto_usd.value": Decimal("120.50")}, "result": False},
    "tool_called": {"node_id": "buscar_tx", "tool": "buscar_transacciones@1.0.0", "call_id": "call-0001",
                    "status": "ok", "args": {"texto": "⟦txt:1⟧"}, "result": {"count": 2}, "result_fp": FP,
                    "error": None, "attempt": 1, "action_id": None, "latency_ms": 35},
    "step_up_requested": {"node_id": "radicar", "required_level": "step_up", "attempt": 1},
    "action_confirmed": {"action_id": "action-0001", "source": "button"},
    "action_cancelled": {"action_id": "action-0001", "reason": "token_expired"},
    "action_dispatched": {"action_id": "action-0001", "tool": "radicar_pqr@1.0.0", "args_hash": "0" * 64},
    "action_verified": {"action_id": "action-0001", "result": "verified", "readback_call_id": "call-0002"},
    "expiry_evaluated": {"now": TS, "last_activity_at": "2026-09-28T11:00:00Z", "ttl": "PT30M",
                         "expired": True},
    "response_emitted": {"node_id": "responder_ok", "kind": "generated",
                         "validator": {"ok": True, "failures": [], "regenerations": 0}, "fallback_used": False,
                         "claims": ["confirmar"], "transcript_fp": FP,
                         "llm": {"calls": 1, "latency_ms": 800, "tokens": 420, "cost_usd": "0.0021",
                                 "models": ["llm-demo"]}},
    "turn_completed": {"client_turn_id": "c-1", "entry": "turn", "duration_ms": 950,
                       "stages": {"guards_ms": 3, "understand_ms": 120, "flow_ms": 60, "response_ms": 700},
                       "degraded": False, "awaiting": "none"},
    "injection_flagged": {"signals": ["ignore-instructions"], "ruleset": "injection-rules@1.0.0",
                          "scope": "user_text"},
    "access_denied": {"reason": "principal_mismatch", "tool": None},
    "escalated": {"reason_code": "policy:escalamiento-disputa-monto", "target_queue": "disputas",
                  "priority": "high", "handoff_ref": "handoff-0001"},
    "handoff_resolved": {"handoff_ref": "handoff-0001", "resolution_code": "resuelto",
                         "handoff_quality": "useful", "reader_type": "advisor"},
    "run_closed": {"outcome": "escalated", "closed_by": "escalation"},
}


def make_event(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_id": "event-0001",
        "type": event_type,
        "run_id": "run-0001",
        "turn_id": "turn-0001",
        "session_id": "session-0001",
        "release": "rel-2026-09-28",
        "ts": TS,
        "payload": SAMPLE_PAYLOADS[event_type] if payload is None else payload,
    }


def reverse_keys(value: Any) -> Any:
    """Mismo dato con las claves de cada objeto en orden inverso."""
    if isinstance(value, dict):
        return {k: reverse_keys(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_keys(v) for v in value]
    return value
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/m00/test_events.py`:

```python
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core.domain.events import EVENT_EMITTERS, EVENT_TYPES, MEASURED_FIELDS, AnyEvent, EngineEvent
from agent_core.domain.json import canonical_bytes, sha256_hex
from tests.m00.samples import SAMPLE_PAYLOADS, make_event, reverse_keys

EVENTS = TypeAdapter(AnyEvent)
INDEX = Path(__file__).resolve().parents[2] / "docs" / "specs" / "motor" / "00-indice.md"


def test_samples_cover_every_event_type() -> None:
    assert set(SAMPLE_PAYLOADS) == set(EVENT_TYPES)


# T-M0-04
@pytest.mark.parametrize("event_type", sorted(SAMPLE_PAYLOADS))
def test_event_canonical_hash_is_stable(event_type: str) -> None:
    raw = make_event(event_type)
    event = EVENTS.validate_python(raw)
    assert isinstance(event, EVENT_TYPES[event_type])
    reordered = EVENTS.validate_python(reverse_keys(raw))
    assert canonical_bytes(event) == canonical_bytes(reordered)
    assert canonical_bytes(raw) == canonical_bytes(reverse_keys(raw))
    assert len(sha256_hex(canonical_bytes(event))) == 64


def test_events_are_frozen_and_typed() -> None:
    event = EVENTS.validate_python(make_event("run_closed"))
    assert isinstance(event, EngineEvent)
    with pytest.raises(ValidationError):
        event.run_id = "otro"
    with pytest.raises(ValidationError):
        EVENTS.validate_python(make_event("run_closed", {"outcome": "resolved", "closed_by": "magia"}))


def _index_rows() -> list[tuple[str, str]]:
    text = INDEX.read_text(encoding="utf-8")
    section = text.split("## 6. Eventos y su emisor", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if line.startswith("| `"):
            events_col, emitter_col = [c.strip() for c in line.strip("|").split("|")][:2]
            rows.append((events_col, emitter_col))
    return rows


def _outside_parens(text: str) -> str:
    return re.sub(r"\([^)]*\)", "", text)


# T-M0-14
def test_emitters_match_index_table() -> None:
    primary: dict[str, set[str]] = {}
    mentioned: dict[str, set[str]] = {}
    for events_col, emitter_col in _index_rows():
        for event in re.findall(r"`([a-z_]+)`", _outside_parens(events_col)):
            if event == "handoff_created":  # outbox, no va a la cadena
                continue
            primary.setdefault(event, set()).update(re.findall(r"M\d+", _outside_parens(emitter_col)))
            mentioned.setdefault(event, set()).update(re.findall(r"M\d+", emitter_col))
    assert set(EVENT_TYPES) == set(EVENT_EMITTERS) == set(primary)
    for event, emitters in EVENT_EMITTERS.items():
        assert primary[event] <= emitters <= mentioned[event], event


# T-M0-15
def test_measured_fields_exist() -> None:
    for event_type, fields in MEASURED_FIELDS.items():
        assert event_type in EVENT_TYPES
        payload_model = EVENT_TYPES[event_type].model_fields["payload"].annotation
        assert payload_model is not None
        assert fields <= set(payload_model.model_fields), event_type
    assert MEASURED_FIELDS["turn_completed"] == frozenset({"duration_ms", "stages"})
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_events.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'agent_core.domain.events'`.

- [ ] **Step 4: Implementar `events.py`**

`agent_core/domain/events.py`:

```python
"""Eventos del motor (M0 §2.10). Payloads en vista `audit`. `seq`/`prev_hash`/`hash` los asigna M11."""

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_core.domain.base import Locale, Model, NodeId, Probability, UtcDatetime
from agent_core.domain.identity import AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Command, Mode, Outcome, ReasonCodeStr
from agent_core.domain.refs import EntityRef
from agent_core.domain.shared import Fingerprint, ToolStatus
from agent_core.domain.state import InvalidationReason


class EngineEvent(Model):
    """Base de todo evento de la cadena."""

    event_id: str
    run_id: str
    turn_id: str | None = None
    session_id: str | None = None
    release: str
    ts: UtcDatetime
    seq: NonNegativeInt | None = None
    prev_hash: str | None = None
    hash: str | None = None


# --- payloads -----------------------------------------------------------------------------------------------


class RunStartedPayload(Model):
    agent: EntityRef
    mode: Mode
    subject_kind: str | None = None
    principal_type: PrincipalType
    locale: Locale
    reportable_attrs: dict[str, str] = Field(default_factory=dict)


class LangScore(Model):
    lang: str
    score: Probability


class LangGuard(Model):
    detector: str
    letters: NonNegativeInt
    top2: list[LangScore]
    decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
    locale_prior: Locale | None = None
    locale: Locale


class InjectionGuard(Model):
    flagged: bool
    signals: list[str] = Field(default_factory=list)
    ruleset: str


class GuardsOutput(Model):
    lang: LangGuard
    injection: InjectionGuard
    size_ok: bool


class TurnStartedPayload(Model):
    client_turn_id: str | None = None
    guards: GuardsOutput | None = None


class CommandEmittedPayload(Model):
    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = Field(default_factory=list)
    above_threshold: dict[str, bool] = Field(default_factory=dict)
    decision_id: str | None = None
    source: Literal["understand", "button"]


class NodeEnteredPayload(Model):
    flow: EntityRef
    node_id: NodeId
    node_type: str
    resume_kind: Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]


class LabelScore(Model):
    label: str
    p: Probability


class DecisionMadePayload(Model):
    decision_id: str
    model: EntityRef
    provider_used: str
    model_version: str
    fallback_depth: NonNegativeInt
    value: dict[str, JsonValue]
    p_cal: dict[str, Probability | None]
    p_raw: dict[str, Probability | None]
    top_k: dict[str, list[LabelScore]] = Field(default_factory=dict)
    above_threshold: dict[str, bool]
    latency_ms: NonNegativeInt
    tokens: NonNegativeInt
    cost_usd: Decimal
    locale: Locale


class RuleEvaluatedPayload(Model):
    node_id: NodeId
    policy: EntityRef | None = None
    inputs: dict[str, JsonValue]
    result: bool


class ToolCalledPayload(Model):
    node_id: NodeId
    tool: EntityRef
    call_id: str
    status: ToolStatus
    args: dict[str, JsonValue]
    result: JsonValue = None
    result_fp: Fingerprint | None = None
    error: str | None = None
    attempt: PositiveInt = 1
    action_id: str | None = None
    latency_ms: NonNegativeInt


class StepUpRequestedPayload(Model):
    node_id: NodeId
    required_level: AuthLevel
    attempt: PositiveInt


class ActionConfirmedPayload(Model):
    action_id: str
    source: Literal["understand", "button"]


class ActionCancelledPayload(Model):
    action_id: str
    reason: InvalidationReason


class ActionDispatchedPayload(Model):
    action_id: str
    tool: EntityRef
    args_hash: str


class ActionVerifiedPayload(Model):
    action_id: str
    result: Literal["verified", "failed"]
    readback_call_id: str


class ExpiryEvaluatedPayload(Model):
    now: UtcDatetime
    last_activity_at: UtcDatetime
    ttl: timedelta
    expired: bool


class ValidatorOutcome(Model):
    ok: bool
    failures: list[str] = Field(default_factory=list)
    regenerations: NonNegativeInt = 0


class LlmUsage(Model):
    calls: NonNegativeInt
    latency_ms: NonNegativeInt
    tokens: NonNegativeInt
    cost_usd: Decimal
    models: list[str] = Field(default_factory=list)


class ResponseEmittedPayload(Model):
    node_id: NodeId | None = None
    kind: Literal["template", "generated"]
    validator: ValidatorOutcome
    fallback_used: bool
    claims: list[str] = Field(default_factory=list)
    transcript_fp: Fingerprint | None = None
    llm: LlmUsage | None = None


class TurnStages(Model):
    guards_ms: NonNegativeInt | None = None
    understand_ms: NonNegativeInt | None = None
    flow_ms: NonNegativeInt | None = None
    response_ms: NonNegativeInt | None = None


class TurnCompletedPayload(Model):
    client_turn_id: str | None = None
    entry: Literal["start_run", "turn"]
    duration_ms: NonNegativeInt
    stages: TurnStages
    degraded: bool
    awaiting: Awaiting


class InjectionFlaggedPayload(Model):
    signals: list[str]
    ruleset: str
    scope: Literal["user_text", "untrusted_field"]


class AccessDeniedReason(StrEnum):
    principal_expired = "principal_expired"
    delegation_expired = "delegation_expired"
    delegation_mismatch = "delegation_mismatch"
    principal_mismatch = "principal_mismatch"
    subject_forbidden = "subject_forbidden"
    agent_forbidden = "agent_forbidden"
    tool_denied = "tool_denied"


class AccessDeniedPayload(Model):
    reason: AccessDeniedReason
    tool: EntityRef | None = None


class EscalatedPayload(Model):
    reason_code: ReasonCodeStr
    target_queue: str
    priority: str
    handoff_ref: str


class HandoffResolvedPayload(Model):
    handoff_ref: str
    resolution_code: str
    handoff_quality: Literal["useful", "incomplete", "unnecessary"]
    reader_type: PrincipalType


class RunClosedPayload(Model):
    outcome: Outcome
    closed_by: Literal["flow", "abandonment", "escalation", "revocation"]


class HandoffCreatedPayload(Model):
    """Evento saliente (outbox, no va a la cadena)."""

    handoff_ref: str
    run_id: str
    target_queue: str
    priority: str
    reason_code: ReasonCodeStr
    language: Locale
    reportable_attrs: dict[str, str] = Field(default_factory=dict)


# --- eventos -------------------------------------------------------------------------------------------------


class RunStarted(EngineEvent):
    type: Literal["run_started"] = "run_started"
    payload: RunStartedPayload


class TurnStarted(EngineEvent):
    type: Literal["turn_started"] = "turn_started"
    payload: TurnStartedPayload


class CommandEmitted(EngineEvent):
    type: Literal["command_emitted"] = "command_emitted"
    payload: CommandEmittedPayload


class NodeEntered(EngineEvent):
    type: Literal["node_entered"] = "node_entered"
    payload: NodeEnteredPayload


class DecisionMade(EngineEvent):
    type: Literal["decision_made"] = "decision_made"
    payload: DecisionMadePayload


class RuleEvaluated(EngineEvent):
    type: Literal["rule_evaluated"] = "rule_evaluated"
    payload: RuleEvaluatedPayload


class ToolCalled(EngineEvent):
    type: Literal["tool_called"] = "tool_called"
    payload: ToolCalledPayload


class StepUpRequested(EngineEvent):
    type: Literal["step_up_requested"] = "step_up_requested"
    payload: StepUpRequestedPayload


class ActionConfirmed(EngineEvent):
    type: Literal["action_confirmed"] = "action_confirmed"
    payload: ActionConfirmedPayload


class ActionCancelled(EngineEvent):
    type: Literal["action_cancelled"] = "action_cancelled"
    payload: ActionCancelledPayload


class ActionDispatched(EngineEvent):
    type: Literal["action_dispatched"] = "action_dispatched"
    payload: ActionDispatchedPayload


class ActionVerified(EngineEvent):
    type: Literal["action_verified"] = "action_verified"
    payload: ActionVerifiedPayload


class ExpiryEvaluated(EngineEvent):
    type: Literal["expiry_evaluated"] = "expiry_evaluated"
    payload: ExpiryEvaluatedPayload


class ResponseEmitted(EngineEvent):
    type: Literal["response_emitted"] = "response_emitted"
    payload: ResponseEmittedPayload


class TurnCompleted(EngineEvent):
    type: Literal["turn_completed"] = "turn_completed"
    payload: TurnCompletedPayload


class InjectionFlagged(EngineEvent):
    type: Literal["injection_flagged"] = "injection_flagged"
    payload: InjectionFlaggedPayload


class AccessDenied(EngineEvent):
    type: Literal["access_denied"] = "access_denied"
    payload: AccessDeniedPayload


class Escalated(EngineEvent):
    type: Literal["escalated"] = "escalated"
    payload: EscalatedPayload


class HandoffResolved(EngineEvent):
    type: Literal["handoff_resolved"] = "handoff_resolved"
    payload: HandoffResolvedPayload


class RunClosed(EngineEvent):
    type: Literal["run_closed"] = "run_closed"
    payload: RunClosedPayload


AnyEvent = Annotated[
    RunStarted
    | TurnStarted
    | CommandEmitted
    | NodeEntered
    | DecisionMade
    | RuleEvaluated
    | ToolCalled
    | StepUpRequested
    | ActionConfirmed
    | ActionCancelled
    | ActionDispatched
    | ActionVerified
    | ExpiryEvaluated
    | ResponseEmitted
    | TurnCompleted
    | InjectionFlagged
    | AccessDenied
    | Escalated
    | HandoffResolved
    | RunClosed,
    Field(discriminator="type"),
]

_EVENT_CLASSES: tuple[type[EngineEvent], ...] = (
    RunStarted, TurnStarted, CommandEmitted, NodeEntered, DecisionMade, RuleEvaluated, ToolCalled,
    StepUpRequested, ActionConfirmed, ActionCancelled, ActionDispatched, ActionVerified, ExpiryEvaluated,
    ResponseEmitted, TurnCompleted, InjectionFlagged, AccessDenied, Escalated, HandoffResolved, RunClosed,
)

EVENT_TYPES: Mapping[str, type[EngineEvent]] = MappingProxyType(
    {cls.model_fields["type"].default: cls for cls in _EVENT_CLASSES}
)

EVENT_EMITTERS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "run_started": frozenset({"M4"}),
        "turn_started": frozenset({"M4"}),
        "command_emitted": frozenset({"M4"}),
        "expiry_evaluated": frozenset({"M4"}),
        "turn_completed": frozenset({"M4"}),
        "run_closed": frozenset({"M4"}),
        "node_entered": frozenset({"M2"}),
        "rule_evaluated": frozenset({"M2"}),
        "step_up_requested": frozenset({"M2"}),
        "tool_called": frozenset({"M2", "M3"}),
        "decision_made": frozenset({"M5"}),
        "action_confirmed": frozenset({"M3"}),
        "action_cancelled": frozenset({"M3"}),
        "action_dispatched": frozenset({"M3"}),
        "action_verified": frozenset({"M3"}),
        "response_emitted": frozenset({"M8"}),
        "injection_flagged": frozenset({"M6"}),
        "access_denied": frozenset({"M9", "M2"}),
        "escalated": frozenset({"M10"}),
        "handoff_resolved": frozenset({"M10"}),
    }
)

MEASURED_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decision_made": frozenset({"latency_ms"}),
        "tool_called": frozenset({"latency_ms"}),
        "response_emitted": frozenset({"llm"}),
        "turn_completed": frozenset({"duration_ms", "stages"}),
    }
)
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/m00/test_events.py -v`
Esperado: todas PASS. Si `test_emitters_match_index_table` falla, el índice §6 y `EVENT_EMITTERS` divergen. **No ajustes la prueba**: corrige el lado equivocado según M0 §2.10 y, si la duda es del spec, detente y pregunta.

- [ ] **Step 6: Lint, tipos y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain/events.py tests/m00/samples.py tests/m00/test_events.py
git commit -m "feat(m0): catálogo de eventos con payloads, emisores y campos de medición

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Interfaz pública de `domain`, puertos, adaptadores de sistema y lint de tiempo

**Files:**
- Create: `agent_core/domain/version.py`; `agent_core/ports/{clock,ids,registry,tools,authz,identity,uow,audit,llm,transcript,knowledge,keys,costs}.py`; `agent_core/adapters/system_clock.py`, `agent_core/adapters/system_ids.py`
- Modify: `agent_core/domain/__init__.py`, `agent_core/ports/__init__.py`
- Test: `tests/m00/test_public_api.py`, `tests/m00/test_lint_rules.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `SCHEMA_VERSION = "0.1.0"` y la reexportación completa de `agent_core.domain`;
  - los Protocols `Clock`, `IdSource` (+ `IdKind`), `RegistryPort`, `ToolExecutor` (+ `ToolCallContext`, `ToolResult`; reexporta `ToolStatus`), `AuthzPort` (+ `AuthzDecision`), `IdentityVerifier`, `UnitOfWork`, `UnitOfWorkFactory`, `AuditSink`, `Outbox`, `LLMGateway` (+ `GenerationResult`), `TranscriptStore`, `KnowledgeSource` (provisional), `KeyProvider` (+ `KeyPurpose`), `CostCounters`;
  - `SystemClock` y `SystemIds`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_public_api.py`:

```python
import agent_core.domain as domain
import agent_core.ports as ports


def test_domain_exports() -> None:
    for name in ["RunState", "Principal", "EntityRef", "RefSpec", "Node", "Flow", "Agent", "Release", "ToolDef",
                 "AnyEvent", "EngineEvent", "EngineError", "ProblemCode", "canonical_bytes", "loads", "dumps",
                 "EscalationRequest", "RejectedDraft", "ConfirmationPrompt", "TurnResult", "SCHEMA_VERSION"]:
        assert hasattr(domain, name), name
    assert domain.SCHEMA_VERSION == "0.1.0"


def test_ports_exports() -> None:
    for name in ["Clock", "IdSource", "IdKind", "RegistryPort", "ToolExecutor", "ToolResult", "ToolStatus",
                 "ToolCallContext", "AuthzPort", "AuthzDecision", "IdentityVerifier", "UnitOfWork",
                 "UnitOfWorkFactory", "AuditSink", "Outbox", "LLMGateway", "GenerationResult", "TranscriptStore",
                 "KnowledgeSource", "KeyProvider", "KeyPurpose", "CostCounters"]:
        assert hasattr(ports, name), name
```

`tests/m00/test_lint_rules.py`:

```python
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ruff(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--config", str(ROOT / "pyproject.toml"),
         "--select", "TID251", str(path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )


# T-M0-06
def test_banned_time_and_randomness_outside_adapters(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import random\nimport time\nimport uuid\nfrom datetime import datetime\n\n"
        "a = datetime.now()\nb = uuid.uuid4()\nc = time.monotonic_ns()\nd = random.random()\n",
        encoding="utf-8",
    )
    result = _ruff(offender)
    assert result.returncode != 0
    for banned in ("datetime.datetime.now", "uuid.uuid4", "time.monotonic_ns", "random"):
        assert banned in result.stdout, banned


def test_system_adapters_are_exempt() -> None:
    for name in ("system_clock.py", "system_ids.py"):
        result = _ruff(ROOT / "agent_core" / "adapters" / name)
        assert result.returncode == 0, result.stdout
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_public_api.py tests/m00/test_lint_rules.py -v`
Esperado: FAIL (faltan exports y los adaptadores).

- [ ] **Step 3: `version.py` y `domain/__init__.py`**

`agent_core/domain/version.py`:

```python
"""Versión semver de los contratos publicados (ADR 0002). `agentcore contracts` la escribe en contracts/VERSION."""

SCHEMA_VERSION = "0.1.0"
```

`agent_core/domain/__init__.py`:

```python
"""M0 — dominio: tipos, esquemas de nodos, eventos, errores y serialización canónica."""

from agent_core.domain.base import (
    EntityId, ExactVersion, Locale, Model, MutableModel, NodeId, Probability, UtcDatetime,
)
from agent_core.domain.entities import (
    ENTITY_KIND, Agent, Budgets, CalibrationRef, DecisionModelDef, EngineTemplates, EscalateAction, Flow,
    InjectionRule, InjectionRuleset, Interrupt, LanguageDetection, Policy, Prompt, ProviderSpec, RegistryEntity,
    Release, RiskClass, StartFlowAction, Template, ToolDef,
)
from agent_core.domain.errors import (
    PROBLEM_STATUS, CredentialsInvalid, DomainError, EngineError, IllegalTransition, InvalidRuntimeRef,
    ProblemCode, SchemaError, TurnInProgress, VersionConflict,
)
from agent_core.domain.events import (
    EVENT_EMITTERS, EVENT_TYPES, MEASURED_FIELDS, AnyEvent, EngineEvent, HandoffCreatedPayload, LlmUsage,
    TurnStages,
)
from agent_core.domain.identity import (
    AuthInfo, AuthLevel, OnBehalfOf, Principal, PrincipalKey, PrincipalType, SubjectRef,
)
from agent_core.domain.json import JsonValue, canonical_bytes, dumps, loads, sha256_hex, to_jsonable
from agent_core.domain.nodes import (
    MVP_NODE_KINDS, PRODUCTION_NODE_KINDS, RESULTS, TERMINAL, WAITING, Node, node_kind,
)
from agent_core.domain.outcomes import (
    DECLARABLE, Awaiting, Command, Mode, Outcome, ReasonCode, ReasonCodeStr, is_declarable,
)
from agent_core.domain.refs import (
    AgentSelector, EntityKind, EntityRef, RefSpec, iter_refspecs, require_exact_refs,
)
from agent_core.domain.shared import (
    EscalationRequest, Fingerprint, OutboxMessage, RejectedDraft, ToolStatus, TranscriptEntry, TranscriptRef,
)
from agent_core.domain.state import (
    Action, ActionState, ActiveFlow, BudgetsUsed, Decision, EncryptedBlob, Fact, FactSource, InvalidationReason,
    PendingIntent, RunState, RunStatus, Slot,
)
from agent_core.domain.turn import (
    ConfirmAnswer, ConfirmationPrompt, Message, RunInput, RunResult, StepUpPrompt, TurnInput, TurnResult,
)
from agent_core.domain.version import SCHEMA_VERSION

__all__ = [
    "DECLARABLE", "ENTITY_KIND", "EVENT_EMITTERS", "EVENT_TYPES", "MEASURED_FIELDS", "MVP_NODE_KINDS",
    "PROBLEM_STATUS", "PRODUCTION_NODE_KINDS", "RESULTS", "SCHEMA_VERSION", "TERMINAL", "WAITING",
    "Action", "ActionState", "ActiveFlow", "Agent", "AgentSelector", "AnyEvent", "AuthInfo", "AuthLevel",
    "Awaiting", "Budgets", "BudgetsUsed", "CalibrationRef", "Command", "ConfirmAnswer", "ConfirmationPrompt",
    "CredentialsInvalid", "Decision", "DecisionModelDef", "DomainError", "EncryptedBlob", "EngineError",
    "EngineEvent", "EngineTemplates", "EntityId", "EntityKind", "EntityRef", "EscalateAction",
    "EscalationRequest", "ExactVersion", "Fact", "FactSource", "Fingerprint", "Flow", "HandoffCreatedPayload",
    "IllegalTransition", "InjectionRule", "InjectionRuleset", "Interrupt", "InvalidRuntimeRef",
    "InvalidationReason", "JsonValue", "LanguageDetection", "LlmUsage", "Locale", "Message", "Mode", "Model",
    "MutableModel", "Node", "NodeId", "OnBehalfOf", "Outcome", "OutboxMessage", "PendingIntent", "Policy",
    "Principal", "PrincipalKey", "PrincipalType", "Probability", "ProblemCode", "Prompt", "ProviderSpec",
    "ReasonCode", "ReasonCodeStr", "RefSpec", "RegistryEntity", "RejectedDraft", "Release", "RiskClass",
    "RunInput", "RunResult", "RunState", "RunStatus", "SchemaError", "Slot", "StartFlowAction", "StepUpPrompt",
    "SubjectRef", "Template", "ToolDef", "ToolStatus", "TranscriptEntry", "TranscriptRef", "TurnInProgress",
    "TurnInput", "TurnResult", "TurnStages", "UtcDatetime", "VersionConflict", "canonical_bytes", "dumps",
    "is_declarable", "iter_refspecs", "loads", "node_kind", "require_exact_refs", "sha256_hex", "to_jsonable",
]
```

Si ruff (`I001`) reordena los imports, acepta el cambio con `uv run ruff check . --fix`.

- [ ] **Step 4: Puertos**

`agent_core/ports/clock.py`:

```python
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Instante UTC con zona. Única fuente de tiempo para decidir."""
        ...

    def monotonic_ns(self) -> int:
        """Solo para medir duraciones (MEASURED_FIELDS). Nunca decide nada."""
        ...
```

`agent_core/ports/ids.py`:

```python
from enum import StrEnum
from typing import Protocol


class IdKind(StrEnum):
    run = "run"
    session = "session"
    turn = "turn"
    action = "action"
    decision = "decision"
    fact = "fact"
    call = "call"
    handoff = "handoff"
    event = "event"
    message = "message"


class IdSource(Protocol):
    def new_id(self, kind: IdKind) -> str:
        """ID opaco y único por `kind`. Ningún módulo genera IDs por su cuenta."""
        ...

    def secret_token(self) -> str:
        """Base64 url-safe sin relleno de al menos 16 bytes (128 bits). Solo tokens de confirmación."""
        ...
```

`agent_core/ports/registry.py`:

```python
from typing import Literal, Protocol

from pydantic import BaseModel

from agent_core.domain.entities import Release
from agent_core.domain.identity import Principal
from agent_core.domain.refs import AgentSelector, EntityRef


class RegistryPort(Protocol):
    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release: ...

    def release_status(self, release_id: str) -> Literal["active", "revoked"]: ...

    def get[T: BaseModel](self, ref: EntityRef, kind: type[T]) -> T:
        """Entidad exacta. Una referencia no exacta en su contenido lanza `InvalidRuntimeRef`."""
        ...
```

`agent_core/ports/tools.py`:

```python
from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.entities import ToolDef
from agent_core.domain.identity import AuthLevel, OnBehalfOf, Principal, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.refs import EntityRef
from agent_core.domain.shared import ToolStatus

__all__ = ["ToolCallContext", "ToolExecutor", "ToolResult", "ToolStatus"]


class ToolCallContext(Model):
    run_id: str
    release: str
    principal: Principal
    on_behalf_of: OnBehalfOf | None = None
    subject: SubjectRef | None = None
    turn_id: str | None = None


class ToolResult(Model):
    """Solo `result_full` y su `source`: las vistas `model`/`audit` las calcula M7 (M0 rev. 2)."""

    status: ToolStatus
    result_full: JsonValue = None
    source: str | None = None
    call_id: str
    error: str | None = None
    required_level: AuthLevel | None = None


class ToolExecutor(Protocol):
    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        """Lectura/compute: ok|error|timeout|denied|step_up_required. Escritura: ok|denied|uncertain|
        step_up_required, y step_up_required siempre antes de cualquier efecto."""
        ...

    def definition(self, tool: EntityRef) -> ToolDef: ...
```

`agent_core/ports/authz.py`:

```python
from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.entities import Agent
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef


class AuthzDecision(Model):
    allowed: bool
    reason: str | None = None


class AuthzPort(Protocol):
    def authorize_agent(self, principal: Principal, agent: Agent, subject: SubjectRef | None) -> AuthzDecision: ...

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision: ...

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]: ...

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool: ...

    def reportable_attrs(self) -> frozenset[str]: ...
```

`agent_core/ports/identity.py`:

```python
from datetime import datetime
from typing import Protocol

from agent_core.domain.identity import OnBehalfOf, Principal


class IdentityVerifier(Protocol):
    def verify(self, raw_credential: str) -> Principal:
        """Solo valida la firma (`CredentialsInvalid`); NO chequea `exp`: eso lo hace M9 con el Clock."""
        ...

    def verify_delegation(self, raw: str) -> OnBehalfOf: ...

    def grant_active(self, grant_ref: str, now: datetime) -> bool: ...
```

`agent_core/ports/uow.py`:

```python
from collections.abc import Callable
from datetime import datetime, timedelta
from types import TracebackType
from typing import Protocol, Self

from agent_core.domain.events import EngineEvent
from agent_core.domain.identity import PrincipalKey
from agent_core.domain.json import JsonValue
from agent_core.domain.shared import OutboxMessage
from agent_core.domain.state import RunState
from agent_core.domain.turn import RunResult, TurnResult


class UnitOfWork(Protocol):
    """Una instancia = una transacción. Sin `commit()` todo se descarta, salvo el lease de `acquire_turn`."""

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None: ...

    def acquire_turn(self, run_id: str, turn_id: str, now: datetime, ttl: timedelta) -> None:
        """Visible de inmediato. Otro lease vigente → `TurnInProgress`."""
        ...

    def release_turn(self, run_id: str, turn_id: str) -> None:
        """Se aplica con `commit()`."""
        ...

    def load_run(self, run_id: str) -> RunState | None: ...

    def find_run_by_session(self, session_id: str) -> RunState | None: ...

    def save_run(self, state: RunState, expected_version: int) -> RunState:
        """Versión distinta → `VersionConflict`. Devuelve el estado con `state_version = expected_version + 1`."""
        ...

    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None: ...

    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None: ...

    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None: ...

    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str, result: RunResult) -> None: ...

    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None: ...

    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None: ...

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None: ...

    def last_event(self, run_id: str) -> EngineEvent | None: ...

    def enqueue_outbox(self, message: OutboxMessage) -> None: ...

    def list_inactive(self, now: datetime, limit: int) -> list[str]:
        """run_ids `open` con `inactive_after < now`, ordenados por `inactive_after`."""
        ...

    def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
```

`agent_core/ports/audit.py`:

```python
from typing import Protocol

from agent_core.domain.events import EngineEvent
from agent_core.domain.shared import OutboxMessage


class AuditSink(Protocol):
    """Lectura y appends fuera de un turno; la escritura del turno va por la UoW."""

    def read(self, run_id: str) -> list[EngineEvent]: ...

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None: ...


class Outbox(Protocol):
    """Lo consume la unidad 4; se escribe por la UoW."""

    def pending(self, limit: int) -> list[OutboxMessage]: ...

    def mark_delivered(self, message_id: str) -> None: ...
```

`agent_core/ports/llm.py`:

```python
from decimal import Decimal
from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.json import JsonValue
from agent_core.domain.refs import EntityRef


class GenerationResult(Model):
    output: JsonValue
    tokens: int
    cost_usd: Decimal
    model: str


class LLMGateway(Protocol):
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: str,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult: ...
```

`agent_core/ports/transcript.py`:

```python
from typing import Protocol

from agent_core.domain.shared import TranscriptEntry


class TranscriptStore(Protocol):
    def append(self, entry: TranscriptEntry) -> str: ...

    def read(self, run_id: str) -> list[TranscriptEntry]: ...

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]: ...
```

`agent_core/ports/knowledge.py`:

```python
from typing import Protocol

from agent_core.domain.json import JsonValue


class KnowledgeSource(Protocol):
    """PROVISIONAL hasta cerrar el tema #10 (M12). No se implementa en la fase 1."""

    def capabilities(self) -> frozenset[str]: ...

    def read(self, path: str, snapshot: str, view: str) -> dict[str, JsonValue] | None: ...
```

`agent_core/ports/keys.py`:

```python
from enum import StrEnum
from typing import Protocol


class KeyPurpose(StrEnum):
    fingerprint = "fingerprint"
    token_map = "token_map"


class KeyProvider(Protocol):
    """Claves distintas por propósito: nunca la misma clave para HMAC y cifrado."""

    def current_kid(self, purpose: KeyPurpose) -> str: ...

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        """`kid` desconocido → `KeyError`."""
        ...
```

`agent_core/ports/costs.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from agent_core.domain.identity import PrincipalKey


class CostCounters(Protocol):
    def spent_today(self, principal: PrincipalKey, now: datetime) -> Decimal: ...

    def hits(self, principal: PrincipalKey, window: timedelta, now: datetime) -> int: ...
```

`agent_core/ports/__init__.py`:

```python
"""M0 — puertos hacia las unidades 2–7 (typing.Protocol, síncronos)."""

from agent_core.ports.audit import AuditSink, Outbox
from agent_core.ports.authz import AuthzDecision, AuthzPort
from agent_core.ports.clock import Clock
from agent_core.ports.costs import CostCounters
from agent_core.ports.identity import IdentityVerifier
from agent_core.ports.ids import IdKind, IdSource
from agent_core.ports.keys import KeyProvider, KeyPurpose
from agent_core.ports.knowledge import KnowledgeSource
from agent_core.ports.llm import GenerationResult, LLMGateway
from agent_core.ports.registry import RegistryPort
from agent_core.ports.tools import ToolCallContext, ToolExecutor, ToolResult, ToolStatus
from agent_core.ports.transcript import TranscriptStore
from agent_core.ports.uow import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AuditSink", "AuthzDecision", "AuthzPort", "Clock", "CostCounters", "GenerationResult", "IdKind",
    "IdSource", "IdentityVerifier", "KeyProvider", "KeyPurpose", "KnowledgeSource", "LLMGateway", "Outbox",
    "RegistryPort", "ToolCallContext", "ToolExecutor", "ToolResult", "ToolStatus", "TranscriptStore",
    "UnitOfWork", "UnitOfWorkFactory",
]
```

- [ ] **Step 5: Adaptadores de sistema**

`agent_core/adapters/system_clock.py`:

```python
"""Único lugar del repo que lee la hora del sistema (M0 §3)."""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


if TYPE_CHECKING:
    from agent_core.ports.clock import Clock

    def _conforms(x: SystemClock) -> Clock:
        return x
```

`agent_core/adapters/system_ids.py`:

```python
"""Único lugar del repo que usa aleatoriedad: UUIDv7 para IDs y `secrets` para tokens (M0 §3)."""

import secrets
import time
import uuid
from typing import TYPE_CHECKING

from agent_core.ports.ids import IdKind


def _uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    rand = secrets.token_bytes(10)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= (int.from_bytes(rand[:2], "big") & 0x0FFF) << 64
    value |= 0b10 << 62
    value |= int.from_bytes(rand[2:], "big") & ((1 << 62) - 1)
    return uuid.UUID(int=value)


class SystemIds:
    def new_id(self, kind: IdKind) -> str:
        return str(_uuid7())

    def secret_token(self) -> str:
        return secrets.token_urlsafe(16)


if TYPE_CHECKING:
    from agent_core.ports.ids import IdSource

    def _conforms(x: SystemIds) -> IdSource:
        return x
```

- [ ] **Step 6: Correr las pruebas**

Run: `uv run pytest tests/m00 -v`
Esperado: todas PASS, incluidas las de las tareas anteriores.

- [ ] **Step 7: Lint, tipos, fronteras y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add agent_core/domain agent_core/ports agent_core/adapters tests/m00/test_public_api.py tests/m00/test_lint_rules.py
git commit -m "feat(m0): puertos, interfaz pública, SystemClock/SystemIds y lint de tiempo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Dobles de tiempo, IDs y claves, con sus suites de contrato

**Files:**
- Create: `testing/fakes/clock.py`, `testing/fakes/ids.py`, `testing/fakes/keys.py`, `agent_core/adapters/env_keys.py`
- Test: `tests/contracts/test_clock_contract.py`, `tests/contracts/test_ids_contract.py`, `tests/contracts/test_keys_contract.py`

**Interfaces:**
- Consumes: `Clock`, `IdSource`, `IdKind`, `KeyProvider`, `KeyPurpose`, `SystemClock`, `SystemIds` (Task 8).
- Produces:
  - `FakeClock(start: datetime = NOW)` con `.now()`, `.monotonic_ns()` y `.advance(delta: timedelta)`;
  - `FakeIds(seed: Mapping[IdKind, Sequence[str]] | None = None, token_seed: str = "fake")`;
  - `FakeKeyProvider(keys, current)`, con `.default()` y `.rotate(purpose, kid, key)`;
  - `EnvKeyProvider(env: Mapping[str, str])`, con `LABEL = "demo-secret-env"` y las variables `AGENTCORE_KEYS_FINGERPRINT` y `AGENTCORE_KEYS_TOKEN_MAP`.

- [ ] **Step 1: Escribir las suites de contrato**

`tests/contracts/test_clock_contract.py`:

```python
from datetime import timedelta

import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.ports import Clock
from testing.fakes.clock import FakeClock


@pytest.fixture(params=["fake", "system"])
def clock(request: pytest.FixtureRequest) -> Clock:
    return FakeClock() if request.param == "fake" else SystemClock()


def test_now_is_utc_aware(clock: Clock) -> None:
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_monotonic_never_decreases(clock: Clock) -> None:
    first = clock.monotonic_ns()
    assert clock.monotonic_ns() >= first


def test_fake_advance_moves_both_clocks_together() -> None:
    clock = FakeClock()
    start, mono = clock.now(), clock.monotonic_ns()
    clock.advance(timedelta(milliseconds=1500))
    assert clock.now() - start == timedelta(milliseconds=1500)
    assert clock.monotonic_ns() - mono == 1_500_000_000
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
```

`tests/contracts/test_ids_contract.py`:

```python
import base64

import pytest

from agent_core.adapters.system_ids import SystemIds
from agent_core.ports import IdKind, IdSource
from testing.fakes.ids import FakeIds


@pytest.fixture(params=["fake", "system"])
def ids(request: pytest.FixtureRequest) -> IdSource:
    return FakeIds() if request.param == "fake" else SystemIds()


def test_ids_unique_per_kind(ids: IdSource) -> None:
    generated = [ids.new_id(IdKind.action) for _ in range(200)]
    assert len(set(generated)) == 200


def test_secret_token_has_128_bits(ids: IdSource) -> None:
    token = ids.secret_token()
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(raw) >= 16
    assert ids.secret_token() != token


def test_fake_ids_reproducible_and_seedable() -> None:
    a, b = FakeIds(), FakeIds()
    assert [a.new_id(IdKind.fact) for _ in range(3)] == [b.new_id(IdKind.fact) for _ in range(3)]
    assert a.secret_token() == b.secret_token()
    seeded = FakeIds(seed={IdKind.action: ["action-grabado-1"]})
    assert seeded.new_id(IdKind.action) == "action-grabado-1"
    assert seeded.new_id(IdKind.action) == "action-0001"
    assert FakeIds().new_id(IdKind.call) == "call-0001"
```

`tests/contracts/test_keys_contract.py`:

```python
import base64

import pytest

from agent_core.adapters.env_keys import EnvKeyProvider
from agent_core.ports import KeyProvider, KeyPurpose
from testing.fakes.keys import FakeKeyProvider


def _b64(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


ENV = {
    "AGENTCORE_KEYS_FINGERPRINT": f"fp-2:{_b64(2)},fp-1:{_b64(1)}",
    "AGENTCORE_KEYS_TOKEN_MAP": f"tm-1:{_b64(9)}",
}


@pytest.fixture(params=["fake", "env"])
def keys(request: pytest.FixtureRequest) -> KeyProvider:
    return FakeKeyProvider.default() if request.param == "fake" else EnvKeyProvider(ENV)


def test_purposes_use_distinct_keys(keys: KeyProvider) -> None:
    fp = keys.key(KeyPurpose.fingerprint, keys.current_kid(KeyPurpose.fingerprint))
    tm = keys.key(KeyPurpose.token_map, keys.current_kid(KeyPurpose.token_map))
    assert fp != tm
    assert len(fp) >= 32


def test_unknown_kid_raises(keys: KeyProvider) -> None:
    with pytest.raises(KeyError):
        keys.key(KeyPurpose.fingerprint, "no-existe")


def test_old_kid_still_available_after_rotation() -> None:
    provider = FakeKeyProvider.default()
    old = provider.current_kid(KeyPurpose.fingerprint)
    provider.rotate(KeyPurpose.fingerprint, "fp-nuevo", b"n" * 32)
    assert provider.current_kid(KeyPurpose.fingerprint) == "fp-nuevo"
    assert provider.key(KeyPurpose.fingerprint, old)
    env = EnvKeyProvider(ENV)
    assert env.current_kid(KeyPurpose.fingerprint) == "fp-2"
    assert env.key(KeyPurpose.fingerprint, "fp-1") == bytes([1]) * 32


def test_env_provider_fails_at_startup_without_keys() -> None:
    with pytest.raises(RuntimeError):
        EnvKeyProvider({})
    with pytest.raises(RuntimeError):
        EnvKeyProvider({**ENV, "AGENTCORE_KEYS_TOKEN_MAP": f"tm-1:{base64.b64encode(b'corta').decode()}"})
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/contracts -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'testing.fakes.clock'`.

- [ ] **Step 3: Implementar los dobles y `EnvKeyProvider`**

`testing/fakes/clock.py`:

```python
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from testing.builders import NOW


class FakeClock:
    """Reloj avanzable: `advance` mueve `now()` y `monotonic_ns()` por igual."""

    def __init__(self, start: datetime = NOW) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock necesita un instante con zona")
        self._now = start
        self._mono = 0

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._mono

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("el reloj no retrocede")
        self._now += delta
        self._mono += int(delta.total_seconds() * 1_000_000_000)


if TYPE_CHECKING:
    from agent_core.ports import Clock

    def _conforms(x: FakeClock) -> Clock:
        return x
```

`testing/fakes/ids.py`:

```python
import base64
import hashlib
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from agent_core.ports import IdKind


class FakeIds:
    """IDs deterministas: primero los sembrados (replay), luego `<kind>-0001`, `<kind>-0002`…"""

    def __init__(self, seed: Mapping[IdKind, Sequence[str]] | None = None, token_seed: str = "fake") -> None:
        self._seeded: dict[IdKind, deque[str]] = {k: deque(v) for k, v in (seed or {}).items()}
        self._counters: defaultdict[IdKind, int] = defaultdict(int)
        self._token_seed = token_seed
        self._tokens = 0

    def new_id(self, kind: IdKind) -> str:
        queue = self._seeded.get(kind)
        if queue:
            return queue.popleft()
        self._counters[kind] += 1
        return f"{kind.value}-{self._counters[kind]:04d}"

    def secret_token(self) -> str:
        self._tokens += 1
        digest = hashlib.sha256(f"{self._token_seed}:{self._tokens}".encode()).digest()[:16]
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


if TYPE_CHECKING:
    from agent_core.ports import IdSource

    def _conforms(x: FakeIds) -> IdSource:
        return x
```

`testing/fakes/keys.py`:

```python
import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING

from agent_core.ports import KeyPurpose


class FakeKeyProvider:
    def __init__(self, keys: Mapping[KeyPurpose, Mapping[str, bytes]], current: Mapping[KeyPurpose, str]) -> None:
        self._keys = {purpose: dict(by_kid) for purpose, by_kid in keys.items()}
        self._current = dict(current)

    @classmethod
    def default(cls) -> "FakeKeyProvider":
        def derive(label: str) -> bytes:
            return hashlib.sha256(label.encode()).digest()

        return cls(
            keys={KeyPurpose.fingerprint: {"fp-1": derive("fp-1")}, KeyPurpose.token_map: {"tm-1": derive("tm-1")}},
            current={KeyPurpose.fingerprint: "fp-1", KeyPurpose.token_map: "tm-1"},
        )

    def current_kid(self, purpose: KeyPurpose) -> str:
        return self._current[purpose]

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        return self._keys[purpose][kid]

    def rotate(self, purpose: KeyPurpose, kid: str, key: bytes) -> None:
        self._keys.setdefault(purpose, {})[kid] = key
        self._current[purpose] = kid


if TYPE_CHECKING:
    from agent_core.ports import KeyProvider

    def _conforms(x: FakeKeyProvider) -> KeyProvider:
        return x
```

`agent_core/adapters/env_keys.py`:

```python
"""Claves desde variables de entorno. SOLO DEMO: etiquetado como secreto de entorno (spec §8.1.1)."""

import base64
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

from agent_core.ports.keys import KeyPurpose

_VARS = {KeyPurpose.fingerprint: "AGENTCORE_KEYS_FINGERPRINT", KeyPurpose.token_map: "AGENTCORE_KEYS_TOKEN_MAP"}
_MIN_KEY_BYTES = 32


class EnvKeyProvider:
    """Formato por variable: `kid:base64,kid:base64`; la primera es la vigente. Falla al arrancar si falta."""

    LABEL = "demo-secret-env"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        source = os.environ if env is None else env
        self._keys: dict[KeyPurpose, dict[str, bytes]] = {}
        self._current: dict[KeyPurpose, str] = {}
        for purpose, var in _VARS.items():
            raw = source.get(var, "").strip()
            if not raw:
                raise RuntimeError(f"falta {var} ({self.LABEL}); nunca se cae a hash sin clave")
            for index, item in enumerate(raw.split(",")):
                kid, _, encoded = item.strip().partition(":")
                key = base64.b64decode(encoded)
                if not kid or len(key) < _MIN_KEY_BYTES:
                    raise RuntimeError(f"{var}: clave inválida o menor a {_MIN_KEY_BYTES} bytes")
                self._keys.setdefault(purpose, {})[kid] = key
                if index == 0:
                    self._current[purpose] = kid

    def current_kid(self, purpose: KeyPurpose) -> str:
        return self._current[purpose]

    def key(self, purpose: KeyPurpose, kid: str) -> bytes:
        return self._keys[purpose][kid]


if TYPE_CHECKING:
    from agent_core.ports.keys import KeyProvider

    def _conforms(x: EnvKeyProvider) -> KeyProvider:
        return x
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/contracts -v`
Esperado: todas PASS.

- [ ] **Step 5: Lint, tipos, fronteras y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add testing/fakes agent_core/adapters/env_keys.py tests/contracts
git commit -m "feat(m0): dobles de reloj, IDs y claves con suites de contrato

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Registro en memoria y ejecutor de tools guionable

**Files:**
- Create: `testing/fakes/registry.py`, `testing/fakes/tools.py`
- Test: `tests/contracts/test_registry_contract.py`, `tests/contracts/test_tools_contract.py`

**Interfaces:**
- Consumes: `RegistryPort`, `ToolExecutor`, `ToolCallContext`, `ToolResult`, `ToolStatus`, `IdKind` (Task 8); `FakeIds` (Task 9); entidades (Task 5); `require_exact_refs` (Task 2); `testing.builders.principal`.
- Produces:
  - `InMemoryRegistry()` con `.add(*entities)`, `.add_release(release, agent_id, alias="prod", version=None)` y `.revoke(release_id)`;
  - `Scripted(status, result=None, error=None, effect=True)`;
  - `RecordedCall`;
  - `FakeToolExecutor(ids)` con `.register(tool_def, script=(), handler=None)`, `.register_readback(readback_def, of)`, `.calls` y `.effects`.

- [ ] **Step 1: Escribir las suites de contrato**

`tests/contracts/test_registry_contract.py`:

```python
import pytest

from agent_core.domain import AgentSelector, EntityRef, Flow, InvalidRuntimeRef, Release, Template
from agent_core.ports import RegistryPort
from testing.builders import principal
from testing.fakes.registry import InMemoryRegistry

EXACT_FLOW = {"id": "flujo", "version": "1.0.0", "priority": 1, "nodes": [
    {"id": "t", "type": "tool", "config": {"tool": "buscar@1.0.0", "save_as": "x"},
     "next": {"ok": "fin", "error": "fin", "timeout": "fin", "denied": "fin"}},
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}]}
RANGED_FLOW = {**EXACT_FLOW, "id": "flujo-rango", "nodes": [
    {**EXACT_FLOW["nodes"][0], "config": {"tool": "buscar@^1", "save_as": "x"}}, EXACT_FLOW["nodes"][1]]}


@pytest.fixture
def registry() -> RegistryPort:
    reg = InMemoryRegistry()
    reg.add(Flow.model_validate(EXACT_FLOW), Flow.model_validate(RANGED_FLOW),
            Template(id="t/saludo", version="1.0.0", locales={"es": "Hola"}))
    reg.add_release(Release(id="rel-1", status="active", language_detection=EntityRef.parse("lang@1.0.0")),
                    agent_id="atencion")
    reg.add_release(Release(id="rel-2", status="active", language_detection=EntityRef.parse("lang@1.0.0")),
                    agent_id="atencion", alias=None, version="2.0.0")
    return reg


def test_get_exact_entity(registry: RegistryPort) -> None:
    flow = registry.get(EntityRef.parse("flujo@1.0.0"), Flow)
    assert flow.id == "flujo"
    assert registry.get(EntityRef.parse("t/saludo@1.0.0"), Template).locales["es"] == "Hola"


def test_get_with_non_exact_content_raises(registry: RegistryPort) -> None:
    with pytest.raises(InvalidRuntimeRef):
        registry.get(EntityRef.parse("flujo-rango@1.0.0"), Flow)


def test_resolve_release_by_alias_and_version(registry: RegistryPort) -> None:
    assert registry.resolve_release(AgentSelector.parse("atencion"), principal()).id == "rel-1"
    assert registry.resolve_release(AgentSelector.parse("atencion@2.0.0"), principal()).id == "rel-2"


def test_revoked_release_status() -> None:
    reg = InMemoryRegistry()
    reg.add_release(Release(id="rel-1", status="active", language_detection=EntityRef.parse("lang@1.0.0")),
                    agent_id="a")
    assert reg.release_status("rel-1") == "active"
    reg.revoke("rel-1")
    assert reg.release_status("rel-1") == "revoked"
```

`tests/contracts/test_tools_contract.py`:

```python
from dataclasses import dataclass

import pytest

from agent_core.domain import EntityRef, ToolDef
from agent_core.ports import ToolCallContext, ToolExecutor, ToolStatus
from testing.builders import principal
from testing.fakes.ids import FakeIds
from testing.fakes.tools import FakeToolExecutor, Scripted

WRITE = EntityRef.parse("radicar@1.0.0")
READBACK = EntityRef.parse("obtener@1.0.0")
READ = EntityRef.parse("buscar@1.0.0")
WRITE_STATUSES = {ToolStatus.ok, ToolStatus.denied, ToolStatus.uncertain, ToolStatus.step_up_required}


@dataclass
class Setup:
    executor: ToolExecutor
    ctx: ToolCallContext
    low_ctx: ToolCallContext


def _defs() -> tuple[ToolDef, ToolDef, ToolDef]:
    write = ToolDef(id="radicar", version="1.0.0", risk_class="write_reversible", min_auth_level="session",
                    idempotent=False, readback_by="idempotency_key")
    readback = ToolDef(id="obtener", version="1.0.0", risk_class="read", min_auth_level="session", idempotent=True)
    read = ToolDef(id="buscar", version="1.0.0", risk_class="read", min_auth_level="session", idempotent=True)
    return write, readback, read


@pytest.fixture
def setup() -> Setup:
    fake = FakeToolExecutor(FakeIds())
    write, readback, read = _defs()
    fake.register(write, script=[Scripted(ToolStatus.uncertain, effect=True)])
    fake.register_readback(readback, of=WRITE)
    fake.register(read, handler=lambda args: {"eco": args.get("q")})
    ctx = ToolCallContext(run_id="run-0001", release="rel-1", principal=principal())
    low = ToolCallContext(run_id="run-0001", release="rel-1",
                          principal=principal(id=None, auth={"level": "anonymous", "at": principal().auth.at}))
    return Setup(fake, ctx, low)


def _readback(s: Setup, key: str) -> object:
    return s.executor.execute(READBACK, {"idempotency_key": key}, {}, s.ctx).result_full


def test_write_returns_only_write_statuses(setup: Setup) -> None:
    result = setup.executor.execute(WRITE, {"a": 1}, {}, setup.ctx, idempotency_key="action-1")
    assert result.status in WRITE_STATUSES


def test_same_idempotency_key_returns_same_resource(setup: Setup) -> None:
    setup.executor.execute(WRITE, {"a": 1}, {}, setup.ctx, idempotency_key="action-1")
    second = setup.executor.execute(WRITE, {"a": 1}, {}, setup.ctx, idempotency_key="action-1")
    assert second.status is ToolStatus.ok
    assert second.result_full == _readback(setup, "action-1")


def test_step_up_required_has_no_effect(setup: Setup) -> None:
    result = setup.executor.execute(WRITE, {"a": 1}, {}, setup.low_ctx, idempotency_key="action-2")
    assert result.status is ToolStatus.step_up_required
    assert result.required_level == "session"
    assert _readback(setup, "action-2") is None


def test_read_tool(setup: Setup) -> None:
    result = setup.executor.execute(READ, {"q": "x"}, {}, setup.ctx)
    assert result.status is ToolStatus.ok
    assert result.result_full == {"eco": "x"}
    assert result.call_id.startswith("call-")


def test_fake_records_calls_and_rejects_write_without_key(setup: Setup) -> None:
    fake = setup.executor
    assert isinstance(fake, FakeToolExecutor)
    with pytest.raises(ValueError):
        fake.execute(WRITE, {"a": 1}, {}, setup.ctx)
    fake.execute(READ, {"q": "y"}, {"customer_id": "cust-001"}, setup.ctx)
    assert fake.calls[-1].bound_params == {"customer_id": "cust-001"}
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/contracts/test_registry_contract.py tests/contracts/test_tools_contract.py -v`
Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `InMemoryRegistry`**

`testing/fakes/registry.py`:

```python
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from agent_core.domain import AgentSelector, EntityRef, Principal, Release, require_exact_refs


class InMemoryRegistry:
    """Registro en memoria cargado con objetos Python. La carga desde YAML de agent-registry la agrega M1."""

    def __init__(self) -> None:
        self._entities: dict[tuple[type[BaseModel], str, str], BaseModel] = {}
        self._releases: dict[str, Release] = {}
        self._selectors: dict[tuple[str, str], str] = {}

    def add(self, *entities: BaseModel) -> None:
        for entity in entities:
            ident, version = getattr(entity, "id"), getattr(entity, "version")  # noqa: B009
            self._entities[(type(entity), ident, version)] = entity

    def add_release(self, release: Release, agent_id: str, alias: str | None = "prod",
                    version: str | None = None) -> None:
        self._releases[release.id] = release
        self._selectors[(agent_id, version or alias or "prod")] = release.id

    def revoke(self, release_id: str) -> None:
        self._releases[release_id] = self._releases[release_id].model_copy(update={"status": "revoked"})

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        release = self._releases[self._selectors[(selector.id, selector.version or selector.alias or "prod")]]
        require_exact_refs(release)
        return release

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        return self._releases[release_id].status

    def get[T: BaseModel](self, ref: EntityRef, kind: type[T]) -> T:
        entity = self._entities[(kind, ref.id, ref.version)]
        require_exact_refs(entity)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref} no es {kind.__name__}")
        return entity


if TYPE_CHECKING:
    from agent_core.ports import RegistryPort

    def _conforms(x: InMemoryRegistry) -> RegistryPort:
        return x
```

- [ ] **Step 4: Implementar `FakeToolExecutor`**

`testing/fakes/tools.py`:

```python
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus

Handler = Callable[[dict[str, JsonValue]], JsonValue]
_WRITE_STATUSES = frozenset({ToolStatus.ok, ToolStatus.denied, ToolStatus.uncertain})


@dataclass(frozen=True)
class Scripted:
    """Resultado guionado. En escrituras, `effect` dice si el backend aplicó el efecto aunque falle."""

    status: ToolStatus
    result: JsonValue = None
    error: str | None = None
    effect: bool = True


@dataclass(frozen=True)
class RecordedCall:
    tool: EntityRef
    args: dict[str, JsonValue]
    bound_params: dict[str, str]
    idempotency_key: str | None
    status: ToolStatus


class FakeToolExecutor:
    def __init__(self, ids: IdSource) -> None:
        self._ids = ids
        self._defs: dict[EntityRef, ToolDef] = {}
        self._scripts: dict[EntityRef, deque[Scripted]] = {}
        self._handlers: dict[EntityRef, Handler] = {}
        self._readbacks: dict[EntityRef, EntityRef] = {}
        self.calls: list[RecordedCall] = []
        self.effects: dict[EntityRef, dict[str, JsonValue]] = {}

    def register(self, tool_def: ToolDef, *, script: Sequence[Scripted] = (), handler: Handler | None = None) -> None:
        ref = EntityRef(id=tool_def.id, version=tool_def.version)
        self._defs[ref] = tool_def
        self._scripts[ref] = deque(script)
        if handler is not None:
            self._handlers[ref] = handler

    def register_readback(self, readback_def: ToolDef, *, of: EntityRef) -> None:
        ref = EntityRef(id=readback_def.id, version=readback_def.version)
        self._defs[ref] = readback_def
        self._readbacks[ref] = of

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._defs[tool]

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        tool_def = self._defs[tool]
        if ctx.principal.auth.level < tool_def.min_auth_level:
            return self._result(tool, args, bound_params, idempotency_key, tool_def,
                                Scripted(ToolStatus.step_up_required), required=True)
        if tool in self._readbacks:
            key = args.get("idempotency_key")
            resource = self.effects.get(self._readbacks[tool], {}).get(key) if isinstance(key, str) else None
            return self._result(tool, args, bound_params, idempotency_key, tool_def,
                                Scripted(ToolStatus.ok, result=resource))
        if tool_def.is_write:
            return self._write(tool, args, bound_params, idempotency_key, tool_def)
        outcome = self._next(tool, args)
        return self._result(tool, args, bound_params, idempotency_key, tool_def, outcome)

    def _next(self, tool: EntityRef, args: dict[str, JsonValue]) -> Scripted:
        script = self._scripts[tool]
        if script:
            return script.popleft()
        handler = self._handlers.get(tool)
        return Scripted(ToolStatus.ok, result=handler(args) if handler else dict(args))

    def _write(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
               key: str | None, tool_def: ToolDef) -> ToolResult:
        if key is None:
            raise ValueError("una escritura siempre lleva idempotency_key (ADR 0007)")
        store = self.effects.setdefault(tool, {})
        if key in store:
            return self._result(tool, args, bound_params, key, tool_def, Scripted(ToolStatus.ok, result=store[key]))
        outcome = self._next(tool, args)
        if outcome.status not in _WRITE_STATUSES:
            raise ValueError(f"estado de escritura fuera de contrato: {outcome.status}")
        resource = outcome.result if outcome.result is not None else dict(args)
        if outcome.status is ToolStatus.ok or (outcome.status is ToolStatus.uncertain and outcome.effect):
            store[key] = resource
        shown = Scripted(outcome.status, result=resource if outcome.status is ToolStatus.ok else None,
                         error=outcome.error)
        return self._result(tool, args, bound_params, key, tool_def, shown)

    def _result(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str], key: str | None,
                tool_def: ToolDef, outcome: Scripted, required: bool = False) -> ToolResult:
        self.calls.append(RecordedCall(tool, dict(args), dict(bound_params), key, outcome.status))
        return ToolResult(
            status=outcome.status,
            result_full=outcome.result,
            source=tool_def.source,
            call_id=self._ids.new_id(IdKind.call),
            error=outcome.error,
            required_level=tool_def.min_auth_level if required else None,
        )


if TYPE_CHECKING:
    from agent_core.ports import ToolExecutor

    def _conforms(x: FakeToolExecutor) -> ToolExecutor:
        return x
```

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/contracts -v`
Esperado: todas PASS.

- [ ] **Step 6: Lint, tipos, fronteras y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add testing/fakes/registry.py testing/fakes/tools.py tests/contracts/test_registry_contract.py tests/contracts/test_tools_contract.py
git commit -m "feat(m0): InMemoryRegistry y FakeToolExecutor guionable con suites de contrato

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Unidad de trabajo, auditoría y outbox en memoria

**Files:**
- Create: `testing/fakes/storage.py`
- Test: `tests/contracts/test_uow_contract.py`

**Interfaces:**
- Consumes: `UnitOfWork`, `AuditSink`, `Outbox` (Task 8); `RunState`, `TurnResult`, `RunResult`, `OutboxMessage`, `EngineEvent`, `VersionConflict`, `TurnInProgress` (tareas 1–7); `testing.builders` (Task 6); `tests/m00/samples.make_event` (Task 7).
- Produces:
  - `SimulatedCrash(Exception)`;
  - `FaultPoint = Literal["on_commit", "after_commit"]`;
  - `InMemoryStore()` con `.inject(point, times=1)` y `.uow() -> InMemoryUoW`;
  - `InMemoryUoW(store)`;
  - `InMemoryAuditSink(store)`;
  - `InMemoryOutbox(store)`.

- [ ] **Step 1: Escribir la suite de contrato**

`tests/contracts/test_uow_contract.py`:

```python
from datetime import timedelta

import pytest
from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, OutboxMessage, PrincipalKey, RunResult, TurnInProgress, VersionConflict
from agent_core.ports import AuditSink, Outbox, UnitOfWorkFactory
from testing.builders import NOW, run_state
from testing.fakes.storage import InMemoryAuditSink, InMemoryOutbox, InMemoryStore, SimulatedCrash
from tests.m00.samples import make_event

EVENT = TypeAdapter(AnyEvent).validate_python({**make_event("run_closed"),
                                               "payload": {"outcome": "resolved", "closed_by": "flow"}})
MSG = OutboxMessage(message_id="message-0001", type="handoff_created", run_id="run-0001", payload={"k": 1},
                    created_at=NOW)
TTL = timedelta(seconds=30)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def factory(store: InMemoryStore) -> UnitOfWorkFactory:
    return store.uow


def _seed(factory: UnitOfWorkFactory) -> None:
    with factory() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.commit()


def test_nothing_persists_without_commit(factory: UnitOfWorkFactory) -> None:
    with factory() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.append_events("run-0001", [EVENT])
    with factory() as uow:
        assert uow.load_run("run-0001") is None
        assert uow.last_event("run-0001") is None


def test_save_run_versions_and_conflicts(factory: UnitOfWorkFactory) -> None:
    with factory() as uow:
        saved = uow.save_run(run_state(), expected_version=0)
        assert saved.state_version == 1
        uow.commit()
    with factory() as uow:
        loaded = uow.load_run("run-0001")
        assert loaded is not None
        with pytest.raises(VersionConflict):
            uow.save_run(loaded, expected_version=0)
        assert uow.save_run(loaded, expected_version=1).state_version == 2


def test_find_run_by_session(factory: UnitOfWorkFactory) -> None:
    _seed(factory)
    with factory() as uow:
        found = uow.find_run_by_session("session-0001")
        assert found is not None
        assert found.run_id == "run-0001"


def test_lease_is_immediate_and_expires(factory: UnitOfWorkFactory) -> None:
    first = factory()
    first.__enter__()
    first.acquire_turn("run-0001", "turn-a", NOW, TTL)
    with factory() as other:
        with pytest.raises(TurnInProgress):
            other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=5), TTL)
        other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=31), TTL)  # venció


def test_release_turn_applies_on_commit(factory: UnitOfWorkFactory) -> None:
    with factory() as uow:
        uow.acquire_turn("run-0001", "turn-a", NOW, TTL)
        uow.release_turn("run-0001", "turn-a")
        uow.commit()
    with factory() as other:
        other.acquire_turn("run-0001", "turn-b", NOW + timedelta(seconds=1), TTL)


def test_state_events_and_outbox_commit_together(store: InMemoryStore, factory: UnitOfWorkFactory) -> None:
    store.inject("on_commit")
    with pytest.raises(SimulatedCrash), factory() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.append_events("run-0001", [EVENT])
        uow.enqueue_outbox(MSG)
        uow.commit()
    audit: AuditSink = InMemoryAuditSink(store)
    outbox: Outbox = InMemoryOutbox(store)
    with factory() as uow:
        assert uow.load_run("run-0001") is None
    assert audit.read("run-0001") == []
    assert outbox.pending(10) == []


def test_after_commit_fault_keeps_changes(store: InMemoryStore, factory: UnitOfWorkFactory) -> None:
    store.inject("after_commit")
    with pytest.raises(SimulatedCrash), factory() as uow:
        uow.save_run(run_state(), expected_version=0)
        uow.commit()
    with factory() as uow:
        assert uow.load_run("run-0001") is not None


def test_audit_and_outbox_read_committed_in_order(store: InMemoryStore, factory: UnitOfWorkFactory) -> None:
    second = MSG.model_copy(update={"message_id": "message-0002"})
    with factory() as uow:
        uow.append_events("run-0001", [EVENT])
        uow.enqueue_outbox(MSG)
        uow.enqueue_outbox(second)
        uow.commit()
    audit = InMemoryAuditSink(store)
    outbox = InMemoryOutbox(store)
    assert audit.read("run-0001") == [EVENT]
    audit.append_outside_turn("run-0001", [EVENT])
    assert len(audit.read("run-0001")) == 2
    assert [m.message_id for m in outbox.pending(10)] == ["message-0001", "message-0002"]
    outbox.mark_delivered("message-0001")
    assert [m.message_id for m in outbox.pending(10)] == ["message-0002"]


def test_turn_results_idempotency_and_handoffs(factory: UnitOfWorkFactory) -> None:
    key = PrincipalKey(type="customer", id="cust-001")
    result = RunResult(run_id="run-0001", release="rel-1", status="open", trace_id="trace-1")
    with factory() as uow:
        uow.put_run_idempotency(key, "idem-1", "hash-body", result)
        uow.put_handoff("handoff-0001", {"reason_code": "tool_failure"})
        uow.commit()
    with factory() as uow:
        assert uow.get_run_idempotency(key, "idem-1") == ("hash-body", result)
        assert uow.get_handoff("handoff-0001") == {"reason_code": "tool_failure"}
        assert uow.get_turn_result("run-0001", "c-1") is None


def test_list_inactive_uses_inactive_after(factory: UnitOfWorkFactory) -> None:
    with factory() as uow:
        uow.save_run(run_state(run_id="run-a", session_id="s-a", inactive_after=NOW + timedelta(minutes=5)), 0)
        uow.save_run(run_state(run_id="run-b", session_id="s-b", inactive_after=NOW + timedelta(minutes=1)), 0)
        uow.save_run(run_state(run_id="run-c", session_id="s-c", inactive_after=NOW + timedelta(hours=1)), 0)
        uow.commit()
    with factory() as uow:
        assert uow.list_inactive(NOW + timedelta(minutes=10), limit=10) == ["run-b", "run-a"]
        assert uow.list_inactive(NOW + timedelta(minutes=10), limit=1) == ["run-b"]


def test_save_run_revalidates_state(factory: UnitOfWorkFactory) -> None:
    broken = run_state()
    broken.outcome = "resolved"  # type: ignore[assignment]  # model_copy/asignación no valida: la UoW sí
    with factory() as uow, pytest.raises(ValueError):
        uow.save_run(broken, expected_version=0)
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/contracts/test_uow_contract.py -v`
Esperado: FAIL con `ModuleNotFoundError: No module named 'testing.fakes.storage'`.

- [ ] **Step 3: Implementar `storage.py`**

`testing/fakes/storage.py`:

```python
"""UoW, log de auditoría y outbox en memoria, con fallas inyectables (M0 §10; M3 las usa entre commits)."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Self

from agent_core.domain import (
    EngineEvent, JsonValue, OutboxMessage, PrincipalKey, RunResult, RunState, TurnInProgress, TurnResult,
    VersionConflict,
)

FaultPoint = Literal["on_commit", "after_commit"]


class SimulatedCrash(Exception):
    """Caída simulada del proceso en un punto de commit."""


@dataclass
class _Lease:
    turn_id: str
    expires_at: datetime


@dataclass
class InMemoryStore:
    runs: dict[str, RunState] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    leases: dict[str, _Lease] = field(default_factory=dict)
    turn_results: dict[tuple[str, str], TurnResult] = field(default_factory=dict)
    idempotency: dict[tuple[PrincipalKey, str], tuple[str, RunResult]] = field(default_factory=dict)
    handoffs: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    events: dict[str, list[EngineEvent]] = field(default_factory=dict)
    outbox: list[OutboxMessage] = field(default_factory=list)
    delivered: set[str] = field(default_factory=set)
    faults: deque[FaultPoint] = field(default_factory=deque)

    def inject(self, point: FaultPoint, times: int = 1) -> None:
        self.faults.extend([point] * times)

    def take_fault(self, point: FaultPoint) -> bool:
        if self.faults and self.faults[0] == point:
            self.faults.popleft()
            return True
        return False

    def uow(self) -> "InMemoryUoW":
        return InMemoryUoW(self)


class InMemoryUoW:
    """Una instancia = una transacción. Lee sus propias escrituras; `acquire_turn` es inmediato."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._runs: dict[str, RunState] = {}
        self._turn_results: dict[tuple[str, str], TurnResult] = {}
        self._idempotency: dict[tuple[PrincipalKey, str], tuple[str, RunResult]] = {}
        self._handoffs: dict[str, dict[str, JsonValue]] = {}
        self._events: dict[str, list[EngineEvent]] = {}
        self._outbox: list[OutboxMessage] = []
        self._released: list[tuple[str, str]] = []
        self._done = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._done = True  # lo no commiteado se descarta

    def _check_open(self) -> None:
        if self._done:
            raise RuntimeError("la UoW ya terminó; abre una nueva")

    def acquire_turn(self, run_id: str, turn_id: str, now: datetime, ttl: timedelta) -> None:
        self._check_open()
        lease = self._store.leases.get(run_id)
        if lease is not None and lease.turn_id != turn_id and lease.expires_at > now:
            raise TurnInProgress(f"turno {lease.turn_id} en curso en {run_id}")
        self._store.leases[run_id] = _Lease(turn_id, now + ttl)

    def release_turn(self, run_id: str, turn_id: str) -> None:
        self._check_open()
        self._released.append((run_id, turn_id))

    def load_run(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id) or self._store.runs.get(run_id)

    def find_run_by_session(self, session_id: str) -> RunState | None:
        for state in self._runs.values():
            if state.session_id == session_id:
                return state
        run_id = self._store.sessions.get(session_id)
        return self._store.runs.get(run_id) if run_id else None

    def save_run(self, state: RunState, expected_version: int) -> RunState:
        self._check_open()
        current = self.load_run(state.run_id)
        current_version = current.state_version if current else 0
        if expected_version != current_version:
            raise VersionConflict(f"{state.run_id}: esperada {expected_version}, vigente {current_version}")
        validated = RunState.model_validate(state.model_dump())
        saved = validated.model_copy(update={"state_version": expected_version + 1})
        self._runs[state.run_id] = saved
        return saved

    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None:
        key = (run_id, client_turn_id)
        return self._turn_results.get(key) or self._store.turn_results.get(key)

    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None:
        self._check_open()
        self._turn_results[(run_id, client_turn_id)] = result

    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None:
        return self._idempotency.get((principal, key)) or self._store.idempotency.get((principal, key))

    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str, result: RunResult) -> None:
        self._check_open()
        self._idempotency[(principal, key)] = (body_hash, result)

    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None:
        self._check_open()
        self._handoffs[handoff_ref] = packet

    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None:
        return self._handoffs.get(handoff_ref) or self._store.handoffs.get(handoff_ref)

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        self._check_open()
        self._events.setdefault(run_id, []).extend(events)

    def last_event(self, run_id: str) -> EngineEvent | None:
        pending = self._events.get(run_id)
        if pending:
            return pending[-1]
        committed = self._store.events.get(run_id)
        return committed[-1] if committed else None

    def enqueue_outbox(self, message: OutboxMessage) -> None:
        self._check_open()
        self._outbox.append(message)

    def list_inactive(self, now: datetime, limit: int) -> list[str]:
        merged = {**self._store.runs, **self._runs}
        due = [s for s in merged.values()
               if s.status == "open" and s.inactive_after is not None and s.inactive_after < now]
        due.sort(key=lambda s: (s.inactive_after or now, s.run_id))
        return [s.run_id for s in due[:limit]]

    def commit(self) -> None:
        self._check_open()
        store = self._store
        if store.take_fault("on_commit"):
            self._done = True
            raise SimulatedCrash("caída antes de aplicar el commit")
        for run_id, state in self._runs.items():
            store.runs[run_id] = state
            if state.session_id is not None:
                store.sessions[state.session_id] = run_id
        store.turn_results.update(self._turn_results)
        store.idempotency.update(self._idempotency)
        store.handoffs.update(self._handoffs)
        for run_id, events in self._events.items():
            store.events.setdefault(run_id, []).extend(events)
        store.outbox.extend(self._outbox)
        for run_id, turn_id in self._released:
            lease = store.leases.get(run_id)
            if lease is not None and lease.turn_id == turn_id:
                del store.leases[run_id]
        self._done = True
        if store.take_fault("after_commit"):
            raise SimulatedCrash("caída después de aplicar el commit")


class InMemoryAuditSink:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def read(self, run_id: str) -> list[EngineEvent]:
        return list(self._store.events.get(run_id, []))

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None:
        self._store.events.setdefault(run_id, []).extend(events)


class InMemoryOutbox:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def pending(self, limit: int) -> list[OutboxMessage]:
        return [m for m in self._store.outbox if m.message_id not in self._store.delivered][:limit]

    def mark_delivered(self, message_id: str) -> None:
        self._store.delivered.add(message_id)


if TYPE_CHECKING:
    from agent_core.ports import AuditSink, Outbox, UnitOfWork

    def _conforms_uow(x: InMemoryUoW) -> UnitOfWork:
        return x

    def _conforms_audit(x: InMemoryAuditSink) -> AuditSink:
        return x

    def _conforms_outbox(x: InMemoryOutbox) -> Outbox:
        return x
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/contracts/test_uow_contract.py -v`
Esperado: todas PASS.

Nota sobre `test_save_run_revalidates_state`: `RunState` no valida asignaciones, así que el estado inválido se puede construir en memoria, pero la UoW lo rechaza con `ValidationError` (subclase de `ValueError`). Esa es la intención: los validadores de coherencia corren al persistir.

- [ ] **Step 5: Lint, tipos, fronteras y commit**

```bash
uv run ruff check . && uv run mypy && uv run lint-imports
git add testing/fakes/storage.py tests/contracts/test_uow_contract.py
git commit -m "feat(m0): InMemoryUoW con lease, versión y fallas inyectables; auditoría y outbox en memoria

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `contracts/` generado, CLI `agentcore` y CI

**Files:**
- Create/replace: `agent_core/contracts.py`, `agent_core/cli.py`
- Create (generado): `contracts/schemas/*.json`, `contracts/VERSION`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/m00/test_contracts.py`

**Interfaces:**
- Consumes: todo `agent_core.domain` y los tipos de `agent_core.ports`.
- Produces:
  - `PUBLIC_TYPES: Mapping[str, Any]`;
  - `render_contracts() -> dict[str, str]` (ruta relativa → contenido);
  - `write_contracts(out: Path) -> None`;
  - `check_contracts(out: Path) -> list[str]` (rutas desactualizadas);
  - `main(argv) -> int`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m00/test_contracts.py`:

```python
import json
import shutil
from pathlib import Path

from agent_core.cli import main
from agent_core.contracts import check_contracts, render_contracts, write_contracts
from agent_core.domain import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_render_is_deterministic_and_complete() -> None:
    first, second = render_contracts(), render_contracts()
    assert first == second
    assert first["VERSION"] == SCHEMA_VERSION + "\n"
    for name in ("RunState", "AnyEvent", "Node", "Flow", "Agent", "TurnResult", "ProblemCode", "ToolResult"):
        assert f"schemas/{name}.json" in first
        json.loads(first[f"schemas/{name}.json"])


# T-M0-05
def test_committed_contracts_are_current() -> None:
    assert check_contracts(ROOT / "contracts") == []


def test_check_detects_stale_and_missing(tmp_path: Path) -> None:
    out = tmp_path / "contracts"
    write_contracts(out)
    assert check_contracts(out) == []
    (out / "schemas" / "RunState.json").write_text("{}\n", encoding="utf-8")
    (out / "schemas" / "Agent.json").unlink()
    (out / "schemas" / "Sobrante.json").write_text("{}\n", encoding="utf-8")
    assert sorted(check_contracts(out)) == ["schemas/Agent.json", "schemas/RunState.json", "schemas/Sobrante.json"]


def test_cli_check_exit_codes(tmp_path: Path) -> None:
    out = tmp_path / "c"
    assert main(["contracts", "--out", str(out)]) == 0
    assert main(["contracts", "--check", "--out", str(out)]) == 0
    shutil.rmtree(out / "schemas")
    assert main(["contracts", "--check", "--out", str(out)]) == 1
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m00/test_contracts.py -v`
Esperado: FAIL con `ImportError: cannot import name 'check_contracts'`.

- [ ] **Step 3: Implementar `contracts.py` y `cli.py`**

`agent_core/contracts.py`:

```python
"""Generación de `contracts/` (ADR 0002): JSON Schema de tipos públicos, eventos y nodos + VERSION."""

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import TypeAdapter

from agent_core import domain as d
from agent_core.ports import AuthzDecision, GenerationResult, ToolCallContext, ToolResult

PUBLIC_TYPES: Mapping[str, Any] = MappingProxyType(
    {
        "Agent": d.Agent, "Flow": d.Flow, "Node": d.Node, "Release": d.Release, "Policy": d.Policy,
        "Template": d.Template, "Prompt": d.Prompt, "ToolDef": d.ToolDef, "DecisionModelDef": d.DecisionModelDef,
        "LanguageDetection": d.LanguageDetection, "InjectionRuleset": d.InjectionRuleset,
        "RunState": d.RunState, "TurnInput": d.TurnInput, "TurnResult": d.TurnResult, "RunInput": d.RunInput,
        "RunResult": d.RunResult, "AnyEvent": d.AnyEvent, "OutboxMessage": d.OutboxMessage,
        "HandoffCreatedPayload": d.HandoffCreatedPayload, "EscalationRequest": d.EscalationRequest,
        "RejectedDraft": d.RejectedDraft, "TranscriptEntry": d.TranscriptEntry, "Fingerprint": d.Fingerprint,
        "ProblemCode": d.ProblemCode, "ToolResult": ToolResult, "ToolCallContext": ToolCallContext,
        "AuthzDecision": AuthzDecision, "GenerationResult": GenerationResult,
    }
)


def render_contracts() -> dict[str, str]:
    files: dict[str, str] = {}
    for name in sorted(PUBLIC_TYPES):
        schema = TypeAdapter(PUBLIC_TYPES[name]).json_schema(mode="validation")
        files[f"schemas/{name}.json"] = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    files["VERSION"] = d.SCHEMA_VERSION + "\n"
    return files


def _existing(out: Path) -> set[str]:
    schemas = out / "schemas"
    return {f"schemas/{p.name}" for p in schemas.glob("*.json")} if schemas.is_dir() else set()


def write_contracts(out: Path) -> None:
    files = render_contracts()
    for stale in _existing(out) - set(files):
        (out / stale).unlink()
    for rel, content in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)


def check_contracts(out: Path) -> list[str]:
    """Rutas que difieren, faltan o sobran respecto de lo generado."""
    files = render_contracts()
    diffs = sorted(_existing(out) - set(files))
    for rel, content in files.items():
        path = out / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            diffs.append(rel)
    return sorted(diffs)
```

`agent_core/cli.py`:

```python
"""CLI `agentcore`. Fase 1: `contracts [--check] [--out DIR]`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_core.contracts import check_contracts, write_contracts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcore")
    sub = parser.add_subparsers(dest="command", required=True)
    contracts = sub.add_parser("contracts", help="genera o verifica contracts/")
    contracts.add_argument("--check", action="store_true", help="falla si contracts/ está desactualizado")
    contracts.add_argument("--out", type=Path, default=Path("contracts"))
    args = parser.parse_args(argv)
    if args.command == "contracts":
        if args.check:
            diffs = check_contracts(args.out)
            for rel in diffs:
                print(f"contracts desactualizado: {rel} (corre `uv run agentcore contracts`)", file=sys.stderr)
            return 1 if diffs else 0
        write_contracts(args.out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Generar `contracts/` y correr las pruebas**

```bash
uv run agentcore contracts
uv run pytest tests/m00/test_contracts.py -v
```

Esperado: se crean `contracts/VERSION` y `contracts/schemas/*.json` (28 archivos), y las pruebas PASS.

Si la generación de algún esquema falla (por ejemplo, `PydanticInvalidForJsonSchema` con el discriminador por función de `Node`), no quites el tipo de `PUBLIC_TYPES`. Detente y reporta el error: `Node` es parte del contrato publicado.

- [ ] **Step 5: Agregar el chequeo a CI**

En `.github/workflows/ci.yml`, después de `- run: uv run lint-imports`, agregar:

```yaml
      - run: uv run agentcore contracts --check
```

- [ ] **Step 6: Suite completa y commit**

```bash
uv run pytest && uv run ruff check . && uv run mypy && uv run lint-imports && uv run agentcore contracts --check
git add agent_core/contracts.py agent_core/cli.py contracts tests/m00/test_contracts.py .github/workflows/ci.yml
git commit -m "feat(m0): contracts/ generado (JSON Schema + VERSION 0.1.0) y CLI agentcore

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Definición de terminado y cierre

**Files:**
- Modify: `CLAUDE.md` (sección Comandos), `docs/specs/motor/m00-dominio-y-contratos.md` (estado)

- [ ] **Step 1: Verificación completa desde cero**

```bash
uv sync --locked
uv run pytest -v
uv run ruff check .
uv run mypy
uv run lint-imports
uv run agentcore contracts --check
```

Esperado: todo en verde. Anota el total de pruebas.

- [ ] **Step 2: Revisar la definición de terminado de M0 (§10) punto por punto**

| Punto | Evidencia |
|---|---|
| Tipos, enums, esquemas de nodos, eventos, errores, `canonical_bytes` y puertos con docstrings, exportados | `tests/m00/test_public_api.py` |
| `SystemClock` y `SystemIds` en `agent_core/adapters/` | `tests/contracts/test_clock_contract.py`, `test_ids_contract.py` |
| 9 dobles de la fase 1 con suite de contrato: `FakeClock`, `FakeIds`, `InMemoryRegistry`, `FakeToolExecutor`, `InMemoryUoW`, `InMemoryAuditSink`, `InMemoryOutbox`, `FakeKeyProvider`, `EnvKeyProvider` | `tests/contracts/*` |
| `contracts/` generado, `VERSION = 0.1.0` y `--check` en CI | `tests/m00/test_contracts.py`, `.github/workflows/ci.yml` |
| T-M0-01…15 en verde | T-M0-01 y 11: `test_refs.py`; 02 y 13: `test_outcomes.py`; 03 y 09: `test_state.py`; 04, 14 y 15: `test_events.py`; 05: `test_contracts.py`; 06: `test_lint_rules.py`; 07: `test_json.py`; 08: `test_identity.py`; 10: `test_base.py` + `test_state.py`; 12: `test_nodes.py` |
| `lint-imports`, `mypy --strict` y `ruff` en verde | Step 1 |

Si algún punto no se cumple, no marques la tarea como terminada: vuelve a la tarea que corresponde.

- [ ] **Step 3: Actualizar `CLAUDE.md` (sección Comandos)**

Reemplazar la lista de comandos por:

```markdown
- Pruebas de un módulo: `uv run pytest tests/mXX`
- Suites de contrato de los puertos: `uv run pytest tests/contracts`
- Todas las pruebas: `uv run pytest`
- Fronteras entre módulos: `uv run lint-imports` (config en `.importlinter`)
- Tipos: `uv run mypy` (strict; archivos en `pyproject.toml`)
- Lint: `uv run ruff check .`
- Contratos: `uv run agentcore contracts` (regenera) · `uv run agentcore contracts --check` (CI)
- Postgres local (M3, M4, M9, M11): `docker compose up -d postgres`
```

- [ ] **Step 4: Marcar M0 como implementado en su spec**

En `docs/specs/motor/m00-dominio-y-contratos.md`, cambiar la línea de estado por:

```markdown
- Estado: **rev. 4 · implementado** (fase 1) · Fase 1
```

Agrega al final del Changelog una línea con las decisiones que tomaste durante la implementación y que el spec no cubría. Si no tomaste ninguna, escribe "sin decisiones fuera del spec".

- [ ] **Step 5: Commit final**

```bash
git add CLAUDE.md docs/specs/motor/m00-dominio-y-contratos.md
git commit -m "docs(m0): comandos del repo y M0 marcado como implementado

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
