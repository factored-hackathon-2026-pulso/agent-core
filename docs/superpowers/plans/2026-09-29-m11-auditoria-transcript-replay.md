# M11 — Auditoría, transcript y replay: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `agent_core.audit` (M11) y el paquete común `agent_telemetry`: cadena de hash por run sobre el log append-only (con adaptador Postgres), spans OTel con atributos `agentcore.*`, transcript con huella HMAC con `kid`, lectura renderizada por lector y replay (`fixture` en CI, `audit` para runs reales) con los comandos `agentcore record` y `agentcore replay`, con T-M11-01…10 en verde.

**Architecture:**
- `agent_core.audit` solo importa `agent_core.domain`, `agent_core.ports`, `agent_core.views` (contrato `audit` de `.importlinter`) y el paquete común `agent_telemetry`. Por eso **no puede importar M4/M2**: el replay recibe el motor por inyección (`EngineRunner`, un `Protocol` definido en M11) y solo construye los puertos "grabados". El cableado real (M4 + M2 + M5/M6/M8) vive en `agent_core/cli.py` (el único que puede importarlo todo).
- La cadena es una función pura (`chain.py`); `AuditLog` la aplica dentro de la `UnitOfWork` del turno. El almacenamiento real de la cadena es un adaptador Postgres en `agent_core/adapters/` (no importa `audit`), con append-only **impuesto por la base** (permisos + triggers). M4 lo usará desde su `UnitOfWork` de Postgres.
- El replay es un pipeline: `check_chain` → puertos grabados → motor inyectado → comparación evento a evento que excluye `event_id/seq/prev_hash/hash/ts` y `MEASURED_FIELDS` (M0).

**Tech Stack:** Python 3.12, Pydantic v2, `psycopg[binary]` 3 (dependencia nueva, adaptador Postgres), `opentelemetry-api`/`-sdk`/`-exporter-otlp-proto-http` (dependencias nuevas), PyYAML (ya está), pytest, ruff, mypy strict, import-linter, Postgres 16 (`docker compose`).

**Spec:** `docs/specs/motor/m11-auditoria-transcript-replay.md` (la Task 1 lo sube a rev. 2 con las decisiones de este plan). Léelos antes de empezar: `docs/specs/motor/00-indice.md` (§3 dependencias, §4 puertos, §6 eventos, §8 convenciones), `docs/specs/motor/m00-dominio-y-contratos.md` §2.8–§2.10, ADR 0003 (dos planos, replay en dos modos), ADR 0008 (huellas con clave), spec general §8.4, §11, §13.2 y §13.6. El spec manda sobre este plan salvo en lo que la sección "Decisiones" declara.

## Global Constraints

- Python `>=3.12,<3.13`, Postgres 16 (ADR 0001). Sin colas, Redis ni vector DB.
- `agent_core.audit` solo importa `agent_core.domain`, `agent_core.ports`, `agent_core.views` y `agent_telemetry` (`uv run lint-imports`). `agent_telemetry` no importa `agent_core`. `agent_core.adapters` solo importa `agent_core.domain` y `agent_core.ports`. `agent_core` nunca importa `testing` ni `tests`.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random`, `secrets` ni `os.urandom`: la hora sale del `Clock` y los IDs/secretos del `IdSource` (lo verifica `ruff`, TID251). Duraciones: `Clock.monotonic_ns()`.
- Cifras en `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads` y toda canonización usa `canonical_bytes` (JCS).
- La cadena usa `sha256` sin clave (eventos ya en vista `audit`). **Ninguna huella del transcript es `sha256` sin clave**: siempre `fingerprint` de M7 (HMAC-SHA256 + `kid`).
- El log es append-only: no hay `update` ni `delete` en el código **y** la base lo impide (permiso + trigger).
- Nunca se guarda razonamiento intermedio. El transcript guarda solo vista `model` (PII tokenizada).
- Replay nunca llama a modelos ni tools reales. Replay `audit` nunca accede a la vista `full` (el doble `ForbiddenFullSource` lanza si se usa).
- Ningún `repr`, mensaje de error ni evento de M11 incluye valores `full`, texto de transcript en claro ni material de clave.
- Fixtures y pruebas solo con datos sintéticos (dominio `example.test`, IDs `cust-…`/`tx-demo-…`). Nunca datos reales del dataset ni las credenciales del diccionario de datos.
- No tocar otros módulos: **prohibido modificar `agent_core/guards/` (M6) y `agent_core/handoff/` (M10)**, que otros agentes trabajan en paralelo. Si hace falta algo fuera de una interfaz pública, detente y pregunta.
- Tests con Postgres: `docker compose up -d postgres`, `uv run pytest tests/integration -m integration`. Sin Postgres accesible se **omiten** (skip), salvo `AGENTCORE_REQUIRE_POSTGRES=1` (CI), donde fallan.
- Comandos: `uv run pytest tests/m11`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`.
- `ruff`: `line-length = 110`. Si solo marca orden de imports (`I001`) o de `__all__` (`RUF022`), corrige con `uv run ruff check --fix`; cualquier otro hallazgo se corrige a mano sin cambiar la semántica del plan.
- Commits: mensajes en español, prefijo `feat(m11):` / `test(m11):` / `docs(m11):`, y terminan con `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Los bloques de código de este plan son la intención exacta; si `mypy --strict` o `ruff` exigen un ajuste de tipos o de imports, ajústalo sin cambiar nombres ni semántica (los nombres son contrato entre tareas).

## Decisiones (ambigüedades del spec resueltas con criterio; van al spec en la Task 1)

1. **`ChainedEvent`** no existe en M0. Es un alias `type ChainedEvent = EngineEvent` en `agent_core.audit` con la garantía de que `seq`, `prev_hash` y `hash` no son `None`. M0 no cambia (no se regeneran `contracts/`).
2. **Fórmula del hash:** `hash_n = sha256_hex(canonical_bytes(evento_n con seq y prev_hash, SIN el campo hash) ‖ prev_hash.encode("ascii"))`. `hash_0 = sha256_hex(b"agentcore:" ‖ run_id.encode())` (hex de 64) y es el `prev_hash` del evento con `seq = 0` (nunca `None` en un evento encadenado). `seq` empieza en 0 y es contiguo.
3. **`record_turn` recibe `turn_id`** (el spec no lo trae y `TranscriptEntry` lo exige): `record_turn(run_id, turn_id, user_msg_model, final_model, rejected)`. Orden de las referencias devueltas: `[user, *rejected (en orden), final]`. Todas las huellas se calculan **antes** de escribir en el store.
4. **Transcript store caído (Abierto del spec):** se falla el turno con `TranscriptWriteError` (sin mensaje del store, solo el nombre del tipo). Riesgo anotado: si el store falla a mitad de las entradas, el reintento del turno duplica las ya escritas; la idempotencia por `(run_id, turn_id, role, ordinal)` queda como abierto para la unidad 7.
5. **`read_rendered`** necesita el `token_map` del run: `TranscriptReader(store, uow_factory, views, keys, ids)` carga el `RunState` por UoW, abre el vault y renderiza con `purpose = "transcript_read"`. Run inexistente → `RunNotFound`.
6. **Eventos fuera de turno** (p. ej. `access_denied` de M9): `AuditSink.append_outside_turn` **no encadena** (el doble en memoria guarda tal cual). Se añade `AuditLog.append_standalone(run_id, events)` (abre una UoW, encadena, commitea). Aviso a M9: debe usar esa vía en lugar de `AuditSink.append_outside_turn`.
7. **Recorder para M3:** `AuditLog.recorder()` devuelve un callable con la firma de `EventRecorder` de M3 (`(uow, state, events) -> None`) que encadena y persiste. M4 lo cablea como `ActionContext.record`.
8. **Replay inyecta el motor:** `Replayer(engine: EngineRunner, clock, …)`. Los adaptadores `GuardService`/`DecisionService`/`UnderstandService` no existen como puertos (son de M6/M5): M11 entrega `RecordedReadings` (accesores de payloads grabados) y el cableado a esas interfaces lo hace M4 en el paso de integración (Task 14).
9. **IDs en replay:** un run real usó `SystemIds`, así que `call_id`, `decision_id`, `action_id`, `handoff_ref`… son aleatorios y van dentro de los payloads (sí se comparan). `RecordedIds` reparte, por `IdKind`, los IDs grabados en orden de cadena. Riesgo: si un módulo crea IDs en un orden distinto al de los eventos, aparece una divergencia falsa (se detecta en la Task 14).
10. **Reloj grabado:** `RecordedClock.now()` es constante dentro de un turno (el `ts` de su `turn_started`; el de `run_started` para el alta; `set()` para los instantes de `expiry_evaluated`/barrido). `monotonic_ns()` devuelve `0`.
11. **Modos:** `fixture` sirve a las tools el resultado en vista `full` del fixture (`FixtureFullSource`) y los borradores del LLM del fixture. `audit` sirve el `result` de vista `audit` del evento `tool_called`, los textos del transcript como borradores y pasa `ForbiddenFullSource`; el motor lee los resultados de `rule`/`verify`/validador con `RecordedReadings` (M2/M3/M8 deben respetar ese contrato en modo `audit`; ver riesgos).
12. **`ReplayReport`** añade campos opcionales (aditivos): `chain_broken_at: int | None` y `duration_ms: int | None`. `verdict` y `first_divergence` como en el spec.
13. **Catálogo de datos de prueba (§13.2 no lo define):** archivo YAML `tests/fixtures/catalogo-datos-prueba.yaml` con `email_domains`, `numbers` (documentos/teléfonos/productos inventados) y `values` (valores `pii_direct` permitidos). Un fixture se rechaza si en `inputs`/`full`/`drafts` hay un email fuera de los dominios, un número de 6+ dígitos fuera de `numbers`, o una hoja de un campo `pii_direct`/`pii_quasi` (según `FieldClassifier`) fuera de `values`. Los `events` no se escanean (vista `audit`, con hashes hex que darían falsos positivos).
14. **Formato del fixture:** YAML, `tests/fixtures/runs/<camino>.yaml`. Los números con decimales se leen como `Decimal` (loader propio); un `Decimal` se escribe como escalar `float` explícito.
15. **`agent-telemetry`:** paquete de primer nivel `agent_telemetry/` en este repo (el "paquete común" se extraerá luego sin cambios de API). La versión de semconv GenAI queda fijada en la constante `SEMCONV_VERSION` (ADR 0003 no pudo confirmar su estabilidad; **verifica la versión contra el paquete `opentelemetry-semantic-conventions` instalado antes de fijarla**).
16. **Exportación de evaluación (§8):** `export_events(sink, run_ids, release=None)` recibe la lista de `run_id` (el `AuditSink` no tiene listado por release; el listado es de la unidad 6/4).
17. **Códigos de salida de `agentcore replay`:** `0` match, `1` diverged, `2` chain_broken, `3` error de uso o motor no disponible.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `docs/specs/motor/m11-auditoria-transcript-replay.md` | spec rev. 2 (decisiones de arriba) |
| `pyproject.toml`, `uv.lock`, `.importlinter`, `.github/workflows/ci.yml` | dependencias, contratos de import, marcador `integration`, Postgres en CI |
| `agent_core/audit/chain.py` | `ChainedEvent`, `ChainCheck`, `genesis_hash`, `event_hash`, `chain_events`, `check_chain`, `ChainError`, `event_to_json`, `event_from_json` |
| `agent_core/audit/log.py` | `AuditLog` (`append`, `append_standalone`, `verify_chain`, `recorder`) |
| `agent_core/audit/transcript.py` | `TurnRecorder`, `TranscriptReader`, `RenderedEntry`, `TranscriptWriteError`, `RunNotFound` |
| `agent_core/audit/export.py` | `export_events`, `chain_integrity` (métricas propias) |
| `agent_core/audit/replay/report.py` | `ReplayMode`, `Divergence`, `ReplayReport` |
| `agent_core/audit/replay/compare.py` | `normalize`, `first_divergence` |
| `agent_core/audit/replay/fixture.py` | `Fixture`, `FullToolResult`, `dump_fixture`, `load_fixture` |
| `agent_core/audit/replay/synthetic.py` | `SyntheticCatalog`, `load_catalog`, `check_fixture`, `FixtureRejected` |
| `agent_core/audit/replay/ports.py` | `RecordedClock`, `RecordedIds`, `RecordedToolExecutor`, `RecordedGateway`, `RecordedReadings`, `FullSource`, `FixtureFullSource`, `ForbiddenFullSource`, `RecordedPorts`, `build_ports`, `ReplayDesync`, `FullViewAccessError` |
| `agent_core/audit/replay/recording.py` | `RecordingToolExecutor`, `RecordingGateway`, `build_fixture` (para `agentcore record`) |
| `agent_core/audit/replay/runner.py` | `EngineRunner`, `ReplayCase`, `Replayer` |
| `agent_core/audit/__init__.py` | interfaz pública |
| `agent_telemetry/{__init__,context,spans,setup,logging}.py` | `bind`, `span`, `set_content`, `current_trace_id`, `setup_tracing`, `JsonLogFormatter` |
| `agent_core/adapters/postgres_audit.py` + `agent_core/adapters/sql/audit_events.sql` | `PgAuditEvents`, `apply_audit_schema` |
| `agent_core/cli.py` | subcomandos `record` y `replay` |
| `testing/fakes/transcript.py` | `InMemoryTranscript` (con falla inyectable y supresión) |
| `tests/contracts/test_transcript_contract.py` | suite de contrato de `TranscriptStore` |
| `tests/m11/helpers.py` | constructores de eventos, motor de prueba, fixtures sintéticos |
| `tests/m11/test_*.py` | pruebas por archivo, T-M11-01…10 y trazabilidad |
| `tests/integration/test_audit_postgres.py`, `tests/integration/conftest.py` | pruebas con Postgres |
| `tests/fixtures/catalogo-datos-prueba.yaml`, `tests/fixtures/runs/*.yaml` | catálogo sintético y fixtures por camino |

---

### Task 1: Andamiaje, dependencias y spec rev. 2

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (por `uv add`), `.importlinter`
- Modify: `docs/specs/motor/m11-auditoria-transcript-replay.md`
- Create: `agent_telemetry/__init__.py` (vacío por ahora, con docstring), `tests/m11/__init__.py`, `tests/integration/__init__.py`

**Interfaces:**
- Consumes: nada.
- Produces: el spec rev. 2; paquetes importables `agent_telemetry` y `tests.m11`; marcador `integration`; contratos de import para `agent_telemetry`.

- [ ] **Step 1: Verificar que la base está en verde**

Run: `uv sync && uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo en verde (si M6/M10 en paralelo dejan la base en rojo por sus propios archivos, anótalo y sigue: no los toques).

- [ ] **Step 2: Añadir dependencias**

Run:
```bash
uv add "psycopg[binary]>=3.2" "opentelemetry-api>=1.27" "opentelemetry-sdk>=1.27" "opentelemetry-exporter-otlp-proto-http>=1.27"
```
Edita `pyproject.toml`:
- `[tool.hatch.build.targets.wheel] packages = ["agent_core", "agent_telemetry"]`
- `[tool.mypy] files = ["agent_core", "agent_telemetry", "testing"]`
- en `[tool.pytest.ini_options]` añade `markers = ["integration: requiere Postgres (docker compose up -d postgres)"]`
- overrides de mypy si alguna librería lo exige (`ignore_missing_imports` solo si `mypy` falla por un paquete sin tipos; `psycopg` y `opentelemetry` traen tipos).

- [ ] **Step 3: Contratos de import-linter**

En `.importlinter`, cambia la cabecera a `root_packages =` con dos líneas y añade el contrato de `agent_telemetry`:

```ini
[importlinter]
root_packages =
    agent_core
    agent_telemetry

[importlinter:contract:telemetry]
name = agent_telemetry no importa agent_core
type = forbidden
source_modules =
    agent_telemetry
forbidden_modules =
    agent_core
```
Los contratos existentes no cambian (el de `audit` ya permite `views`; `agent_telemetry` está fuera de `agent_core`, así que `audit` puede importarlo).

- [ ] **Step 4: Paquetes vacíos**

`agent_telemetry/__init__.py`:
```python
"""Paquete común de telemetría (ADR 0003 #4): spans OTel con atributos `agentcore.*`."""
```
`tests/m11/__init__.py` y `tests/integration/__init__.py`: vacíos.

- [ ] **Step 5: Subir el spec a rev. 2**

En `docs/specs/motor/m11-auditoria-transcript-replay.md`:
- Cambia `Estado: borrador` por `Estado: rev. 2 (2026-09-29)`.
- Reemplaza el bloque de código de §2 por:

```python
type ChainedEvent = EngineEvent            # con seq, prev_hash y hash no nulos
class ChainCheck: ok: bool; broken_at: int | None; reason: str | None
class AuditLog:
    def __init__(self, sink: AuditSink, uow_factory: UnitOfWorkFactory | None = None)
    def append(self, uow, run_id, events: list[EngineEvent]) -> list[ChainedEvent]   # asigna seq, prev_hash, hash
    def append_standalone(self, run_id, events) -> list[ChainedEvent]                 # fuera de turno (M9): abre su UoW
    def verify_chain(self, run_id) -> ChainCheck
    def recorder(self) -> Callable[[UnitOfWork, RunState, list[EngineEvent]], None]   # EventRecorder de M3
class TurnRecorder:
    def __init__(self, store: TranscriptStore, keys: KeyProvider)
    def record_turn(self, run_id, turn_id, user_msg_model, final_model, rejected: list[RejectedDraft]) -> list[TranscriptRef]
    # orden: [user, *rejected, final]; falla del store → TranscriptWriteError
class TranscriptReader:
    def __init__(self, store, uow_factory, views: ViewService, keys, ids)
    def read_rendered(self, run_id, reader, on_behalf_of) -> list[RenderedEntry]      # M7 render, purpose "transcript_read"
# agent-telemetry
def span(name, **attrs) -> ContextManager       # agrega run_id, turn_id, session_id, agentcore.release
# Replay
class ReplayReport: mode; run_id; release; verdict: Literal["match", "diverged", "chain_broken"]
                    first_divergence: {event_seq, expected, actual} | None; chain_broken_at: int | None; duration_ms: int | None
class Replayer:
    def __init__(self, engine: EngineRunner, clock: Clock, *, audit: AuditSink | None = None,
                 transcript: TranscriptStore | None = None, definitions: ToolDefinitions | None = None)
    def replay(self, source: Fixture | RunId, mode: Literal["fixture", "audit"]) -> ReplayReport
```
- Añade al final una sección `## 12. Decisiones de la rev. 2` copiando las decisiones 1–17 de este plan (`docs/superpowers/plans/2026-09-29-m11-auditoria-transcript-replay.md`), y en §11 "Abiertos" cambia el punto del transcript store por: "Resuelto en rev. 2 (falla el turno); queda abierta la idempotencia del reintento con la unidad 7".
- Añade a §3.1: "Fórmula exacta: decisión 2".

- [ ] **Step 6: Verificar y commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check . && uv run pytest -q`
Expected: verde.

```bash
git add pyproject.toml uv.lock .importlinter agent_telemetry tests/m11 tests/integration docs/specs/motor/m11-auditoria-transcript-replay.md
git commit -m "docs(m11): spec rev. 2, dependencias y contratos de import" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Cadena de hash (función pura)

**Files:**
- Create: `agent_core/audit/chain.py`, `tests/m11/helpers.py`, `tests/m11/test_chain.py`

**Interfaces:**
- Consumes: `EngineEvent`, `AnyEvent`, `canonical_bytes`, `to_jsonable`, `sha256_hex`, `dumps`, `loads` (`agent_core.domain`).
- Produces (las usan las Tasks 3, 8, 9, 12):
  - `type ChainedEvent = EngineEvent`
  - `class ChainError(ValueError)`
  - `class ChainCheck(Model): ok: bool; broken_at: int | None = None; reason: str | None = None`
  - `def genesis_hash(run_id: str) -> str`
  - `def event_hash(event: EngineEvent, prev_hash: str) -> str`
  - `def chain_events(run_id: str, events: list[EngineEvent], last: EngineEvent | None) -> list[EngineEvent]`
  - `def check_chain(run_id: str, events: list[EngineEvent]) -> ChainCheck`
  - `def event_to_json(event: EngineEvent) -> str` y `def event_from_json(text: str) -> EngineEvent`

- [ ] **Step 1: Helpers de prueba y prueba que falla**

`tests/m11/helpers.py`:
```python
"""Ayudantes de las pruebas de M11. Solo datos sintéticos."""

from typing import Any

from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent
from tests.m00.samples import SAMPLE_PAYLOADS, make_event

_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnyEvent)
EVENT_TYPES = tuple(SAMPLE_PAYLOADS)


def event(event_type: str = "turn_completed", *, run_id: str = "run-0001", n: int = 1,
          **over: Any) -> EngineEvent:
    """Evento sin encadenar (seq/prev_hash/hash vacíos) de un tipo de M0."""
    raw = {**make_event(event_type), "run_id": run_id, "event_id": f"event-{run_id}-{n:04d}", **over}
    result: EngineEvent = _ADAPTER.validate_python(raw)
    return result


def events_of(run_id: str, count: int) -> list[EngineEvent]:
    """`count` eventos sin encadenar de tipos variados."""
    return [event(EVENT_TYPES[i % len(EVENT_TYPES)], run_id=run_id, n=i + 1) for i in range(count)]
```

`tests/m11/test_chain.py`:
```python
"""T-M11-03 (unidad): un evento alterado rompe la cadena. Fórmula del hash (decisión 2)."""

import pytest

from agent_core.audit.chain import (
    ChainError,
    check_chain,
    chain_events,
    event_from_json,
    event_hash,
    event_to_json,
    genesis_hash,
)
from agent_core.domain import sha256_hex
from tests.m11.helpers import EVENT_TYPES, event, events_of


def test_genesis_is_sha256_of_prefix_and_run_id() -> None:
    assert genesis_hash("run-0001") == sha256_hex(b"agentcore:run-0001")


def test_first_event_links_to_genesis_and_seq_is_contiguous() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 3), last=None)
    assert [e.seq for e in chained] == [0, 1, 2]
    assert chained[0].prev_hash == genesis_hash("run-0001")
    assert chained[1].prev_hash == chained[0].hash
    assert chained[2].prev_hash == chained[1].hash


def test_hash_formula() -> None:
    [first] = chain_events("run-0001", [event()], last=None)
    assert first.hash == event_hash(first, genesis_hash("run-0001"))
    # el hash no depende de sí mismo: recalcular con hash=None da lo mismo
    assert first.hash == event_hash(first.model_copy(update={"hash": None}), first.prev_hash or "")


def test_continues_from_last_event() -> None:
    first = chain_events("run-0001", events_of("run-0001", 2), last=None)
    more = chain_events("run-0001", [event(n=9)], last=first[-1])
    assert more[0].seq == 2 and more[0].prev_hash == first[-1].hash


def test_rejects_foreign_run_and_already_chained_events() -> None:
    with pytest.raises(ChainError):
        chain_events("run-0001", [event(run_id="run-0002")], last=None)
    [done] = chain_events("run-0001", [event()], last=None)
    with pytest.raises(ChainError):
        chain_events("run-0001", [done], last=None)


def test_valid_chain_and_empty_chain_are_ok() -> None:
    assert check_chain("run-0001", []).ok
    assert check_chain("run-0001", chain_events("run-0001", events_of("run-0001", 5), None)).ok


def test_altered_payload_breaks_at_that_seq() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 4), None)
    forged = chained[2].model_copy(update={"release": "rel-otra"})
    result = check_chain("run-0001", [*chained[:2], forged, chained[3]])
    assert (result.ok, result.broken_at) == (False, 2)


def test_deleted_reordered_or_wrong_genesis_break() -> None:
    chained = chain_events("run-0001", events_of("run-0001", 4), None)
    assert check_chain("run-0001", [chained[0], chained[2], chained[3]]).broken_at == 1
    assert check_chain("run-0001", [chained[1], chained[0], chained[2]]).broken_at == 0
    assert not check_chain("run-0999", chained).ok  # otra génesis
    assert check_chain("run-0001", chained[1:]).broken_at == 1  # falta el inicio


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_hash_survives_json_roundtrip_for_every_event_type(event_type: str) -> None:
    """Postgres guarda el evento como JSON: el hash debe recalcularse igual tras leerlo."""
    [chained] = chain_events("run-0001", [event(event_type)], None)
    back = event_from_json(event_to_json(chained))
    assert back == chained
    assert check_chain("run-0001", [back]).ok
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/m11/test_chain.py -v`
Expected: FAIL (`ModuleNotFoundError: agent_core.audit.chain`).

- [ ] **Step 3: Implementar**

`agent_core/audit/chain.py`:
```python
"""Cadena de hash por run (M11 §3.1, decisiones 1 y 2). Funciones puras: sin I/O, sin reloj, sin IDs."""

from typing import Any

from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent, canonical_bytes, dumps, loads, sha256_hex, to_jsonable
from agent_core.domain.base import Model

type ChainedEvent = EngineEvent  # seq, prev_hash y hash no nulos

_GENESIS_PREFIX = b"agentcore:"
_EVENTS: TypeAdapter[Any] = TypeAdapter(AnyEvent)


class ChainError(ValueError):
    """Los eventos no se pueden encadenar (run ajeno, ya encadenados o `last` sin hash)."""


class ChainCheck(Model):
    ok: bool
    broken_at: int | None = None
    reason: str | None = None


def genesis_hash(run_id: str) -> str:
    """`hash_0`: constante por run. Es el `prev_hash` del evento con `seq = 0`."""
    return sha256_hex(_GENESIS_PREFIX + run_id.encode())


def event_hash(event: EngineEvent, prev_hash: str) -> str:
    """`sha256(JCS(evento sin el campo hash) ‖ prev_hash)`. Incluye `seq` y `prev_hash` del evento."""
    body = to_jsonable(event)
    body.pop("hash", None)
    return sha256_hex(canonical_bytes(body) + prev_hash.encode("ascii"))


def chain_events(run_id: str, events: list[EngineEvent], last: EngineEvent | None) -> list[EngineEvent]:
    if last is None:
        seq, prev = 0, genesis_hash(run_id)
    else:
        if last.seq is None or last.hash is None:
            raise ChainError("el último evento del run no está encadenado")
        seq, prev = last.seq + 1, last.hash
    chained: list[EngineEvent] = []
    for event in events:
        if event.run_id != run_id:
            raise ChainError(f"evento {event.event_id} de otro run")
        if event.seq is not None or event.prev_hash is not None or event.hash is not None:
            raise ChainError(f"evento {event.event_id} ya encadenado")
        linked = event.model_copy(update={"seq": seq, "prev_hash": prev})
        linked = linked.model_copy(update={"hash": event_hash(linked, prev)})
        chained.append(linked)
        seq, prev = seq + 1, linked.hash or ""
    return chained


def check_chain(run_id: str, events: list[EngineEvent]) -> ChainCheck:
    """`ok`, o `broken_at(seq)` con el `seq` esperado en la primera posición inconsistente."""
    prev = genesis_hash(run_id)
    for index, event in enumerate(events):
        problem = None
        if event.run_id != run_id:
            problem = "run distinto"
        elif event.seq != index:
            problem = "seq no contiguo"
        elif event.prev_hash != prev:
            problem = "prev_hash no coincide"
        elif event.hash is None or event.hash != event_hash(event, prev):
            problem = "hash no coincide"
        if problem is not None:
            return ChainCheck(ok=False, broken_at=index, reason=problem)
        prev = event.hash or ""
    return ChainCheck(ok=True)


def event_to_json(event: EngineEvent) -> str:
    return dumps(event)


def event_from_json(text: str) -> EngineEvent:
    result: EngineEvent = _EVENTS.validate_python(loads(text))
    return result
```

- [ ] **Step 4: Ver que pasa**

Run: `uv run pytest tests/m11/test_chain.py -v && uv run mypy && uv run lint-imports`
Expected: PASS. Si `test_hash_survives_json_roundtrip…` falla por un tipo (p. ej. `Decimal("0.0000")` o `float`), **ese es el hallazgo**: ajusta `event_to_json` para que el ida y vuelta conserve el hash (no cambies la fórmula) y anótalo en el spec.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/chain.py tests/m11
git commit -m "feat(m11): cadena de hash por run" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `AuditLog` sobre la UoW

**Files:**
- Create: `agent_core/audit/log.py`, `tests/m11/test_audit_log.py`

**Interfaces:**
- Consumes: `chain_events`, `check_chain`, `ChainCheck`, `ChainedEvent` (Task 2); `AuditSink`, `UnitOfWork`, `UnitOfWorkFactory` (`agent_core.ports`); `RunState`, `EngineEvent` (`agent_core.domain`).
- Produces:
  - `class AuditLog: __init__(self, sink: AuditSink, uow_factory: UnitOfWorkFactory | None = None)`
  - `append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]`
  - `append_standalone(self, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]`
  - `verify_chain(self, run_id: str) -> ChainCheck`
  - `recorder(self) -> Callable[[UnitOfWork, RunState, list[EngineEvent]], None]`

- [ ] **Step 1: Prueba que falla**

`tests/m11/test_audit_log.py`:
```python
"""AuditLog sobre el UoW en memoria: T-M11-03 (cadena rota vía el log) y encadenado entre commits."""

from agent_core.audit.log import AuditLog
from testing.builders import run_state
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.helpers import events_of


def make() -> tuple[InMemoryStore, AuditLog]:
    store = InMemoryStore()
    return store, AuditLog(InMemoryAuditSink(store), store.uow)


def test_append_chains_within_one_uow_and_across_commits() -> None:
    store, log = make()
    with store.uow() as uow:
        first = log.append(uow, "run-0001", events_of("run-0001", 2))
        second = log.append(uow, "run-0001", events_of("run-0001", 1))  # ve los eventos pendientes
        uow.commit()
    assert [e.seq for e in first + second] == [0, 1, 2]
    with store.uow() as uow:
        third = log.append(uow, "run-0001", events_of("run-0001", 2))
        uow.commit()
    assert [e.seq for e in third] == [3, 4] and third[0].prev_hash == second[-1].hash
    assert log.verify_chain("run-0001").ok


def test_uncommitted_append_leaves_no_trace() -> None:
    store, log = make()
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 2))
    assert InMemoryAuditSink(store).read("run-0001") == []
    assert log.verify_chain("run-0001").ok


def test_verify_chain_detects_tampering_in_storage() -> None:
    store, log = make()
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 4))
        uow.commit()
    stored = store.events["run-0001"]
    stored[1] = stored[1].model_copy(update={"release": "rel-alterada"})
    check = log.verify_chain("run-0001")
    assert (check.ok, check.broken_at) == (False, 1)


def test_runs_have_independent_chains() -> None:
    store, log = make()
    with store.uow() as uow:
        a = log.append(uow, "run-0001", events_of("run-0001", 1))
        b = log.append(uow, "run-0002", events_of("run-0002", 1))
        uow.commit()
    assert a[0].seq == b[0].seq == 0 and a[0].prev_hash != b[0].prev_hash


def test_append_standalone_commits_its_own_uow() -> None:
    store, log = make()
    out = log.append_standalone("run-0001", events_of("run-0001", 2))
    assert [e.seq for e in out] == [0, 1] and log.verify_chain("run-0001").ok


def test_recorder_has_event_recorder_shape_and_chains() -> None:
    store, log = make()
    state = run_state(run_id="run-0001")
    with store.uow() as uow:
        log.recorder()(uow, state, events_of("run-0001", 2))
        uow.commit()
    assert log.verify_chain("run-0001").ok and len(store.events["run-0001"]) == 2


def test_empty_append_is_a_noop() -> None:
    store, log = make()
    with store.uow() as uow:
        assert log.append(uow, "run-0001", []) == []
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/m11/test_audit_log.py -v`
Expected: FAIL (`ModuleNotFoundError: agent_core.audit.log`).

- [ ] **Step 3: Implementar**

`agent_core/audit/log.py`:
```python
"""`AuditLog` (M11 §2): encadena y persiste dentro de la transacción del turno (o de una escritura de M3)."""

from collections.abc import Callable

from agent_core.audit.chain import ChainCheck, ChainedEvent, chain_events, check_chain
from agent_core.domain import EngineEvent, RunState
from agent_core.ports import AuditSink, UnitOfWork, UnitOfWorkFactory


class AuditLog:
    def __init__(self, sink: AuditSink, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._sink = sink
        self._uow_factory = uow_factory

    def append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]:
        """Asigna `seq`, `prev_hash` y `hash` y los agrega a la UoW. Llamadas sucesivas en la misma UoW
        siguen la cadena (`UnitOfWork.last_event` ve lo pendiente)."""
        if not events:
            return []
        chained = chain_events(run_id, events, uow.last_event(run_id))
        uow.append_events(run_id, chained)
        return chained

    def append_standalone(self, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]:
        """Eventos fuera de un turno (p. ej. `access_denied` de M9): UoW propia, encadenada y commiteada."""
        if self._uow_factory is None:
            raise RuntimeError("AuditLog sin uow_factory: no puede abrir una UoW propia")
        with self._uow_factory() as uow:
            chained = self.append(uow, run_id, events)
            uow.commit()
        return chained

    def verify_chain(self, run_id: str) -> ChainCheck:
        return check_chain(run_id, self._sink.read(run_id))

    def recorder(self) -> Callable[[UnitOfWork, RunState, list[EngineEvent]], None]:
        """Con la firma del `EventRecorder` de M3; M4 lo cablea como `ActionContext.record`."""

        def record(uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
            self.append(uow, state.run_id, events)

        return record
```

- [ ] **Step 4: Ver que pasa**

Run: `uv run pytest tests/m11 -v && uv run mypy && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/log.py tests/m11/test_audit_log.py
git commit -m "feat(m11): AuditLog con append, verify_chain y recorder" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Transcript (escritura): `InMemoryTranscript` y `TurnRecorder`

**Files:**
- Create: `testing/fakes/transcript.py`, `tests/contracts/test_transcript_contract.py`, `agent_core/audit/transcript.py`, `tests/m11/test_turn_recorder.py`

**Interfaces:**
- Consumes: `TranscriptStore`, `KeyProvider` (`agent_core.ports`); `TranscriptEntry`, `TranscriptRef`, `RejectedDraft`, `Fingerprint` (`agent_core.domain`); `fingerprint`, `verify_fingerprint` (`agent_core.views`).
- Produces:
  - `class InMemoryTranscript` con `append(entry) -> str` (`entry-0001`…), `read(run_id)`, `recent_turns(run_id, n)`, y de prueba: `fail_next(times: int = 1)` y `delete_run(run_id)`.
  - `class TranscriptWriteError(Exception)` con atributos `run_id`, `turn_id`.
  - `class TurnRecorder: __init__(self, store: TranscriptStore, keys: KeyProvider)`; `record_turn(self, run_id: str, turn_id: str, user_msg_model: str, final_model: str, rejected: list[RejectedDraft]) -> list[TranscriptRef]`.

- [ ] **Step 1: Pruebas que fallan**

`tests/contracts/test_transcript_contract.py` (suite de contrato reutilizable por el adaptador real de la unidad 7):
```python
"""Contrato de `TranscriptStore`: cada `check_*` corre contra cualquier backend."""

from collections.abc import Callable

import pytest

from agent_core.domain import TranscriptEntry
from agent_core.ports import TranscriptStore
from testing.fakes.transcript import InMemoryTranscript

BACKENDS: list[Callable[[], TranscriptStore]] = [InMemoryTranscript]


def entry(run_id: str = "run-0001", turn_id: str = "turn-0001", role: str = "user",
          text: str = "hola ⟦name:1⟧") -> TranscriptEntry:
    return TranscriptEntry.model_validate(
        {"run_id": run_id, "turn_id": turn_id, "role": role, "text_model": text})


@pytest.fixture(params=BACKENDS)
def store(request: pytest.FixtureRequest) -> TranscriptStore:
    return request.param()  # type: ignore[no-any-return]


def test_append_returns_distinct_ids_and_read_keeps_order(store: TranscriptStore) -> None:
    a = store.append(entry(text="uno"))
    b = store.append(entry(role="assistant", text="dos"))
    assert a != b
    assert [e.text_model for e in store.read("run-0001")] == ["uno", "dos"]


def test_read_is_scoped_by_run(store: TranscriptStore) -> None:
    store.append(entry(run_id="run-0001"))
    assert store.read("run-0002") == []


def test_recent_turns_returns_last_n_entries_oldest_first(store: TranscriptStore) -> None:
    for i in range(5):
        store.append(entry(turn_id=f"turn-{i}", text=f"t{i}"))
    assert [e.text_model for e in store.recent_turns("run-0001", 2)] == ["t3", "t4"]
    assert store.recent_turns("run-0001", 0) == []
```

`tests/m11/test_turn_recorder.py`:
```python
"""T-M11-06 (huella HMAC con kid) y falla del transcript store (decisión 4)."""

import hashlib

import pytest

from agent_core.audit.transcript import TranscriptWriteError, TurnRecorder
from agent_core.domain import RejectedDraft
from agent_core.views import verify_fingerprint
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.transcript import InMemoryTranscript

REJECTED = [RejectedDraft(text_model="borrador ⟦doc:1⟧", reason="pii_en_claro", failures=["pii"])]


def make() -> tuple[InMemoryTranscript, FakeKeyProvider, TurnRecorder]:
    store, keys = InMemoryTranscript(), FakeKeyProvider.default()
    return store, keys, TurnRecorder(store, keys)


def test_t_m11_06_fingerprint_is_keyed_hmac_with_kid() -> None:
    store, keys, recorder = make()
    refs = recorder.record_turn("run-0001", "turn-0001", "hola", "respuesta", REJECTED)
    assert len(refs) == 3
    for ref, text in zip(refs, ["hola", "borrador ⟦doc:1⟧", "respuesta"], strict=True):
        assert ref.fingerprint.alg == "HMAC-SHA256" and ref.fingerprint.kid == "fp-1"
        assert ref.fingerprint.value != hashlib.sha256(text.encode()).hexdigest()  # nunca sha256 sin clave
        assert verify_fingerprint(text, ref.fingerprint, keys)


def test_entries_are_stored_in_order_with_roles_and_reason() -> None:
    store, _, recorder = make()
    refs = recorder.record_turn("run-0001", "turn-0001", "hola", "respuesta", REJECTED)
    stored = store.read("run-0001")
    assert [(e.role, e.reason) for e in stored] == [
        ("user", None), ("rejected_draft", "pii_en_claro"), ("assistant", None)]
    assert refs[0].entry_id != refs[2].entry_id


def test_store_failure_fails_the_turn_without_leaking_text() -> None:
    store, _, recorder = make()
    store.fail_next()
    with pytest.raises(TranscriptWriteError) as info:
        recorder.record_turn("run-0001", "turn-0001", "hola secreta", "ok", [])
    assert "secreta" not in str(info.value) and "secreta" not in repr(info.value)


def test_fingerprints_are_computed_before_any_write() -> None:
    store, keys, recorder = make()
    keys._current.pop(next(iter(keys._current)))  # type: ignore[attr-defined]  # sin kid vigente: falla al calcular
    with pytest.raises(KeyError):
        recorder.record_turn("run-0001", "turn-0001", "hola", "ok", [])
    assert store.read("run-0001") == []
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/contracts/test_transcript_contract.py tests/m11/test_turn_recorder.py -v`
Expected: FAIL (módulos inexistentes).

- [ ] **Step 3: Implementar el doble**

`testing/fakes/transcript.py`:
```python
"""`TranscriptStore` en memoria (M11). Falla inyectable (`fail_next`) y supresión (`delete_run`), que el
puerto real de la unidad 7 ofrece por su cuenta (retención y supresión propias)."""

import threading
from copy import deepcopy
from typing import TYPE_CHECKING

from agent_core.domain import TranscriptEntry


class TranscriptUnavailable(Exception):
    """Falla simulada del store."""


class InMemoryTranscript:
    def __init__(self) -> None:
        self._entries: dict[str, list[tuple[str, TranscriptEntry]]] = {}
        self._count = 0
        self._failures = 0
        self._lock = threading.RLock()

    def fail_next(self, times: int = 1) -> None:
        with self._lock:
            self._failures += times

    def append(self, entry: TranscriptEntry) -> str:
        with self._lock:
            if self._failures > 0:
                self._failures -= 1
                raise TranscriptUnavailable("store caído")
            self._count += 1
            entry_id = f"entry-{self._count:04d}"
            self._entries.setdefault(entry.run_id, []).append((entry_id, deepcopy(entry)))
            return entry_id

    def read(self, run_id: str) -> list[TranscriptEntry]:
        with self._lock:
            return [deepcopy(e) for _, e in self._entries.get(run_id, [])]

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]:
        with self._lock:
            entries = self._entries.get(run_id, [])
            return [deepcopy(e) for _, e in entries[-n:]] if n > 0 else []

    def delete_run(self, run_id: str) -> None:
        """Supresión (unidad 7). No toca el log de auditoría: T-M11-08."""
        with self._lock:
            self._entries.pop(run_id, None)


if TYPE_CHECKING:
    from agent_core.ports import TranscriptStore

    def _conforms(x: InMemoryTranscript) -> TranscriptStore:
        return x
```

- [ ] **Step 4: Implementar `TurnRecorder`**

`agent_core/audit/transcript.py`:
```python
"""Transcript (M11 §3.3): escritura por turno con huella con clave. Nunca razonamiento intermedio."""

from agent_core.domain import RejectedDraft, TranscriptEntry, TranscriptRef
from agent_core.ports import KeyProvider, TranscriptStore
from agent_core.views import fingerprint


class TranscriptWriteError(Exception):
    """El transcript store falló: se falla el turno (decisión 4). Sin el mensaje del store ni el texto."""

    def __init__(self, run_id: str, turn_id: str, cause: str) -> None:
        super().__init__(f"transcript no disponible (run={run_id}, turn={turn_id}, causa={cause})")
        self.run_id = run_id
        self.turn_id = turn_id


class TurnRecorder:
    def __init__(self, store: TranscriptStore, keys: KeyProvider) -> None:
        self._store = store
        self._keys = keys

    def record_turn(self, run_id: str, turn_id: str, user_msg_model: str, final_model: str,
                    rejected: list[RejectedDraft]) -> list[TranscriptRef]:
        """Orden de las referencias: `[user, *rejected, final]`. Los textos ya vienen en vista `model`."""
        planned: list[tuple[str, str, str | None]] = [
            ("user", user_msg_model, None),
            *[("rejected_draft", d.text_model, d.reason) for d in rejected],
            ("assistant", final_model, None),
        ]
        fingerprints = [fingerprint(text, self._keys) for _, text, _ in planned]  # antes de escribir
        refs: list[TranscriptRef] = []
        for (role, text, reason), fp in zip(planned, fingerprints, strict=True):
            entry = TranscriptEntry.model_validate(
                {"run_id": run_id, "turn_id": turn_id, "role": role, "text_model": text, "reason": reason})
            try:
                entry_id = self._store.append(entry)
            except Exception as exc:  # cualquier falla del store: el turno falla y se reintenta
                raise TranscriptWriteError(run_id, turn_id, type(exc).__name__) from None
            refs.append(TranscriptRef(entry_id=entry_id, fingerprint=fp))
        return refs
```

- [ ] **Step 5: Ver que pasa**

Run: `uv run pytest tests/contracts/test_transcript_contract.py tests/m11/test_turn_recorder.py -v && uv run mypy && uv run lint-imports`
Expected: PASS. (`test_fingerprints_are_computed_before_any_write` toca un atributo privado del doble; si `FakeKeyProvider` cambia, usa una subclase de prueba cuyo `current_kid` lance `KeyError`.)

- [ ] **Step 6: Commit**

```bash
git add testing/fakes/transcript.py tests/contracts/test_transcript_contract.py agent_core/audit/transcript.py tests/m11/test_turn_recorder.py
git commit -m "feat(m11): TurnRecorder e InMemoryTranscript con suite de contrato" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `TranscriptReader` (lectura renderizada) y supresión

**Files:**
- Modify: `agent_core/audit/transcript.py`
- Create: `tests/m11/test_transcript_reader.py`

**Interfaces:**
- Consumes: `TranscriptStore`, `UnitOfWorkFactory`, `KeyProvider`, `IdSource` (`agent_core.ports`); `ViewService`, `TokenVault` (`agent_core.views`); `Principal`, `OnBehalfOf`, `RunState` (`agent_core.domain`); `AuditLog` (Task 3) solo en pruebas.
- Produces:
  - `class RunNotFound(LookupError)`
  - `class RenderedEntry(Model)`: `turn_id: str; role: Literal["user","assistant","rejected_draft"]; text: str = Field(repr=False); reason: str | None = None; unknown_tokens: list[str] = []`
  - `class TranscriptReader: __init__(self, store, uow_factory, views: ViewService, keys: KeyProvider, ids: IdSource)`; `read_rendered(self, run_id: str, reader: Principal, on_behalf_of: OnBehalfOf | None) -> list[RenderedEntry]`
  - constante `TRANSCRIPT_PURPOSE = "transcript_read"`.

- [ ] **Step 1: Pruebas que fallan**

`tests/m11/test_transcript_reader.py`:
```python
"""T-M11-07 (permisos del lector) y T-M11-08 (suprimir el transcript no rompe la cadena)."""

import pytest

from agent_core.audit.log import AuditLog
from agent_core.audit.transcript import (
    TRANSCRIPT_PURPOSE,
    RunNotFound,
    TranscriptReader,
    TurnRecorder,
)
from agent_core.views import TokenVault
from testing.builders import advisor_with_delegation, principal, run_state
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript
from tests.m07.helpers import FieldAuthz, make_service
from tests.m11.helpers import events_of

DOC = "1023456789"  # documento inventado


def setup() -> tuple[InMemoryStore, InMemoryTranscript, FakeKeyProvider, TokenVault, str]:
    store, transcript, keys = InMemoryStore(), InMemoryTranscript(), FakeKeyProvider.default()
    vault = TokenVault("run-0001", keys, FakeIds())
    token = vault.tokenize(DOC, "document_number", "doc")
    with store.uow() as uow:
        uow.save_run(run_state(token_map=vault.seal()), 0)
        uow.commit()
    TurnRecorder(transcript, keys).record_turn(
        "run-0001", "turn-0001", f"mi documento es {token}", "ok", [])
    return store, transcript, keys, vault, token


def reader_for(store: InMemoryStore, transcript: InMemoryTranscript, keys: FakeKeyProvider,
               grants: set[tuple[str, str, str]]) -> TranscriptReader:
    views = make_service(keys=keys, authz=FieldAuthz(grants))
    return TranscriptReader(transcript, store.uow, views, keys, FakeIds())


def test_t_m11_07_authorized_advisor_sees_value_and_others_see_mask() -> None:
    store, transcript, keys, _, _ = setup()
    advisor, obo = advisor_with_delegation()
    allowed = reader_for(store, transcript, keys, {("adv-7", "document_number", TRANSCRIPT_PURPOSE)})
    shown = allowed.read_rendered("run-0001", advisor, obo)
    assert DOC in shown[0].text and shown[1].text == "ok"
    denied = reader_for(store, transcript, keys, set())
    hidden = denied.read_rendered("run-0001", advisor, obo)
    assert DOC not in hidden[0].text and "***" in hidden[0].text


def test_advisor_without_delegation_never_sees_the_value() -> None:
    store, transcript, keys, _, _ = setup()
    advisor, _ = advisor_with_delegation()
    reader = reader_for(store, transcript, keys, {("adv-7", "document_number", TRANSCRIPT_PURPOSE)})
    assert DOC not in reader.read_rendered("run-0001", advisor, None)[0].text


def test_unknown_tokens_are_reported_not_rendered() -> None:
    store, transcript, keys, _, _ = setup()
    TurnRecorder(transcript, keys).record_turn("run-0001", "turn-0002", "⟦doc:99⟧", "ok", [])
    reader = reader_for(store, transcript, keys, set())
    entries = reader.read_rendered("run-0001", principal(), None)
    assert entries[2].unknown_tokens == ["⟦doc:99⟧"]


def test_rendered_entry_repr_hides_text() -> None:
    store, transcript, keys, _, _ = setup()
    entries = reader_for(store, transcript, keys, set()).read_rendered("run-0001", principal(), None)
    assert "mi documento" not in repr(entries[0])


def test_unknown_run_raises() -> None:
    store, transcript, keys, _, _ = setup()
    with pytest.raises(RunNotFound):
        reader_for(store, transcript, keys, set()).read_rendered("run-9999", principal(), None)


def test_t_m11_08_suppressing_transcript_does_not_break_the_chain() -> None:
    store, transcript, keys, _, _ = setup()
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 3))
        uow.commit()
    transcript.delete_run("run-0001")
    assert transcript.read("run-0001") == []
    assert log.verify_chain("run-0001").ok
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/m11/test_transcript_reader.py -v`
Expected: FAIL (`ImportError: TranscriptReader`).

- [ ] **Step 3: Implementar**

Añade a `agent_core/audit/transcript.py` (imports: `Literal`, `Field`, `Model`, `OnBehalfOf`, `Principal`, `UnitOfWorkFactory`, `IdSource`, `TokenVault`, `ViewService`):
```python
TRANSCRIPT_PURPOSE = "transcript_read"


class RunNotFound(LookupError):
    """El run no existe (o el lector no puede saberlo: M9 decide qué código HTTP devuelve)."""


class RenderedEntry(Model):
    turn_id: str
    role: Literal["user", "assistant", "rejected_draft"]
    text: str = Field(repr=False)
    reason: str | None = None
    unknown_tokens: list[str] = Field(default_factory=list)


class TranscriptReader:
    """Autoriza por campo (política de la unidad 3 vía `AuthzPort`, dentro de `ViewService.render`)."""

    def __init__(self, store: TranscriptStore, uow_factory: UnitOfWorkFactory, views: ViewService,
                 keys: KeyProvider, ids: IdSource) -> None:
        self._store = store
        self._uow_factory = uow_factory
        self._views = views
        self._keys = keys
        self._ids = ids

    def read_rendered(self, run_id: str, reader: Principal,
                      on_behalf_of: OnBehalfOf | None) -> list[RenderedEntry]:
        with self._uow_factory() as uow:
            state = uow.load_run(run_id)
        if state is None:
            raise RunNotFound(run_id)
        vault = (TokenVault.open(state.token_map, run_id, self._keys, self._ids)
                 if state.token_map is not None else TokenVault(run_id, self._keys, self._ids))
        rendered: list[RenderedEntry] = []
        for entry in self._store.read(run_id):
            out = self._views.render(entry.text_model, vault, reader, TRANSCRIPT_PURPOSE, on_behalf_of)
            rendered.append(RenderedEntry(turn_id=entry.turn_id, role=entry.role, text=out.text,
                                          reason=entry.reason, unknown_tokens=out.unknown_tokens))
        return rendered
```
(Importa `from agent_core.domain.base import Model` y `from pydantic import Field`.)

- [ ] **Step 4: Ver que pasa**

Run: `uv run pytest tests/m11 -v && uv run mypy && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/transcript.py tests/m11/test_transcript_reader.py
git commit -m "feat(m11): TranscriptReader con render autorizado (T-M11-07, T-M11-08)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `agent_telemetry` (spans, contexto, OTLP, logs JSON)

**Files:**
- Create: `agent_telemetry/context.py`, `agent_telemetry/spans.py`, `agent_telemetry/setup.py`, `agent_telemetry/logging.py`
- Modify: `agent_telemetry/__init__.py`
- Create: `tests/m11/test_telemetry.py`

**Interfaces:**
- Consumes: `opentelemetry` (`trace`, `sdk.trace`, `sdk.trace.export.in_memory_span_exporter` en pruebas). No importa `agent_core`.
- Produces:
  - `bind(*, run_id=None, turn_id=None, session_id=None, release=None) -> ContextManager[None]` (contextvars)
  - `span(name: str, **attrs: Any) -> ContextManager[Span]` (lanza `MissingTelemetryContext` si falta `run_id` o `agentcore.release`); nombres de span como constantes `INVOKE_AGENT`, `DECIDE`, `RULE`, `EXECUTE_TOOL`, `CHAT`
  - `set_content(span: Span, key: str, audit_view: object) -> None` y `configure(capture_content: bool = False)`
  - `current_trace_id() -> str | None`
  - `setup_tracing(endpoint: str | None = None, exporter: SpanExporter | None = None) -> TracerProvider`
  - `SEMCONV_VERSION: str`
  - `JsonLogFormatter(logging.Formatter)`

- [ ] **Step 1: Prueba que falla**

`tests/m11/test_telemetry.py`:
```python
"""T-M11-09 (mitad telemetría): todo span lleva run_id y agentcore.release; trace_id disponible;
contenido apagado por defecto; backend caído no rompe."""

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_telemetry.setup import setup_tracing


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    setup_tracing(exporter=exp)
    tel.configure(capture_content=False)
    return exp


def test_every_span_carries_run_and_release_and_nesting_works(exporter: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", turn_id="turn-0001", session_id="session-0001", release="rel-1"):
        with tel.span(tel.INVOKE_AGENT, agentcore_agent="atencion@1.0.0"):
            with tel.span(tel.DECIDE, agentcore_node="pedir_cargo", gen_ai_operation_name="chat"):
                pass
            with tel.span(tel.EXECUTE_TOOL):
                pass
    spans = exporter.get_finished_spans()
    assert {s.name for s in spans} == {tel.INVOKE_AGENT, tel.DECIDE, tel.EXECUTE_TOOL}
    for s in spans:
        attrs = dict(s.attributes or {})
        assert attrs["run_id"] == "run-0001" and attrs["agentcore.release"] == "rel-1"
        assert attrs["turn_id"] == "turn-0001" and attrs["session_id"] == "session-0001"
    root = next(s for s in spans if s.name == tel.INVOKE_AGENT)
    assert dict(root.attributes or {})["agentcore.agent"] == "atencion@1.0.0"
    child = next(s for s in spans if s.name == tel.DECIDE)
    assert child.parent is not None and child.parent.span_id == root.context.span_id
    assert dict(child.attributes or {})["gen_ai.operation.name"] == "chat"


def test_span_without_context_is_rejected() -> None:
    with pytest.raises(tel.MissingTelemetryContext):
        with tel.span(tel.CHAT):
            pass


def test_trace_id_is_available_inside_a_span(exporter: InMemorySpanExporter) -> None:
    assert tel.current_trace_id() is None
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT):
        trace_id = tel.current_trace_id()
    assert trace_id is not None and len(trace_id) == 32
    assert format(exporter.get_finished_spans()[0].context.trace_id, "032x") == trace_id


def test_content_capture_is_off_by_default_and_uses_given_audit_view(exporter: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"):
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
        tel.configure(capture_content=True)
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
    off, on = exporter.get_finished_spans()
    assert "agentcore.content.input" not in (off.attributes or {})
    assert json.loads(str((on.attributes or {})["agentcore.content.input"])) == {"texto": "⟦name:1⟧"}


def test_failing_backend_never_breaks_the_caller() -> None:
    class Broken(SpanExporter):
        def export(self, spans):  # type: ignore[no-untyped-def]
            raise RuntimeError("phoenix caído")

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(Broken()))
    trace.set_tracer_provider(provider)  # puede ignorarse si ya hay uno global; ver setup_tracing
    setup_tracing(exporter=Broken())
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass  # no lanza


def test_json_logs_carry_run_id_and_trace_id(exporter: InMemorySpanExporter) -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hola", None, None)
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        line = json.loads(tel.JsonLogFormatter().format(record))
    assert line["run_id"] == "run-0001" and line["agentcore.release"] == "rel-1" and len(line["trace_id"]) == 32
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/m11/test_telemetry.py -v`
Expected: FAIL (`AttributeError: module 'agent_telemetry' has no attribute 'bind'`).

- [ ] **Step 3: Implementar**

`agent_telemetry/context.py`:
```python
"""Contexto de correlación del run (ADR 0003 #4): run_id, turn_id, session_id y release."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_CTX: ContextVar[Mapping[str, str]] = ContextVar("agent_telemetry_ctx", default=MappingProxyType({}))


@contextmanager
def bind(*, run_id: str | None = None, turn_id: str | None = None, session_id: str | None = None,
         release: str | None = None) -> Iterator[None]:
    values = {"run_id": run_id, "turn_id": turn_id, "session_id": session_id, "agentcore.release": release}
    token = _CTX.set({**_CTX.get(), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _CTX.reset(token)


def current() -> Mapping[str, str]:
    return _CTX.get()
```

`agent_telemetry/spans.py`:
```python
"""Spans `invoke_agent` › `agentcore.decide`, `agentcore.rule`, `execute_tool`, `chat` (M11 §3.2)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from agent_telemetry.context import current

# ADR 0003: la versión de semconv GenAI queda fijada. Verifícala contra `opentelemetry-semantic-conventions`
# instalado antes de cambiarla; ver decisión 15 del plan de M11.
SEMCONV_VERSION = "1.37.0"
INVOKE_AGENT, DECIDE, RULE, EXECUTE_TOOL, CHAT = (
    "invoke_agent", "agentcore.decide", "agentcore.rule", "execute_tool", "chat")
_REQUIRED = ("run_id", "agentcore.release")
_capture_content = False


class MissingTelemetryContext(RuntimeError):
    """Un span sin `run_id` o `agentcore.release` rompería la correlación (ADR 0003 #4)."""


def configure(*, capture_content: bool = False) -> None:
    global _capture_content
    _capture_content = capture_content


def _key(name: str) -> str:
    """`agentcore_flow` → `agentcore.flow`; `gen_ai_operation_name` → `gen_ai.operation.name`."""
    for prefix in ("agentcore_", "gen_ai_"):
        if name.startswith(prefix):
            return prefix[:-1] + "." + name[len(prefix):].replace("_", ".")
    return name


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    merged: dict[str, Any] = {**current(), **{_key(k): v for k, v in attrs.items() if v is not None}}
    missing = [k for k in _REQUIRED if k not in merged]
    if missing:
        raise MissingTelemetryContext(f"span {name!r} sin {', '.join(missing)}; usa bind(...)")
    tracer = trace.get_tracer("agent_telemetry", SEMCONV_VERSION,
                              schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}")
    with tracer.start_as_current_span(name, attributes=merged) as active:
        yield active


def set_content(active: Span, key: str, audit_view: object) -> None:
    """Contenido en trazas: apagado por defecto; si se activa, quien llama pasa la vista `audit`."""
    if _capture_content:
        active.set_attribute(f"agentcore.content.{key}", json.dumps(audit_view, default=str, ensure_ascii=False))


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
```

`agent_telemetry/setup.py`:
```python
"""Configuración del exportador: OTLP/HTTP a Phoenix en la demo (cambiar el endpoint = Langfuse)."""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter


def setup_tracing(endpoint: str | None = None, exporter: SpanExporter | None = None) -> TracerProvider:
    """`endpoint` p. ej. `http://localhost:6006/v1/traces`. Un `exporter` explícito manda (pruebas)."""
    if exporter is None:
        if endpoint is None:
            raise ValueError("indica endpoint OTLP o un exporter")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))  # fallas del backend: se registran y descartan
    trace.set_tracer_provider(provider)
    return provider
```
**Nota para quien implemente:** `trace.set_tracer_provider` solo se acepta una vez por proceso. Para que las pruebas puedan reconfigurar, `spans.py` debe obtener el tracer con `provider = trace.get_tracer_provider()` en cada `span(...)`, y `setup_tracing` debe guardar el provider en una variable de módulo `_PROVIDER` que `span` usa si existe (`_PROVIDER.get_tracer(...)`), llamando a `set_tracer_provider` solo la primera vez. Ajusta el código para que las pruebas del Step 1 pasen en cualquier orden.

`agent_telemetry/logging.py`:
```python
"""Logs JSON correlacionados con la traza (ADR 0003 #4)."""

import json
import logging

from opentelemetry import trace

from agent_telemetry.context import current


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()
        body = {"level": record.levelname, "logger": record.name, "message": record.getMessage(), **current()}
        if ctx.is_valid:
            body["trace_id"] = format(ctx.trace_id, "032x")
        return json.dumps(body, ensure_ascii=False)
```

`agent_telemetry/__init__.py`:
```python
"""Paquete común de telemetría (ADR 0003 #4): spans OTel con atributos `agentcore.*`."""

from agent_telemetry.context import bind
from agent_telemetry.logging import JsonLogFormatter
from agent_telemetry.setup import setup_tracing
from agent_telemetry.spans import (
    CHAT, DECIDE, EXECUTE_TOOL, INVOKE_AGENT, RULE, SEMCONV_VERSION,
    MissingTelemetryContext, configure, current_trace_id, set_content, span,
)

__all__ = [
    "CHAT", "DECIDE", "EXECUTE_TOOL", "INVOKE_AGENT", "RULE", "SEMCONV_VERSION", "JsonLogFormatter",
    "MissingTelemetryContext", "bind", "configure", "current_trace_id", "set_content", "setup_tracing", "span",
]
```

- [ ] **Step 4: Ver que pasa**

Run: `uv run pytest tests/m11/test_telemetry.py -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS (los seis tests, en cualquier orden: `uv run pytest tests/m11/test_telemetry.py -p no:randomly` y también invertido con `-k`).

- [ ] **Step 5: Commit**

```bash
git add agent_telemetry tests/m11/test_telemetry.py
git commit -m "feat(m11): agent_telemetry con spans agentcore.*, trace_id y logs JSON" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Exportación de evaluación, métricas propias y T-M11-09 (eventos)

**Files:**
- Create: `agent_core/audit/export.py`, `tests/m11/test_export.py`

**Interfaces:**
- Consumes: `AuditSink`, `check_chain` (Task 2), `to_jsonable`.
- Produces:
  - `def export_events(sink: AuditSink, run_ids: list[str], release: str | None = None) -> Iterator[dict[str, JsonValue]]` (cada fila: el evento serializado con `seq`, `hash`, `release`, `run_id`, `type`, `payload`, **incluidos los campos de medición**)
  - `class ChainIntegrity(Model): runs: int; intact: int; broken: list[str]; ratio: Decimal`
  - `def chain_integrity(sink: AuditSink, run_ids: list[str]) -> ChainIntegrity`

- [ ] **Step 1: Pruebas que fallan**

`tests/m11/test_export.py`:
```python
"""Fuente de datos de la unidad 6 (§8) y T-M11-09 (mitad eventos): todo evento lleva run_id y release."""

from decimal import Decimal

from agent_core.audit.export import chain_integrity, export_events
from agent_core.audit.log import AuditLog
from agent_core.domain import EVENT_TYPES
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.helpers import EVENT_TYPES as SAMPLED, event, events_of


def fill(store: InMemoryStore) -> AuditLog:
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 6))
        log.append(uow, "run-0002", [event("run_closed", run_id="run-0002", release="rel-otra")])
        uow.commit()
    return log


def test_t_m11_09_every_chained_event_carries_run_id_and_release() -> None:
    store = InMemoryStore()
    fill(store)
    for rows in (store.events["run-0001"], store.events["run-0002"]):
        for e in rows:
            assert e.run_id and e.release and e.seq is not None and e.hash


def test_sample_payloads_cover_every_event_type() -> None:
    assert set(SAMPLED) == set(EVENT_TYPES)  # si M0 agrega un evento, agrega su muestra


def test_export_rows_keep_measured_fields_and_filter_by_release() -> None:
    store = InMemoryStore()
    fill(store)
    sink = InMemoryAuditSink(store)
    rows = list(export_events(sink, ["run-0001", "run-0002"], release="rel-2026-09-28"))
    assert {r["run_id"] for r in rows} == {"run-0001"}
    tool = next(r for r in rows if r["type"] == "tool_called")
    assert "latency_ms" in tool["payload"]  # type: ignore[operator]
    assert all("hash" in r and "seq" in r for r in rows)


def test_chain_integrity_counts_intact_and_broken_runs() -> None:
    store = InMemoryStore()
    fill(store)
    store.events["run-0001"][2] = store.events["run-0001"][2].model_copy(update={"release": "x"})
    result = chain_integrity(InMemoryAuditSink(store), ["run-0001", "run-0002"])
    assert (result.runs, result.intact, result.broken) == (2, 1, ["run-0001"])
    assert result.ratio == Decimal("0.5")


def test_chain_integrity_of_no_runs_is_full() -> None:
    assert chain_integrity(InMemoryAuditSink(InMemoryStore()), []).ratio == Decimal(1)
```

- [ ] **Step 2: Ver que falla** — Run: `uv run pytest tests/m11/test_export.py -v` — Expected: FAIL (`ModuleNotFoundError: agent_core.audit.export`).

- [ ] **Step 3: Implementar**

`agent_core/audit/export.py`:
```python
"""Fuente de datos de la unidad 6 (M11 §8) y métricas propias de integridad."""

from collections.abc import Iterator
from decimal import Decimal

from agent_core.audit.chain import check_chain
from agent_core.domain import JsonValue, to_jsonable
from agent_core.domain.base import Model
from agent_core.ports import AuditSink


def export_events(sink: AuditSink, run_ids: list[str], release: str | None = None) -> Iterator[dict[str, JsonValue]]:
    """Un dict por evento (vista `audit`), con los campos de medición. Filtra por `release` si se indica."""
    for run_id in run_ids:
        for event in sink.read(run_id):
            if release is None or event.release == release:
                row: dict[str, JsonValue] = to_jsonable(event)
                yield row


class ChainIntegrity(Model):
    runs: int
    intact: int
    broken: list[str]
    ratio: Decimal  # objetivo 1 (100 %)


def chain_integrity(sink: AuditSink, run_ids: list[str]) -> ChainIntegrity:
    broken = [r for r in run_ids if not check_chain(r, sink.read(r)).ok]
    total = len(run_ids)
    ratio = Decimal(1) if total == 0 else Decimal(total - len(broken)) / Decimal(total)
    return ChainIntegrity(runs=total, intact=total - len(broken), broken=broken, ratio=ratio)
```

- [ ] **Step 4: Ver que pasa** — Run: `uv run pytest tests/m11 -v && uv run mypy` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/export.py tests/m11/test_export.py
git commit -m "feat(m11): exportación de eventos y métrica de integridad (T-M11-09)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Adaptador Postgres del log (append-only impuesto por la base)

**Files:**
- Create: `agent_core/adapters/sql/audit_events.sql`, `agent_core/adapters/postgres_audit.py`, `tests/integration/conftest.py`, `tests/integration/test_audit_postgres.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `event_to_json`/`event_from_json` **no** (el adaptador no importa `audit`): duplica esas 2 líneas con `dumps`/`loads` + `TypeAdapter(AnyEvent)` de `agent_core.domain`. `EngineEvent`, `AnyEvent`, `dumps`, `loads`.
- Produces:
  - `def apply_audit_schema(conn: psycopg.Connection[Any], app_role: str | None = None) -> None` (crea tabla, índices, triggers; `GRANT SELECT, INSERT` al rol si se indica)
  - `class PgAuditEvents: __init__(self, conn)`; `append_events(self, run_id: str, events: list[EngineEvent]) -> None`; `last_event(self, run_id: str) -> EngineEvent | None`; `read(self, run_id: str) -> list[EngineEvent]`. M4 delega en esto desde su `UnitOfWork` de Postgres; `PgAuditEvents` cumple `AuditSink.read` (y `append_outside_turn` **no** se implementa: usar `AuditLog.append_standalone`).

- [ ] **Step 1: Levantar Postgres**

Run: `docker compose up -d postgres && docker compose ps`
Expected: `postgres` en estado `healthy`.

- [ ] **Step 2: Pruebas de integración que fallan**

`tests/integration/conftest.py`:
```python
"""Postgres de desarrollo (docker compose). Sin él, las pruebas se omiten salvo AGENTCORE_REQUIRE_POSTGRES=1."""

import os
from collections.abc import Iterator

import psycopg
import pytest

# Credencial de desarrollo local de docker-compose.yml (no es secreta). El rol de aplicación es de prueba.
ADMIN_DSN = os.environ.get("AGENTCORE_TEST_DATABASE_URL",
                           "postgresql://agentcore:agentcore-dev-only@127.0.0.1:5432/agentcore")
APP_ROLE, APP_PASSWORD = "agentcore_audit_app", "app-dev-only"
SCHEMA = "m11_test"


@pytest.fixture
def admin_conn() -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("AGENTCORE_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("Postgres no disponible (docker compose up -d postgres)")
    with conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        conn.execute(f"SET search_path TO {SCHEMA}")
        conn.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'; END IF; END $$")
        conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE}")
        yield conn
        conn.execute("RESET search_path")
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture
def app_conn(admin_conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """Conexión con el rol de la aplicación: solo SELECT e INSERT sobre audit_events."""
    from agent_core.adapters.postgres_audit import apply_audit_schema

    apply_audit_schema(admin_conn, app_role=APP_ROLE)
    dsn = ADMIN_DSN.replace("agentcore:agentcore-dev-only", f"{APP_ROLE}:{APP_PASSWORD}")
    with psycopg.connect(dsn, autocommit=False, options=f"-c search_path={SCHEMA}") as conn:
        yield conn
```

`tests/integration/test_audit_postgres.py`:
```python
"""M11 con Postgres: cadena persistida, append-only por permiso y trigger, unicidad de (run_id, seq)."""

import psycopg
import pytest

from agent_core.adapters.postgres_audit import PgAuditEvents
from agent_core.audit.chain import chain_events, check_chain
from tests.m11.helpers import events_of

pytestmark = pytest.mark.integration


def persisted(conn: psycopg.Connection, run_id: str = "run-0001", count: int = 5) -> PgAuditEvents:
    store = PgAuditEvents(conn)
    store.append_events(run_id, chain_events(run_id, events_of(run_id, count), store.last_event(run_id)))
    conn.commit()
    return store


def test_chain_survives_postgres_roundtrip_for_all_event_types(app_conn: psycopg.Connection) -> None:
    store = persisted(app_conn, count=20)
    events = store.read("run-0001")
    assert [e.seq for e in events] == list(range(20))
    assert check_chain("run-0001", events).ok  # el JSON ida y vuelta conserva el hash


def test_last_event_sees_uncommitted_rows_of_the_same_transaction(app_conn: psycopg.Connection) -> None:
    store = PgAuditEvents(app_conn)
    store.append_events("run-0001", chain_events("run-0001", events_of("run-0001", 2), None))
    last = store.last_event("run-0001")
    assert last is not None and last.seq == 1
    app_conn.rollback()
    assert store.last_event("run-0001") is None


def test_app_role_cannot_update_delete_or_truncate(app_conn: psycopg.Connection) -> None:
    persisted(app_conn)
    for sql in ("UPDATE audit_events SET release = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(sql)
        app_conn.rollback()


def test_even_the_table_owner_is_blocked_by_trigger(app_conn: psycopg.Connection,
                                                    admin_conn: psycopg.Connection) -> None:
    persisted(app_conn)
    for sql in ("UPDATE audit_events SET release = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            admin_conn.execute(sql)


def test_seq_is_unique_per_run(app_conn: psycopg.Connection) -> None:
    persisted(app_conn, count=2)
    store = PgAuditEvents(app_conn)
    dup = chain_events("run-0001", events_of("run-0001", 1), None)  # seq 0 otra vez
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.append_events("run-0001", dup)
    app_conn.rollback()


def test_tampering_through_a_privileged_bypass_is_detected_by_verify(admin_conn: psycopg.Connection,
                                                                    app_conn: psycopg.Connection) -> None:
    store = persisted(app_conn)
    admin_conn.execute("ALTER TABLE audit_events DISABLE TRIGGER USER")
    admin_conn.execute("UPDATE audit_events SET event_json = replace(event_json, 'rel-2026-09-28', 'rel-x') "
                       "WHERE seq = 2")
    assert check_chain("run-0001", store.read("run-0001")).broken_at == 2
```

- [ ] **Step 3: Ver que falla**

Run: `uv run pytest tests/integration -m integration -v`
Expected: FAIL (`ModuleNotFoundError: agent_core.adapters.postgres_audit`).

- [ ] **Step 4: Migración y adaptador**

`agent_core/adapters/sql/audit_events.sql`:
```sql
-- Log de auditoría (M11, ADR 0003 #2): append-only impuesto por la base, no solo por el código.
CREATE TABLE IF NOT EXISTS audit_events (
    run_id     text        NOT NULL,
    seq        integer     NOT NULL CHECK (seq >= 0),
    event_id   text        NOT NULL UNIQUE,
    type       text        NOT NULL,
    release    text        NOT NULL,
    ts         timestamptz NOT NULL,
    prev_hash  char(64)    NOT NULL,
    hash       char(64)    NOT NULL,
    event_json text        NOT NULL,   -- JSON de M0 (`dumps`): conserva Decimal y permite recalcular el hash
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS audit_events_release_type_idx ON audit_events (release, type);

CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events es append-only' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable();

REVOKE ALL ON audit_events FROM PUBLIC;
```

`agent_core/adapters/postgres_audit.py`:
```python
"""Almacén Postgres de la cadena de auditoría (M11). No encadena: recibe eventos ya encadenados.

M4 lo usa desde su `UnitOfWork` de Postgres, dentro de la misma transacción que `save_run`."""

from importlib import resources
from typing import Any

import psycopg
from psycopg import sql
from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent, dumps, loads

_EVENTS: TypeAdapter[Any] = TypeAdapter(AnyEvent)


def apply_audit_schema(conn: "psycopg.Connection[Any]", app_role: str | None = None) -> None:
    """Crea tabla, índices y triggers (idempotente). `app_role` recibe solo SELECT e INSERT."""
    conn.execute(resources.files("agent_core.adapters").joinpath("sql/audit_events.sql").read_text("utf-8"))
    if app_role is not None:
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON audit_events TO {}").format(sql.Identifier(app_role)))


def _decode(text: str) -> EngineEvent:
    event: EngineEvent = _EVENTS.validate_python(loads(text))
    return event


class PgAuditEvents:
    def __init__(self, conn: "psycopg.Connection[Any]") -> None:
        self._conn = conn

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        rows = []
        for e in events:
            if e.run_id != run_id or e.seq is None or e.hash is None or e.prev_hash is None:
                raise ValueError("solo se persisten eventos encadenados del run indicado")
            rows.append((run_id, e.seq, e.event_id, getattr(e, "type"), e.release, e.ts,
                         e.prev_hash, e.hash, dumps(e)))
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO audit_events (run_id, seq, event_id, type, release, ts, prev_hash, hash, "
                "event_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)

    def last_event(self, run_id: str) -> EngineEvent | None:
        row = self._conn.execute(
            "SELECT event_json FROM audit_events WHERE run_id = %s ORDER BY seq DESC LIMIT 1",
            (run_id,)).fetchone()
        return _decode(row[0]) if row else None

    def read(self, run_id: str) -> list[EngineEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM audit_events WHERE run_id = %s ORDER BY seq", (run_id,)).fetchall()
        return [_decode(r[0]) for r in rows]
```
Verifica que `hatch` incluye `agent_core/adapters/sql/*.sql` en el wheel (los archivos dentro de un paquete se incluyen por defecto). Con `conn.execute(text)` de varias sentencias sin parámetros, psycopg 3 usa el protocolo simple: si falla, ejecuta el script con `cur.execute` sentencia a sentencia (los `$$` impiden partir por `;`; en ese caso deja el script en un solo `execute`).

- [ ] **Step 5: Ver que pasa**

Run: `uv run pytest tests/integration -m integration -v && uv run mypy && uv run lint-imports && uv run ruff check .`
Expected: PASS (6 tests). `lint-imports` confirma que `adapters` no importa `audit`.

- [ ] **Step 6: CI con Postgres**

En `.github/workflows/ci.yml`, bajo el job `check`, agrega el servicio y el entorno:
```yaml
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: agentcore
          POSTGRES_PASSWORD: agentcore-dev-only
          POSTGRES_DB: agentcore
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U agentcore" --health-interval 5s --health-timeout 3s --health-retries 10
    env:
      AGENTCORE_REQUIRE_POSTGRES: "1"
```
(mantén los pasos existentes; `uv run pytest` ya corre `tests/integration`.)

- [ ] **Step 7: Commit**

```bash
git add agent_core/adapters tests/integration .github/workflows/ci.yml
git commit -m "feat(m11): adaptador Postgres del log con append-only impuesto por la base" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Replay — informe y comparación (T-M11-10 a nivel unidad)

**Files:**
- Create: `agent_core/audit/replay/__init__.py`, `agent_core/audit/replay/report.py`, `agent_core/audit/replay/compare.py`, `tests/m11/test_replay_compare.py`

**Interfaces:**
- Consumes: `EngineEvent`, `MEASURED_FIELDS`, `to_jsonable`, `JsonValue` (`agent_core.domain`).
- Produces:
  - `ReplayMode = Literal["fixture", "audit"]`
  - `class Divergence(Model): event_seq: int | None; expected: JsonValue; actual: JsonValue`
  - `class ReplayReport(Model): mode: ReplayMode; run_id: str; release: str; verdict: Literal["match","diverged","chain_broken"]; first_divergence: Divergence | None = None; chain_broken_at: int | None = None; duration_ms: int | None = None`
  - `def normalize(event: EngineEvent) -> dict[str, JsonValue]` (sin `event_id`, `seq`, `prev_hash`, `hash`, `ts` ni campos de `MEASURED_FIELDS`)
  - `def first_divergence(recorded: list[EngineEvent], replayed: list[EngineEvent]) -> Divergence | None`

- [ ] **Step 1: Pruebas que fallan**

`tests/m11/test_replay_compare.py`:
```python
"""T-M11-10: los campos de medición no cuentan; cualquier otro cambio del mismo evento sí."""

from decimal import Decimal

from agent_core.audit.replay.compare import first_divergence, normalize
from agent_core.domain import MEASURED_FIELDS
from tests.m11.helpers import event


def with_payload(event_type: str, **changes: object) -> object:
    base = event(event_type)
    return base.model_copy(update={"payload": base.payload.model_copy(update=changes)})  # type: ignore[attr-defined]


def test_t_m11_10_measured_fields_are_ignored() -> None:
    cases = {
        "decision_made": {"latency_ms": 999},
        "tool_called": {"latency_ms": 999},
        "turn_completed": {"duration_ms": 12345},
    }
    for event_type, change in cases.items():
        a, b = event(event_type), with_payload(event_type, **change)
        assert first_divergence([a], [b]) is None  # type: ignore[list-item]


def test_response_emitted_llm_is_ignored_but_validator_is_not() -> None:
    a = event("response_emitted")
    llm = a.payload.llm.model_copy(update={"latency_ms": 1, "cost_usd": Decimal("9.9")})  # type: ignore[attr-defined]
    assert first_divergence([a], [with_payload("response_emitted", llm=llm)]) is None  # type: ignore[list-item]
    bad = a.payload.validator.model_copy(update={"ok": False})  # type: ignore[attr-defined]
    div = first_divergence([a], [with_payload("response_emitted", validator=bad)])  # type: ignore[list-item]
    assert div is not None


def test_t_m11_10_change_in_any_other_field_of_the_same_event_diverges() -> None:
    a = event("turn_completed")
    changed = with_payload("turn_completed", degraded=True, duration_ms=1)  # mide distinto Y cambia degraded
    div = first_divergence([a], [changed])  # type: ignore[list-item]
    assert div is not None and div.event_seq is None or div is not None


def test_envelope_fields_are_ignored() -> None:
    a = event("run_closed")
    b = a.model_copy(update={"event_id": "otro", "seq": 7, "prev_hash": "a" * 64, "hash": "b" * 64,
                             "ts": a.ts.replace(year=2030)})
    assert first_divergence([a], [b]) is None


def test_release_and_run_are_not_ignored() -> None:
    a = event("run_closed")
    assert first_divergence([a], [a.model_copy(update={"release": "otra"})]) is not None


def test_reports_first_divergence_with_recorded_seq_and_values() -> None:
    from agent_core.audit.chain import chain_events

    recorded = chain_events("run-0001", [event("run_started"), event("rule_evaluated", n=2), event("run_closed", n=3)], None)
    replayed = [recorded[0], recorded[1].model_copy(update={"payload": recorded[1].payload.model_copy(update={"result": True})}),  # type: ignore[attr-defined]
                recorded[2]]
    div = first_divergence(recorded, replayed)
    assert div is not None and div.event_seq == 1
    assert div.expected["payload"]["result"] is False and div.actual["payload"]["result"] is True  # type: ignore[index]


def test_length_mismatch_diverges_at_the_missing_or_extra_event() -> None:
    from agent_core.audit.chain import chain_events

    recorded = chain_events("run-0001", [event("run_started"), event("run_closed", n=2)], None)
    short = first_divergence(recorded, recorded[:1])
    assert short is not None and short.event_seq == 1 and short.actual is None
    extra = first_divergence(recorded[:1], recorded)
    assert extra is not None and extra.event_seq == 1 and extra.expected is None


def test_normalize_drops_only_declared_fields() -> None:
    n = normalize(event("tool_called"))
    assert "latency_ms" not in n["payload"] and "call_id" in n["payload"]  # type: ignore[operator]
    assert not ({"event_id", "seq", "prev_hash", "hash", "ts"} & set(n))
    assert set(MEASURED_FIELDS) >= {"tool_called"}
```
(La prueba `test_t_m11_10_change_in_any_other_field…` debe afirmar solo `assert div is not None`; simplifícala así al escribirla: `assert first_divergence([a], [changed]) is not None`.)

- [ ] **Step 2: Ver que falla** — Run: `uv run pytest tests/m11/test_replay_compare.py -v` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar**

`agent_core/audit/replay/__init__.py`: vacío (docstring `"""Replay (M11 §3.4)."""`).

`agent_core/audit/replay/report.py`:
```python
"""Salida del replay (M11 §2, decisión 12)."""

from typing import Literal

from agent_core.domain import JsonValue
from agent_core.domain.base import Model

ReplayMode = Literal["fixture", "audit"]


class Divergence(Model):
    event_seq: int | None  # seq grabado del evento que difiere; el siguiente seq si el replay produjo de más
    expected: JsonValue    # lo grabado (normalizado); None si faltaba
    actual: JsonValue      # lo recalculado (normalizado); None si faltó


class ReplayReport(Model):
    mode: ReplayMode
    run_id: str
    release: str
    verdict: Literal["match", "diverged", "chain_broken"]
    first_divergence: Divergence | None = None
    chain_broken_at: int | None = None
    duration_ms: int | None = None
```

`agent_core/audit/replay/compare.py`:
```python
"""Comparación evento a evento (M11 §3.4): excluye el sobre y los campos de medición (M0 §2.10)."""

from itertools import zip_longest

from agent_core.audit.replay.report import Divergence
from agent_core.domain import MEASURED_FIELDS, EngineEvent, JsonValue, to_jsonable

_IGNORED_ENVELOPE = ("event_id", "seq", "prev_hash", "hash", "ts")


def normalize(event: EngineEvent) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = to_jsonable(event)
    for key in _IGNORED_ENVELOPE:
        data.pop(key, None)
    payload = data.get("payload")
    if isinstance(payload, dict):
        for name in MEASURED_FIELDS.get(str(data.get("type")), frozenset()):
            payload.pop(name, None)
    return data


def first_divergence(recorded: list[EngineEvent], replayed: list[EngineEvent]) -> Divergence | None:
    for index, (expected, actual) in enumerate(zip_longest(recorded, replayed)):
        exp = normalize(expected) if expected is not None else None
        act = normalize(actual) if actual is not None else None
        if exp != act:
            seq = expected.seq if expected is not None and expected.seq is not None else index
            return Divergence(event_seq=seq, expected=exp, actual=act)
    return None
```

- [ ] **Step 4: Ver que pasa** — Run: `uv run pytest tests/m11/test_replay_compare.py -v && uv run mypy && uv run lint-imports` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/replay tests/m11/test_replay_compare.py
git commit -m "feat(m11): informe y comparación de replay que excluye campos de medición (T-M11-10)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Fixtures (formato YAML) y catálogo de datos sintéticos (T-M11-05)

**Files:**
- Create: `agent_core/audit/replay/fixture.py`, `agent_core/audit/replay/synthetic.py`, `tests/fixtures/catalogo-datos-prueba.yaml`, `tests/m11/test_fixture_io.py`, `tests/m11/test_synthetic.py`

**Interfaces:**
- Consumes: `AnyEvent`, `EngineEvent`, `JsonValue`, `to_jsonable` (`agent_core.domain`); `FieldClassifier` (`agent_core.views`); `ToolStatus`, `AuthLevel`.
- Produces:
  - `class FullToolResult(Model): status: ToolStatus; result_full: JsonValue = None; source: str | None = None; error: str | None = None; required_level: AuthLevel | None = None`
  - `class Fixture(Model): name: str; run_id: str; release: str; inputs: list[dict[str, JsonValue]]; events: list[AnyEvent]; full: dict[str, FullToolResult]; drafts: list[JsonValue]` (`full` por `call_id`; `drafts` = salidas del LLM en orden)
  - `def dump_fixture(fixture: Fixture) -> str`; `def load_fixture(text: str) -> Fixture`; `def load_fixture_file(path: Path) -> Fixture`
  - `class SyntheticCatalog(Model): email_domains: list[str]; numbers: list[str]; values: list[str]`; `def load_catalog(path: Path) -> SyntheticCatalog`
  - `class FixtureRejected(ValueError)` (con `.violations: list[str]`, rutas sin valores); `def check_fixture(fixture: Fixture, catalog: SyntheticCatalog, classifier: FieldClassifier | None = None) -> None`

- [ ] **Step 1: Catálogo sintético**

`tests/fixtures/catalogo-datos-prueba.yaml`:
```yaml
# Catálogo de datos de prueba (M11 decisión 13). Todo inventado; nunca datos del dataset real.
email_domains: [example.test]
numbers: ["1023456789", "3001234567", "4111000011112222"]
values: ["Ana Prueba", "Luis Muestra", "cust-001", "tx-demo-1", "tx-demo-2", "1023456789", "3001234567"]
```

- [ ] **Step 2: Pruebas que fallan**

`tests/m11/test_fixture_io.py`:
```python
"""Formato del fixture (decisión 14): ida y vuelta con Decimal, cadena y eventos intactos."""

from decimal import Decimal

from agent_core.audit.chain import chain_events, check_chain
from agent_core.audit.replay.fixture import FullToolResult, Fixture, dump_fixture, load_fixture
from tests.m11.helpers import event


def sample() -> Fixture:
    events = chain_events("run-0001", [event("run_started"), event("rule_evaluated", n=2),
                                       event("tool_called", n=3), event("run_closed", n=4)], None)
    return Fixture(
        name="demo", run_id="run-0001", release="rel-2026-09-28",
        inputs=[{"text_model": "hola", "client_turn_id": "c-1"}], events=events,
        full={"call-0001": FullToolResult(status="ok", result_full={"monto": Decimal("120.50"), "n": 500,
                                                                     "entero": Decimal("500")}, source="tx")},
        drafts=["Listo ⟦name:1⟧"],
    )


def test_roundtrip_preserves_events_chain_and_decimals() -> None:
    back = load_fixture(dump_fixture(sample()))
    assert back.events == sample().events and check_chain("run-0001", back.events).ok
    result = back.full["call-0001"].result_full
    assert isinstance(result, dict) and result["monto"] == Decimal("120.50")
    assert str(result["monto"]) == "120.50" and result["entero"] == 500


def test_dump_is_stable_text() -> None:
    assert dump_fixture(sample()) == dump_fixture(load_fixture(dump_fixture(sample())))


def test_loader_rejects_duplicate_keys_and_extra_fields() -> None:
    import pytest

    with pytest.raises(ValueError):
        load_fixture("name: a\nname: b\n")
    with pytest.raises(ValueError):
        load_fixture(dump_fixture(sample()) + "extra: 1\n")
```

`tests/m11/test_synthetic.py`:
```python
"""T-M11-05: un fixture con un valor no sintético se rechaza."""

from pathlib import Path

import pytest

from agent_core.audit.replay.fixture import FullToolResult
from agent_core.audit.replay.synthetic import FixtureRejected, check_fixture, load_catalog
from tests.m11.test_fixture_io import sample

CATALOG = load_catalog(Path("tests/fixtures/catalogo-datos-prueba.yaml"))


def with_full(**result: object) -> object:
    fx = sample()
    return fx.model_copy(update={"full": {"call-0001": FullToolResult(status="ok", result_full=dict(result),
                                                                     source="cliente")}})


def test_synthetic_fixture_is_accepted() -> None:
    check_fixture(with_full(document_number="1023456789", email="ana@example.test"), CATALOG)  # type: ignore[arg-type]
    check_fixture(sample(), CATALOG)


@pytest.mark.parametrize("result", [
    {"email": "cliente@gmail.com"},             # dominio real
    {"telefono_libre": "3157654321"},           # número de 6+ dígitos fuera del catálogo
    {"first_name": "Carlos Real"},              # campo pii_direct fuera de `values`
])
def test_t_m11_05_non_synthetic_value_is_rejected(result: dict[str, object]) -> None:
    with pytest.raises(FixtureRejected) as info:
        check_fixture(with_full(**result), CATALOG)  # type: ignore[arg-type]
    text = str(info.value) + repr(info.value.violations)
    for leaked in ("gmail", "3157654321", "Carlos"):
        assert leaked not in text  # reporta rutas, nunca valores


def test_non_synthetic_text_in_inputs_and_drafts_is_rejected() -> None:
    fx = sample().model_copy(update={"inputs": [{"text_model": "escríbeme a real@banco.com"}]})
    with pytest.raises(FixtureRejected):
        check_fixture(fx, CATALOG)
    fx = sample().model_copy(update={"drafts": ["mi cédula es 80123456"]})
    with pytest.raises(FixtureRejected):
        check_fixture(fx, CATALOG)
```
En `with_full`, el catálogo por defecto de `FieldClassifier` clasifica `first_name` como `pii_direct` (tag `name`) y `email` como `pii_direct`; `document_number` también.

- [ ] **Step 3: Ver que falla** — Run: `uv run pytest tests/m11/test_fixture_io.py tests/m11/test_synthetic.py -v` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Implementar el formato**

`agent_core/audit/replay/fixture.py`:
```python
"""Fixture de replay (M11 §3.5, decisión 14): eventos + entradas en vista `full`, solo datos sintéticos."""

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from agent_core.domain import AnyEvent, AuthLevel, JsonValue, ToolStatus, to_jsonable
from agent_core.domain.base import Model

_MAX_BYTES = 4 * 1024 * 1024


class FullToolResult(Model):
    status: ToolStatus
    result_full: JsonValue = None
    source: str | None = None
    error: str | None = None
    required_level: AuthLevel | None = None


class Fixture(Model):
    name: str
    run_id: str
    release: str
    inputs: list[dict[str, JsonValue]]
    events: list[AnyEvent]
    full: dict[str, FullToolResult]
    drafts: list[JsonValue]


class _Loader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ValueError(f"clave duplicada: {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


def _float_as_decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Decimal:
    return Decimal(str(loader.construct_scalar(node)))  # type: ignore[arg-type]


_Loader.add_constructor("tag:yaml.org,2002:float", _float_as_decimal)


class _Dumper(yaml.SafeDumper):
    pass


def _represent_decimal(dumper: yaml.SafeDumper, value: Decimal) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:float", format(value, "f"))


_Dumper.add_representer(Decimal, _represent_decimal)


def dump_fixture(fixture: Fixture) -> str:
    return yaml.dump(to_jsonable(fixture), Dumper=_Dumper, sort_keys=False, allow_unicode=True)


def load_fixture(text: str) -> Fixture:
    if len(text.encode()) > _MAX_BYTES:
        raise ValueError("fixture demasiado grande")
    data = yaml.load(text, Loader=_Loader)  # noqa: S506 — Loader derivado de SafeLoader
    if not isinstance(data, dict):
        raise ValueError("el fixture debe ser un mapa YAML")
    return Fixture.model_validate(data)


def load_fixture_file(path: Path) -> Fixture:
    return load_fixture(path.read_text(encoding="utf-8"))
```
Nota: si `yaml.dump` emite un `Decimal` entero como `!!float 500` y la ida y vuelta lo devuelve `Decimal("500")`, la comparación con `int` es igual (`500 == Decimal(500)`); el test lo cubre. Elimina `import re` si queda sin uso.

- [ ] **Step 5: Implementar el catálogo**

`agent_core/audit/replay/synthetic.py`:
```python
"""Rechazo de fixtures con datos no sintéticos (M11 §3.5, decisión 13). Reporta rutas, nunca valores."""

import re
from collections.abc import Iterator
from pathlib import Path

import yaml

from agent_core.audit.replay.fixture import Fixture
from agent_core.domain import JsonValue, to_jsonable
from agent_core.domain.base import Model
from agent_core.views import FieldClassifier

_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_DIGITS = re.compile(r"\d{6,}")
_STRICT = ("pii_direct", "pii_quasi")


class SyntheticCatalog(Model):
    email_domains: list[str]
    numbers: list[str]
    values: list[str]


class FixtureRejected(ValueError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__(f"fixture con datos no sintéticos en: {', '.join(violations)}")
        self.violations = violations


def load_catalog(path: Path) -> SyntheticCatalog:
    return SyntheticCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _leaves(value: JsonValue, path: str) -> Iterator[tuple[str, JsonValue]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def check_fixture(fixture: Fixture, catalog: SyntheticCatalog, classifier: FieldClassifier | None = None) -> None:
    """Escanea `inputs`, `full` y `drafts` (los `events` van en vista `audit` y con hashes hex)."""
    classifier = classifier or FieldClassifier()
    scanned: dict[str, JsonValue] = {
        "inputs": to_jsonable(fixture.inputs),
        "full": {k: to_jsonable(v.result_full) for k, v in fixture.full.items()},
        "drafts": to_jsonable(fixture.drafts),
    }
    bad: list[str] = []
    for path, leaf in _leaves(scanned, "fixture"):
        if leaf is None or isinstance(leaf, bool):
            continue
        text = str(leaf)
        last = path.rsplit(".", 1)[-1].split("[")[0]
        strict = classifier.classify(last) in _STRICT
        if strict and text not in catalog.values:
            bad.append(path)
        elif any(m.group(1) not in catalog.email_domains for m in _EMAIL.finditer(text)):
            bad.append(path)
        elif any(m.group(0) not in catalog.numbers for m in _DIGITS.finditer(text)):
            bad.append(path)
    if bad:
        raise FixtureRejected(sorted(set(bad)))
```
`FieldClassifier.classify(path)` devuelve `pii_direct` para un campo sin clasificar (regla de M7): **excluye del escaneo estricto los nombres de campo que el catálogo de M7 no conoce** usando `classifier.lookup(last) is not None`; de lo contrario todo campo desconocido (p. ej. `text_model`) se rechazaría. Ajusta la línea `strict` a `rule = classifier.lookup(last); strict = rule is not None and rule.field_class in _STRICT`.

- [ ] **Step 6: Ver que pasa** — Run: `uv run pytest tests/m11/test_fixture_io.py tests/m11/test_synthetic.py -v && uv run mypy && uv run lint-imports` — Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agent_core/audit/replay/fixture.py agent_core/audit/replay/synthetic.py tests/fixtures/catalogo-datos-prueba.yaml tests/m11/test_fixture_io.py tests/m11/test_synthetic.py
git commit -m "feat(m11): formato de fixture YAML y rechazo de datos no sintéticos (T-M11-05)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Puertos grabados y envoltorios de grabación

**Files:**
- Create: `agent_core/audit/replay/ports.py`, `agent_core/audit/replay/recording.py`, `tests/m11/test_recorded_ports.py`, `tests/m11/test_recording.py`

**Interfaces:**
- Consumes: `Clock`, `IdKind`, `IdSource`, `ToolExecutor`, `ToolCallContext`, `ToolResult`, `LLMGateway`, `GenerationResult` (`agent_core.ports`); `ToolCalledPayload`, `EntityRef`, `ToolDef`, `EngineEvent`, `JsonValue` (`agent_core.domain`); `Fixture`, `FullToolResult` (Task 10); `ReplayMode` (Task 9).
- Produces (`ports.py`):
  - `class ReplayDesync(Exception)`: `__init__(self, event_seq: int | None, expected: JsonValue, actual: JsonValue)`
  - `class FullViewAccessError(RuntimeError)`
  - `class FullSource(Protocol): def tool_result(self, call_id: str) -> FullToolResult`
  - `FixtureFullSource(results: Mapping[str, FullToolResult])`, `ForbiddenFullSource()` (lanza `FullViewAccessError`)
  - `RecordedClock(instants: Mapping[str | None, UtcDatetime])` con `enter_turn(turn_id: str | None)`, `set(instant)`, `now()`, `monotonic_ns() -> 0`; `RecordedClock.from_events(events)`
  - `RecordedIds(by_kind: Mapping[IdKind, list[str]])` con `new_id`, `secret_token`; `RecordedIds.from_events(events)`
  - `RecordedToolExecutor(events, mode, full, definitions: Callable[[EntityRef], ToolDef])` con `execute(...)`, `definition(...)`
  - `RecordedGateway(outputs: list[JsonValue])` con `generate(...)`
  - `RecordedReadings(events)`: `events_of(event_type: str, turn_id: str | None = None) -> list[EngineEvent]`
  - `@dataclass(frozen=True) class RecordedPorts: mode; clock: RecordedClock; ids: RecordedIds; tools: RecordedToolExecutor; llm: RecordedGateway; readings: RecordedReadings; full: FullSource`
  - `def build_ports(events, mode, *, full: FullSource, drafts: list[JsonValue], definitions) -> RecordedPorts`
- Produces (`recording.py`): `RecordingToolExecutor(inner: ToolExecutor)` (captura `.captured: dict[str, FullToolResult]`), `RecordingGateway(inner: LLMGateway)` (captura `.drafts: list[JsonValue]`), `build_fixture(name, run_id, release, events, inputs, tools: RecordingToolExecutor, llm: RecordingGateway) -> Fixture`.

- [ ] **Step 1: Pruebas que fallan**

`tests/m11/test_recorded_ports.py`:
```python
"""Puertos grabados: nunca llaman nada real; una llamada no grabada es una divergencia (ReplayDesync)."""

import pytest

from agent_core.audit.chain import chain_events
from agent_core.audit.replay.fixture import FullToolResult
from agent_core.audit.replay.ports import (
    FixtureFullSource,
    ForbiddenFullSource,
    FullViewAccessError,
    RecordedClock,
    RecordedGateway,
    RecordedIds,
    RecordedToolExecutor,
    ReplayDesync,
    build_ports,
)
from agent_core.ports import IdKind, ToolCallContext
from testing.builders import principal
from tests.m11.helpers import event

CTX = ToolCallContext(run_id="run-0001", release="rel-2026-09-28", principal=principal())


def run_events():  # type: ignore[no-untyped-def]
    return chain_events("run-0001", [
        event("run_started", turn_id=None, n=1),
        event("turn_started", n=2),
        event("tool_called", n=3),      # call-0001, buscar_transacciones@1.0.0, ok
        event("decision_made", n=4),    # decision-0001
        event("response_emitted", n=5),
        event("turn_completed", n=6),
    ], None)


def test_clock_is_constant_within_a_turn_and_monotonic_is_fixed() -> None:
    clock = RecordedClock.from_events(run_events())
    clock.enter_turn(None)
    started = clock.now()
    clock.enter_turn("turn-0001")
    assert clock.now() == clock.now() and clock.monotonic_ns() == clock.monotonic_ns() == 0
    assert started == clock.now()  # ts idéntico en el sobre sintético


def test_ids_replay_recorded_ids_in_order_then_fall_back_deterministically() -> None:
    ids = RecordedIds.from_events(run_events())
    assert ids.new_id(IdKind.call) == "call-0001" and ids.new_id(IdKind.decision) == "decision-0001"
    assert ids.new_id(IdKind.call) == "call-replay-0001"
    assert ids.secret_token() != ids.secret_token() and len(ids.secret_token()) >= 22


def test_tools_fixture_mode_serves_full_and_audit_mode_serves_audit_view() -> None:
    events = run_events()
    tool = event("tool_called").payload.tool  # type: ignore[attr-defined]
    full = FixtureFullSource({"call-0001": FullToolResult(status="ok", result_full={"count": 2, "sec": "x"},
                                                          source="tx")})
    fx = RecordedToolExecutor(events, "fixture", full, lambda ref: (_ for _ in ()).throw(KeyError()))
    got = fx.execute(tool, {}, {}, CTX)
    assert got.call_id == "call-0001" and got.result_full == {"count": 2, "sec": "x"} and got.source == "tx"
    au = RecordedToolExecutor(events, "audit", ForbiddenFullSource(), lambda ref: (_ for _ in ()).throw(KeyError()))
    assert au.execute(tool, {}, {}, CTX).result_full == {"count": 2}  # vista audit del evento; no toca full


def test_unrecorded_tool_call_is_a_desync_with_the_expected_event() -> None:
    events = run_events()
    tools = RecordedToolExecutor(events, "audit", ForbiddenFullSource(), lambda r: None)  # type: ignore[arg-type, return-value]
    from agent_core.domain import EntityRef

    with pytest.raises(ReplayDesync) as info:
        tools.execute(EntityRef.parse("otra_tool@1.0.0"), {}, {}, CTX)
    assert info.value.event_seq == 2
    tools.execute(event("tool_called").payload.tool, {}, {}, CTX)  # type: ignore[attr-defined]
    with pytest.raises(ReplayDesync):  # ya no quedan llamadas grabadas
        tools.execute(event("tool_called").payload.tool, {}, {}, CTX)  # type: ignore[attr-defined]


def test_forbidden_full_source_raises() -> None:
    with pytest.raises(FullViewAccessError):
        ForbiddenFullSource().tool_result("call-0001")


def test_gateway_serves_recorded_drafts_then_desyncs() -> None:
    gw = RecordedGateway(["uno"])
    from agent_core.domain import EntityRef

    assert gw.generate(EntityRef.parse("p@1.0.0"), {}, "es").output == "uno"
    with pytest.raises(ReplayDesync):
        gw.generate(EntityRef.parse("p@1.0.0"), {}, "es")


def test_build_ports_wires_everything_for_the_mode() -> None:
    ports = build_ports(run_events(), "audit", full=ForbiddenFullSource(), drafts=[], definitions=lambda r: None)  # type: ignore[arg-type, return-value]
    assert ports.mode == "audit" and len(ports.readings.events_of("tool_called")) == 1
    assert ports.readings.events_of("turn_started", "turn-0001")
```
(`EntityRef.parse` — usa el constructor real de `EntityRef` de M0; consulta `agent_core/domain/refs.py` y ajusta la construcción si es otra.)

`tests/m11/test_recording.py`:
```python
"""Grabación para `agentcore record`: captura resultados `full` y borradores sin alterar el puerto envuelto."""

from decimal import Decimal

from agent_core.audit.chain import chain_events
from agent_core.audit.replay.recording import RecordingGateway, RecordingToolExecutor, build_fixture
from agent_core.domain import EntityRef
from agent_core.ports import GenerationResult, ToolCallContext, ToolResult
from testing.builders import principal
from tests.m11.helpers import event


class InnerTools:
    def execute(self, tool, args, bound_params, ctx, idempotency_key=None):  # type: ignore[no-untyped-def]
        return ToolResult(status="ok", result_full={"monto": Decimal("10.50")}, source="tx", call_id="call-0001")

    def definition(self, tool):  # type: ignore[no-untyped-def]
        raise NotImplementedError


class InnerGateway:
    def generate(self, prompt, inputs_model_view, locale, schema=None):  # type: ignore[no-untyped-def]
        return GenerationResult(output="hola", tokens_in=1, tokens_out=1, cost_usd=Decimal("0"), model="m")


def test_recording_wrappers_capture_and_pass_through() -> None:
    tools, llm = RecordingToolExecutor(InnerTools()), RecordingGateway(InnerGateway())  # type: ignore[arg-type]
    ctx = ToolCallContext(run_id="run-0001", release="r", principal=principal())
    assert tools.execute(EntityRef.parse("t@1.0.0"), {}, {}, ctx).call_id == "call-0001"
    assert llm.generate(EntityRef.parse("p@1.0.0"), {}, "es").output == "hola"
    events = chain_events("run-0001", [event("run_started"), event("run_closed", n=2)], None)
    fx = build_fixture("demo", "run-0001", "rel-2026-09-28", events, [{"text_model": "hola"}], tools, llm)
    assert fx.full["call-0001"].result_full == {"monto": Decimal("10.50")} and fx.drafts == ["hola"]
    assert fx.events == events
```

- [ ] **Step 2: Ver que falla** — Run: `uv run pytest tests/m11/test_recorded_ports.py tests/m11/test_recording.py -v` — Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar `ports.py`**

```python
"""Puertos "grabados" del replay (M11 §3.4): responden desde los registros y nunca llaman nada real."""

import base64
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from agent_core.audit.replay.fixture import FullToolResult
from agent_core.audit.replay.report import ReplayMode
from agent_core.domain import (
    EngineEvent, EntityRef, JsonValue, Locale, ToolCalledPayload, ToolDef, UtcDatetime, to_jsonable,
)
from agent_core.ports import GenerationResult, IdKind, ToolCallContext, ToolResult


class ReplayDesync(Exception):
    """El motor pidió algo que el registro no tiene (o no en ese orden): es una divergencia."""

    def __init__(self, event_seq: int | None, expected: JsonValue, actual: JsonValue) -> None:
        super().__init__("el motor se desvió de lo grabado")
        self.event_seq, self.expected, self.actual = event_seq, expected, actual


class FullViewAccessError(RuntimeError):
    """El replay `audit` intentó leer la vista `full` (invariante de M11 §4)."""


class FullSource(Protocol):
    def tool_result(self, call_id: str) -> FullToolResult: ...


class FixtureFullSource:
    def __init__(self, results: Mapping[str, FullToolResult]) -> None:
        self._results = dict(results)

    def tool_result(self, call_id: str) -> FullToolResult:
        try:
            return self._results[call_id]
        except KeyError:
            raise ReplayDesync(None, f"fixture.full[{call_id}]", None) from None


class ForbiddenFullSource:
    def tool_result(self, call_id: str) -> FullToolResult:
        raise FullViewAccessError("el replay `audit` no accede a la vista full")


class RecordedClock:
    """`now()` constante dentro de un turno (decisión 10). `monotonic_ns()` constante: solo alimenta medición."""

    def __init__(self, instants: Mapping[str | None, UtcDatetime]) -> None:
        self._instants = dict(instants)
        self._turn: str | None = None
        self._override: UtcDatetime | None = None

    @classmethod
    def from_events(cls, events: list[EngineEvent]) -> "RecordedClock":
        instants: dict[str | None, UtcDatetime] = {}
        for e in events:
            kind = getattr(e, "type", None)
            if kind == "run_started":
                instants.setdefault(None, e.ts)
            elif kind == "turn_started" and e.turn_id is not None:
                instants.setdefault(e.turn_id, e.ts)
        return cls(instants)

    def enter_turn(self, turn_id: str | None) -> None:
        self._turn, self._override = turn_id, None

    def set(self, instant: UtcDatetime) -> None:
        """Instante de un `expiry_evaluated` o de un barrido."""
        self._override = instant

    def now(self) -> UtcDatetime:
        if self._override is not None:
            return self._override
        try:
            return self._instants[self._turn]
        except KeyError:
            raise ReplayDesync(None, f"instante grabado del turno {self._turn}", None) from None

    def monotonic_ns(self) -> int:
        return 0


class RecordedIds:
    """Reparte los IDs grabados, por `IdKind`, en el orden de la cadena (decisión 9)."""

    def __init__(self, by_kind: Mapping[IdKind, list[str]]) -> None:
        self._queues: dict[IdKind, deque[str]] = {k: deque(v) for k, v in by_kind.items()}
        self._fallback: defaultdict[IdKind, int] = defaultdict(int)
        self._tokens = 0

    @classmethod
    def from_events(cls, events: list[EngineEvent]) -> "RecordedIds":
        out: dict[IdKind, list[str]] = defaultdict(list)

        def add(kind: IdKind, value: str | None) -> None:
            if value is not None and value not in out[kind]:
                out[kind].append(value)

        for e in events:
            add(IdKind.event, e.event_id)
            add(IdKind.run, e.run_id)
            add(IdKind.session, e.session_id)
            add(IdKind.turn, e.turn_id)
            payload = getattr(e, "payload", None)
            for name, kind in (("call_id", IdKind.call), ("readback_call_id", IdKind.call),
                               ("decision_id", IdKind.decision), ("action_id", IdKind.action),
                               ("handoff_ref", IdKind.handoff)):
                add(kind, getattr(payload, name, None))
        return cls(out)

    def new_id(self, kind: IdKind) -> str:
        queue = self._queues.get(kind)
        if queue:
            return queue.popleft()
        self._fallback[kind] += 1
        return f"{kind.value}-replay-{self._fallback[kind]:04d}"

    def secret_token(self) -> str:
        self._tokens += 1
        return base64.urlsafe_b64encode(self._tokens.to_bytes(16, "big")).rstrip(b"=").decode()


type ToolDefinitions = Callable[[EntityRef], ToolDef]


class RecordedToolExecutor:
    """Sirve los `tool_called` grabados en orden. Otra tool, o de más, es un `ReplayDesync`."""

    def __init__(self, events: list[EngineEvent], mode: ReplayMode, full: FullSource,
                 definitions: ToolDefinitions) -> None:
        self._calls = deque((e.seq, e.payload) for e in events  # type: ignore[attr-defined]
                            if getattr(e, "type", None) == "tool_called")
        self._mode, self._full, self._definitions = mode, full, definitions

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if not self._calls:
            raise ReplayDesync(None, None, {"tool": to_jsonable(tool)})
        seq, rec = self._calls[0]
        assert isinstance(rec, ToolCalledPayload)
        if rec.tool != tool:
            raise ReplayDesync(seq, {"tool": to_jsonable(rec.tool)}, {"tool": to_jsonable(tool)})
        self._calls.popleft()
        if self._mode == "fixture":
            full = self._full.tool_result(rec.call_id)
            return ToolResult(status=full.status, result_full=full.result_full, source=full.source,
                              call_id=rec.call_id, error=full.error, required_level=full.required_level)
        return ToolResult(status=rec.status, result_full=rec.result, call_id=rec.call_id, error=rec.error)

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._definitions(tool)


class RecordedGateway:
    def __init__(self, outputs: list[JsonValue]) -> None:
        self._outputs = deque(outputs)

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        if not self._outputs:
            raise ReplayDesync(None, None, {"prompt": to_jsonable(prompt)})
        from decimal import Decimal

        return GenerationResult(output=self._outputs.popleft(), tokens_in=0, tokens_out=0,
                                cost_usd=Decimal(0), model="replay-recorded")


class RecordedReadings:
    """Salidas no deterministas que el motor lee de los registros (guardas, decisiones, comandos,
    resultados de `rule`/`verify`/validador en modo `audit`)."""

    def __init__(self, events: list[EngineEvent]) -> None:
        self._events = list(events)

    def events_of(self, event_type: str, turn_id: str | None = None) -> list[EngineEvent]:
        return [e for e in self._events if getattr(e, "type", None) == event_type
                and (turn_id is None or e.turn_id == turn_id)]


@dataclass(frozen=True)
class RecordedPorts:
    mode: ReplayMode
    clock: RecordedClock
    ids: RecordedIds
    tools: RecordedToolExecutor
    llm: RecordedGateway
    readings: RecordedReadings
    full: FullSource


def build_ports(events: list[EngineEvent], mode: ReplayMode, *, full: FullSource, drafts: list[JsonValue],
                definitions: ToolDefinitions) -> RecordedPorts:
    return RecordedPorts(mode, RecordedClock.from_events(events), RecordedIds.from_events(events),
                         RecordedToolExecutor(events, mode, full, definitions), RecordedGateway(drafts),
                         RecordedReadings(events), full)
```
`UtcDatetime`, `Locale`, `ToolDef`, `EntityRef` salen de `agent_core.domain` (`UtcDatetime`/`Locale` desde `agent_core.domain.base` si no se reexportan). Los tipos `ToolStatus` de `ToolResult.status` aceptan `ToolStatus`.

- [ ] **Step 4: Implementar `recording.py`**

```python
"""Envoltorios para `agentcore record` (M11 §3.5): capturan lo `full` y los borradores de una corrida sintética."""

from agent_core.audit.replay.fixture import Fixture, FullToolResult
from agent_core.domain import EngineEvent, EntityRef, JsonValue, Locale, ToolDef
from agent_core.ports import GenerationResult, LLMGateway, ToolCallContext, ToolExecutor, ToolResult


class RecordingToolExecutor:
    def __init__(self, inner: ToolExecutor) -> None:
        self._inner = inner
        self.captured: dict[str, FullToolResult] = {}

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        result = self._inner.execute(tool, args, bound_params, ctx, idempotency_key)
        self.captured[result.call_id] = FullToolResult(
            status=result.status, result_full=result.result_full, source=result.source,
            error=result.error, required_level=result.required_level)
        return result

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._inner.definition(tool)


class RecordingGateway:
    def __init__(self, inner: LLMGateway) -> None:
        self._inner = inner
        self.drafts: list[JsonValue] = []

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        result = self._inner.generate(prompt, inputs_model_view, locale, schema)
        self.drafts.append(result.output)
        return result


def build_fixture(name: str, run_id: str, release: str, events: list[EngineEvent],
                  inputs: list[dict[str, JsonValue]], tools: RecordingToolExecutor,
                  llm: RecordingGateway) -> Fixture:
    return Fixture(name=name, run_id=run_id, release=release, inputs=inputs, events=events,
                   full=dict(tools.captured), drafts=list(llm.drafts))
```

- [ ] **Step 5: Ver que pasa** — Run: `uv run pytest tests/m11 -v && uv run mypy && uv run lint-imports && uv run ruff check .` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_core/audit/replay/ports.py agent_core/audit/replay/recording.py tests/m11/test_recorded_ports.py tests/m11/test_recording.py
git commit -m "feat(m11): puertos grabados del replay y envoltorios de grabación" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `Replayer` (T-M11-02, 03, 04, 10)

**Files:**
- Create: `agent_core/audit/replay/runner.py`, `tests/m11/engine_stub.py`, `tests/m11/test_replayer.py`

**Interfaces:**
- Consumes: `check_chain` (Task 2); `first_divergence`, `ReplayReport`, `Divergence`, `ReplayMode` (Task 9); `Fixture` (Task 10); `RecordedPorts`, `build_ports`, `FixtureFullSource`, `ForbiddenFullSource`, `ReplayDesync`, `FullViewAccessError` (Task 11); `Clock`, `AuditSink`, `TranscriptStore` (`agent_core.ports`).
- Produces:
  - `@dataclass(frozen=True) class ReplayCase: run_id: str; release: str; mode: ReplayMode; inputs: list[dict[str, JsonValue]]; recorded: list[EngineEvent]`
  - `class EngineRunner(Protocol): def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]`
  - `class Replayer: __init__(self, engine: EngineRunner, clock: Clock, *, audit: AuditSink | None = None, transcript: TranscriptStore | None = None, definitions: ToolDefinitions | None = None)`; `replay(self, source: Fixture | str, mode: ReplayMode) -> ReplayReport`

- [ ] **Step 1: Motor de prueba y pruebas que fallan**

`tests/m11/engine_stub.py`:
```python
"""Motor de prueba: re-emite los eventos grabados pasando por los puertos grabados, con mutaciones opcionales.
Reproduce en pequeño lo que hará M4 + M2: pide tools/LLM a los puertos y devuelve la secuencia producida."""

from collections.abc import Callable

from agent_core.audit.replay.runner import ReplayCase
from agent_core.audit.replay.ports import RecordedPorts
from agent_core.domain import EngineEvent

Mutation = Callable[[EngineEvent], EngineEvent]


class StubEngine:
    def __init__(self, mutate: Mutation | None = None, *, touch_full: bool = False) -> None:
        self._mutate, self._touch_full = mutate, touch_full
        self.calls: list[str] = []

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        for e in case.recorded:
            kind = getattr(e, "type", "")
            if kind == "tool_called":
                self.calls.append("tool")
                ports.tools.execute(e.payload.tool, {}, {}, None)  # type: ignore[attr-defined, arg-type]
                if self._touch_full:
                    ports.full.tool_result(e.payload.call_id)  # type: ignore[attr-defined]
            out.append(self._mutate(e) if self._mutate else e)
        return out
```

`tests/m11/test_replayer.py`:
```python
"""T-M11-02, 03, 04 y 10 sobre el Replayer con un motor de prueba (T-M11-01 va en la Task 14)."""

import pytest

from agent_core.audit.chain import chain_events
from agent_core.audit.log import AuditLog
from agent_core.audit.replay.fixture import FullToolResult, Fixture
from agent_core.audit.replay.ports import FullViewAccessError
from agent_core.audit.replay.runner import Replayer
from testing.fakes.clock import FakeClock
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.engine_stub import StubEngine
from tests.m11.helpers import event

FULL = {"call-0001": FullToolResult(status="ok", result_full={"count": 2}, source="tx")}


def recorded() -> list:  # type: ignore[type-arg]
    return chain_events("run-0001", [event("run_started", turn_id=None, n=1), event("turn_started", n=2),
                                     event("rule_evaluated", n=3), event("tool_called", n=4),
                                     event("turn_completed", n=5), event("run_closed", n=6)], None)


def fixture(events: list | None = None) -> Fixture:  # type: ignore[type-arg]
    return Fixture(name="demo", run_id="run-0001", release="rel-2026-09-28", inputs=[],
                   events=events or recorded(), full=FULL, drafts=[])


def flip_rule(e):  # type: ignore[no-untyped-def]
    if getattr(e, "type", "") == "rule_evaluated":
        return e.model_copy(update={"payload": e.payload.model_copy(update={"result": True})})
    return e


def replayer(engine: StubEngine, store: InMemoryStore | None = None) -> Replayer:
    audit = InMemoryAuditSink(store) if store else None
    return Replayer(engine, FakeClock(), audit=audit, definitions=lambda r: None)  # type: ignore[arg-type, return-value]


def test_t_m11_01_shape_fixture_replay_of_an_unchanged_engine_matches() -> None:
    report = replayer(StubEngine()).replay(fixture(), "fixture")
    assert (report.verdict, report.mode, report.run_id, report.release) == (
        "match", "fixture", "run-0001", "rel-2026-09-28")
    assert report.first_divergence is None and report.duration_ms is not None


def test_t_m11_02_changed_rule_diverges_with_first_divergence() -> None:
    report = replayer(StubEngine(flip_rule)).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence is not None
    assert report.first_divergence.event_seq == 2
    assert report.first_divergence.expected["payload"]["result"] is False  # type: ignore[index]
    assert report.first_divergence.actual["payload"]["result"] is True  # type: ignore[index]


@pytest.mark.parametrize("mode", ["fixture", "audit"])
def test_t_m11_03_altered_event_is_chain_broken_in_both_modes(mode: str) -> None:
    events = recorded()
    events[2] = events[2].model_copy(update={"release": "rel-alterada"})
    engine = StubEngine()
    report = replayer(engine).replay(fixture(events), mode)  # type: ignore[arg-type]
    assert report.verdict == "chain_broken" and report.chain_broken_at == 2
    assert engine.calls == []  # el motor ni siquiera corre


def test_t_m11_04_audit_replay_of_a_recorded_run_matches_without_touching_full() -> None:
    store = InMemoryStore()
    log = AuditLog(InMemoryAuditSink(store), store.uow)
    with store.uow() as uow:
        log.append(uow, "run-0001", [e.model_copy(update={"seq": None, "prev_hash": None, "hash": None})
                                     for e in recorded()])
        uow.commit()
    report = replayer(StubEngine(), store).replay("run-0001", "audit")
    assert report.verdict == "match" and report.mode == "audit"


def test_t_m11_04_audit_engine_touching_full_raises_full_view_access_error() -> None:
    with pytest.raises(FullViewAccessError):
        replayer(StubEngine(touch_full=True)).replay(fixture(), "audit")


def test_t_m11_10_measured_fields_differing_still_match() -> None:
    def remeasure(e):  # type: ignore[no-untyped-def]
        kind = getattr(e, "type", "")
        if kind == "tool_called":
            return e.model_copy(update={"payload": e.payload.model_copy(update={"latency_ms": 9999})})
        if kind == "turn_completed":
            return e.model_copy(update={"payload": e.payload.model_copy(update={"duration_ms": 1})})
        return e

    assert replayer(StubEngine(remeasure)).replay(fixture(), "fixture").verdict == "match"


def test_t_m11_10_change_elsewhere_in_the_same_event_diverges() -> None:
    def both(e):  # type: ignore[no-untyped-def]
        if getattr(e, "type", "") == "tool_called":
            return e.model_copy(update={"payload": e.payload.model_copy(update={"latency_ms": 1, "attempt": 2})})
        return e

    report = replayer(StubEngine(both)).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence.event_seq == 3  # type: ignore[union-attr]


def test_engine_desync_is_reported_as_divergence() -> None:
    class Wrong(StubEngine):
        def run(self, case, ports):  # type: ignore[no-untyped-def]
            from agent_core.domain import EntityRef

            ports.tools.execute(EntityRef.parse("otra@1.0.0"), {}, {}, None)  # type: ignore[arg-type]
            return []

    report = replayer(Wrong()).replay(fixture(), "fixture")
    assert report.verdict == "diverged" and report.first_divergence is not None


def test_usage_errors() -> None:
    with pytest.raises(ValueError):
        replayer(StubEngine()).replay("run-0001", "audit")      # run_id sin AuditSink
    with pytest.raises(ValueError):
        replayer(StubEngine(), InMemoryStore()).replay("run-0001", "fixture")  # fixture pide un Fixture


def test_replay_never_receives_real_ports() -> None:
    """El motor solo recibe `RecordedPorts`: no hay camino a modelos ni tools reales."""
    seen: list[object] = []

    class Spy(StubEngine):
        def run(self, case, ports):  # type: ignore[no-untyped-def]
            seen.append(ports)
            return list(case.recorded)

    replayer(Spy()).replay(fixture(), "fixture")
    from agent_core.audit.replay.ports import RecordedPorts

    assert isinstance(seen[0], RecordedPorts)
```
Nota: `FakeClock` de `testing.fakes.clock`; verifica su constructor. `Replayer.duration_ms` se mide con `clock.monotonic_ns()`.

- [ ] **Step 2: Ver que falla** — Run: `uv run pytest tests/m11/test_replayer.py -v` — Expected: FAIL (`ModuleNotFoundError: agent_core.audit.replay.runner`).

- [ ] **Step 3: Implementar**

`agent_core/audit/replay/runner.py`:
```python
"""Replay (M11 §3.4): verify_chain → puertos grabados → motor inyectado → comparación."""

from dataclasses import dataclass
from typing import Protocol

from agent_core.audit.chain import check_chain
from agent_core.audit.replay.compare import first_divergence
from agent_core.audit.replay.fixture import Fixture
from agent_core.audit.replay.ports import (
    FixtureFullSource,
    ForbiddenFullSource,
    FullSource,
    RecordedPorts,
    ReplayDesync,
    ToolDefinitions,
    build_ports,
)
from agent_core.audit.replay.report import Divergence, ReplayMode, ReplayReport
from agent_core.domain import EngineEvent, JsonValue
from agent_core.ports import AuditSink, Clock, TranscriptStore


@dataclass(frozen=True)
class ReplayCase:
    run_id: str
    release: str
    mode: ReplayMode
    inputs: list[dict[str, JsonValue]]
    recorded: list[EngineEvent]


class EngineRunner(Protocol):
    """Corre M4 + M2 con los puertos grabados y devuelve la secuencia de eventos producida.
    La implementa el cableado (`agent_core.cli`), porque `audit` no puede importar `turn` ni `interpreter`.
    Debe lanzar `ReplayDesync` (lo hacen los puertos grabados) y no puede usar más puertos que `RecordedPorts`."""

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]: ...


def _no_definitions(_: object) -> object:
    raise KeyError("sin definiciones de tools")


class Replayer:
    def __init__(self, engine: EngineRunner, clock: Clock, *, audit: AuditSink | None = None,
                 transcript: TranscriptStore | None = None, definitions: ToolDefinitions | None = None) -> None:
        self._engine, self._clock, self._audit, self._transcript = engine, clock, audit, transcript
        self._definitions: ToolDefinitions = definitions or _no_definitions  # type: ignore[assignment]

    def replay(self, source: Fixture | str, mode: ReplayMode) -> ReplayReport:
        start = self._clock.monotonic_ns()
        events, inputs, full, drafts, run_id, release = self._resolve(source, mode)
        chain = check_chain(run_id, events)
        if not chain.ok:
            return self._report(mode, run_id, release, start, verdict="chain_broken",
                                chain_broken_at=chain.broken_at)
        ports = build_ports(events, mode, full=full, drafts=drafts, definitions=self._definitions)
        case = ReplayCase(run_id, release, mode, inputs, events)
        try:
            produced = self._engine.run(case, ports)
        except ReplayDesync as desync:
            div = Divergence(event_seq=desync.event_seq, expected=desync.expected, actual=desync.actual)
            return self._report(mode, run_id, release, start, verdict="diverged", first_divergence=div)
        div = first_divergence(events, produced)
        return self._report(mode, run_id, release, start, verdict="match" if div is None else "diverged",
                            first_divergence=div)

    def _resolve(self, source: Fixture | str, mode: ReplayMode
                 ) -> tuple[list[EngineEvent], list[dict[str, JsonValue]], FullSource, list[JsonValue], str, str]:
        if isinstance(source, Fixture):
            full: FullSource = FixtureFullSource(source.full) if mode == "fixture" else ForbiddenFullSource()
            drafts = source.drafts if mode == "fixture" else []
            return source.events, source.inputs, full, drafts, source.run_id, source.release
        if mode == "fixture":
            raise ValueError("el modo fixture necesita un Fixture, no un run_id")
        if self._audit is None:
            raise ValueError("replay por run_id necesita un AuditSink")
        events = self._audit.read(source)
        release = events[0].release if events else ""
        entries = self._transcript.read(source) if self._transcript is not None else []
        inputs: list[dict[str, JsonValue]] = [
            {"turn_id": e.turn_id, "text_model": e.text_model} for e in entries if e.role == "user"]
        drafts = [e.text_model for e in entries if e.role != "user"]
        return events, inputs, ForbiddenFullSource(), drafts, source, release

    def _report(self, mode: ReplayMode, run_id: str, release: str, start: int, **fields: object) -> ReplayReport:
        elapsed = (self._clock.monotonic_ns() - start) // 1_000_000
        return ReplayReport(mode=mode, run_id=run_id, release=release, duration_ms=elapsed,
                            **fields)  # type: ignore[arg-type]
```
Ajusta los tipos (`ReplayReport(**fields)` tipado con `Unpack`/campos explícitos) para que `mypy --strict` pase, sin cambiar semántica.

- [ ] **Step 4: Ver que pasa** — Run: `uv run pytest tests/m11 -v && uv run mypy && uv run lint-imports && uv run ruff check .` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/audit/replay/runner.py tests/m11/engine_stub.py tests/m11/test_replayer.py
git commit -m "feat(m11): Replayer con modos fixture y audit (T-M11-02, 03, 04, 10)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Interfaz pública, CLI (`record`/`replay`) y trazabilidad

**Files:**
- Modify: `agent_core/audit/__init__.py`, `agent_core/cli.py`
- Create: `agent_core/audit/replay/cli_support.py` (carga de motor y de fixtures para el CLI), `tests/m11/test_public_api.py`, `tests/m11/test_cli.py`, `tests/m11/test_traceability.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: la interfaz pública de `agent_core.audit`; `agentcore replay <ruta|run_id> --mode fixture|audit [--json] [--catalog RUTA]` con códigos de salida de la decisión 17; `agentcore record <camino> --out RUTA` (implementación real en la Task 14; aquí sale con código 3 y mensaje claro si no hay motor).

- [ ] **Step 1: Pruebas que fallan**

`tests/m11/test_public_api.py`:
```python
"""La interfaz pública de M11 exporta lo que M4 y M9 consumen y nada de los internos."""

import agent_core.audit as audit

EXPECTED = {
    "AuditLog", "ChainCheck", "ChainedEvent", "TurnRecorder", "TranscriptReader", "RenderedEntry",
    "TranscriptWriteError", "RunNotFound", "TRANSCRIPT_PURPOSE", "export_events", "chain_integrity",
    "ReplayReport", "Divergence", "Replayer", "ReplayCase", "EngineRunner", "RecordedPorts", "ReplayDesync",
    "FullViewAccessError", "Fixture", "FullToolResult", "load_fixture", "dump_fixture", "load_fixture_file",
    "SyntheticCatalog", "load_catalog", "check_fixture", "FixtureRejected", "RecordingToolExecutor",
    "RecordingGateway", "build_fixture", "check_chain", "chain_events",
}


def test_exports_are_exactly_the_public_interface() -> None:
    assert set(audit.__all__) == EXPECTED
    for name in EXPECTED:
        assert hasattr(audit, name)
```

`tests/m11/test_cli.py`:
```python
"""`agentcore replay` y `record`: códigos de salida (decisión 17) y sin motor disponible."""

from pathlib import Path

from agent_core.audit import chain_events, dump_fixture
from agent_core.audit import Fixture
from agent_core.cli import main
from tests.m11.helpers import event


def write_fixture(tmp_path: Path, tamper: bool = False) -> Path:
    events = chain_events("run-0001", [event("run_started", turn_id=None), event("run_closed", n=2)], None)
    if tamper:
        events[1] = events[1].model_copy(update={"release": "otra"})
    path = tmp_path / "camino.yaml"
    path.write_text(dump_fixture(Fixture(name="camino", run_id="run-0001", release="rel-2026-09-28", inputs=[],
                                         events=events, full={}, drafts=[])), encoding="utf-8")
    return path


def test_replay_chain_broken_exits_2_even_without_an_engine(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["replay", str(write_fixture(tmp_path, tamper=True)), "--mode", "fixture", "--json"])
    assert code == 2 and '"chain_broken"' in capsys.readouterr().out


def test_replay_without_engine_available_exits_3(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(["replay", str(write_fixture(tmp_path)), "--mode", "fixture"])
    assert code == 3 and "motor" in capsys.readouterr().err


def test_replay_rejects_unsynthetic_fixture_with_catalog(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = write_fixture(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("inputs: []", "inputs:\n- text_model: mail real@banco.com"),
                    encoding="utf-8")
    code = main(["replay", str(path), "--mode", "fixture", "--catalog", "tests/fixtures/catalogo-datos-prueba.yaml"])
    assert code == 3 and "no sintéticos" in capsys.readouterr().err


def test_record_without_engine_exits_3(tmp_path: Path) -> None:
    assert main(["record", "resuelto", "--out", str(tmp_path / "x.yaml")]) == 3
```

`tests/m11/test_traceability.py`:
```python
"""Cada prueba T-M11-NN del spec existe en tests/m11 (T-M11-01 se agrega en la Task 14)."""

import re
from pathlib import Path

REQUIRED = {f"T-M11-{n:02d}" for n in range(2, 11)}


def test_every_spec_test_id_is_referenced_by_a_test() -> None:
    text = " ".join(p.read_text(encoding="utf-8") for p in Path("tests/m11").glob("test_*.py")).lower()
    found = {i for i in REQUIRED if i.lower().replace("-", "_") in text or i.lower() in text}
    assert found == REQUIRED, sorted(REQUIRED - found)
```
(Los tests de las tasks anteriores ya llevan `t_m11_NN` en su nombre o docstring: 02, 03, 04, 05, 06, 07, 08, 09, 10.)

- [ ] **Step 2: Ver que falla** — Run: `uv run pytest tests/m11/test_public_api.py tests/m11/test_cli.py tests/m11/test_traceability.py -v` — Expected: FAIL.

- [ ] **Step 3: Interfaz pública**

`agent_core/audit/__init__.py`:
```python
"""M11 — auditoría, transcript y replay (ADR 0003, ADR 0008)."""

from agent_core.audit.chain import ChainCheck, ChainedEvent, chain_events, check_chain
from agent_core.audit.export import chain_integrity, export_events
from agent_core.audit.log import AuditLog
from agent_core.audit.replay.fixture import (
    Fixture, FullToolResult, dump_fixture, load_fixture, load_fixture_file,
)
from agent_core.audit.replay.ports import FullViewAccessError, RecordedPorts, ReplayDesync
from agent_core.audit.replay.recording import RecordingGateway, RecordingToolExecutor, build_fixture
from agent_core.audit.replay.report import Divergence, ReplayReport
from agent_core.audit.replay.runner import EngineRunner, ReplayCase, Replayer
from agent_core.audit.replay.synthetic import FixtureRejected, SyntheticCatalog, check_fixture, load_catalog
from agent_core.audit.transcript import (
    TRANSCRIPT_PURPOSE, RenderedEntry, RunNotFound, TranscriptReader, TranscriptWriteError, TurnRecorder,
)

__all__ = [
    "TRANSCRIPT_PURPOSE", "AuditLog", "ChainCheck", "ChainedEvent", "Divergence", "EngineRunner", "Fixture",
    "FixtureRejected", "FullToolResult", "FullViewAccessError", "RecordedPorts", "RecordingGateway",
    "RecordingToolExecutor", "RenderedEntry", "ReplayCase", "ReplayDesync", "ReplayReport", "Replayer",
    "RunNotFound", "SyntheticCatalog", "TranscriptReader", "TranscriptWriteError", "TurnRecorder",
    "build_fixture", "chain_events", "chain_integrity", "check_chain", "check_fixture", "dump_fixture",
    "export_events", "load_catalog", "load_fixture", "load_fixture_file",
]
```
Verifica que el `__all__` coincida con `EXPECTED` del test (mismo conjunto).

- [ ] **Step 4: CLI**

`agent_core/audit/replay/cli_support.py`:
```python
"""Ayudas del CLI de replay: formato de salida y códigos (decisión 17)."""

from agent_core.audit.replay.report import ReplayReport

EXIT = {"match": 0, "diverged": 1, "chain_broken": 2}
USAGE_ERROR = 3


def render_report(report: ReplayReport, as_json: bool) -> str:
    if as_json:
        return report.model_dump_json()
    line = f"{report.verdict} · {report.mode} · {report.run_id}@{report.release}"
    if report.first_divergence is not None:
        line += f" · primera divergencia en seq {report.first_divergence.event_seq}"
    if report.chain_broken_at is not None:
        line += f" · cadena rota en seq {report.chain_broken_at}"
    return line
```
`agent_core/audit/replay/cli_support.py` no importa el motor. En `agent_core/cli.py` agrega:
- subparser `replay` (`target`, `--mode {fixture,audit}` requerido, `--json`, `--catalog`), y subparser `record` (`scenario`, `--out`).
- `replay`: carga el fixture (`load_fixture_file`), aplica `check_fixture` si hay `--catalog` (rechazo → stderr `"fixture con datos no sintéticos: …"` y exit 3); **verifica la cadena antes de pedir el motor** (`check_chain`): rota → imprime el reporte `chain_broken` y exit 2; si no, obtiene el motor con `load_engine()`.
- `load_engine()` en `cli.py`: intenta `from agent_core.turn import ...` para construir el `EngineRunner`; mientras M4 no exista, lanza `EngineUnavailable` → stderr `"motor M4/M2 no disponible: el replay necesita el motor integrado"` y exit 3.
- `record`: mismo `load_engine()`; sin motor → exit 3.
Usa `SystemClock` (`agent_core.adapters.system_clock`) para el `Replayer`.

- [ ] **Step 5: Ver que pasa** — Run: `uv run pytest tests/m11 -v && uv run mypy && uv run lint-imports && uv run ruff check . && uv run agentcore contracts --check` — Expected: PASS (`contracts --check` no cambia: M11 no toca M0).

- [ ] **Step 6: Commit**

```bash
git add agent_core/audit agent_core/cli.py tests/m11
git commit -m "feat(m11): interfaz pública, comandos agentcore replay/record y trazabilidad" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Integración con M4/M2 — fixtures de los seis caminos y T-M11-01 (BLOQUEADA hasta que M4, M2, M5, M6 y M8 estén integrados)

**Files:**
- Modify: `agent_core/cli.py` (`load_engine`, `record`), `tests/m11/test_traceability.py` (REQUIRED pasa a `range(1, 11)`)
- Create: `tests/fixtures/runs/{resuelto,cancelado,escalado-por-monto,uncertain-verify,step-up,interrupcion}.yaml` (generados, no escritos a mano), `tests/m11/test_replay_fixtures.py`, `tests/m11/test_recorded_ports_wiring.py`

**Interfaces:**
- Consumes: la interfaz pública de M4 (`agent_core.turn`), M2 (`agent_core.interpreter`), M5/M6/M8, el flow de demo `disputa-cargo` (fixture de M1 en `tests/m01/fixtures/registry`), `AuditLog.recorder()`, `TurnRecorder`, `Replayer`, `RecordedPorts`.
- Produces: `agent_core.cli.load_engine() -> EngineRunner` real; `agentcore record <camino> --out <ruta>` real; los seis fixtures; T-M11-01.

**Precondiciones (verifícalas primero; si no se cumplen, detente y avisa, no inventes interfaces):**
1. `agent_core.turn` exporta el ciclo del turno y acepta por constructor `Clock`, `IdSource`, `ToolExecutor`, `UnitOfWorkFactory`, `TurnRecorder` (M11) y los servicios de M5/M6/M2.
2. M5 (`DecisionService`/`UnderstandService`) y M6 (`GuardService`) tienen interfaz pública estable.
3. El arnés de la demo corre `disputa-cargo` con dobles (`testing/fakes/`).
4. M2/M3/M8 tienen un gancho para leer resultados de `rule`/`verify`/validador desde `RecordedReadings` en modo `audit` (decisión 11). Si no existe, abre un issue en el spec de cada módulo y deja el modo `audit` con `chain_broken`/`match` de integridad solamente (es el recorte 1 del índice §7).

- [ ] **Step 1: Adaptadores de puertos de M5/M6 sobre `RecordedReadings`**

En `agent_core/cli.py` (o un módulo `agent_core/replay_wiring.py` nuevo, que puede importar todo) implementa `RecordedGuardService`, `RecordedUnderstandService` y `RecordedDecisionService` con los tipos reales de M5/M6: cada uno devuelve el payload de `turn_started.guards`, `command_emitted` y `decision_made` del turno vía `ports.readings.events_of(...)`. Prueba unitaria en `tests/m11/test_recorded_ports_wiring.py`: cada adaptador devuelve el valor grabado y lanza `ReplayDesync` si no hay evento para el turno.

- [ ] **Step 2: `load_engine()` real y prueba de humo del motor**

`load_engine()` construye un `EngineRunner` que, por cada turno de `case.inputs`, llama a `RecordedClock.enter_turn(turn_id)`, arma M4 con `ports.clock`, `ports.ids`, `ports.tools`, `ports.llm` y los tres servicios grabados, corre el turno sobre un `InMemoryStore` limpio y devuelve los eventos que M4 persistió (`InMemoryAuditSink.read`). Nota: el motor encadena con `AuditLog`, así que los eventos producidos traen su propia cadena (ya excluida de la comparación).

- [ ] **Step 3: Generar los seis fixtures con `agentcore record`**

Implementa `record`: corre el camino guionado de la demo con `RecordingToolExecutor` y `RecordingGateway` envolviendo los dobles, con datos del catálogo sintético, y escribe `dump_fixture(build_fixture(...))`. Caminos: `resuelto`, `cancelado`, `escalado-por-monto`, `uncertain-verify`, `step-up`, `interrupcion`. Run: `uv run agentcore record <camino> --out tests/fixtures/runs/<camino>.yaml` para cada uno y revisa a mano que no haya datos reales.

- [ ] **Step 4: T-M11-01**

`tests/m11/test_replay_fixtures.py`:
```python
"""T-M11-01: el replay `fixture` de cada camino del flow de demo da `match`, y ningún fixture trae datos no sintéticos."""

from pathlib import Path

import pytest

from agent_core.audit import check_fixture, load_catalog, load_fixture_file
from agent_core.adapters.system_clock import SystemClock
from agent_core.audit import Replayer
from agent_core.cli import load_engine

CAMINOS = ["resuelto", "cancelado", "escalado-por-monto", "uncertain-verify", "step-up", "interrupcion"]
CATALOG = load_catalog(Path("tests/fixtures/catalogo-datos-prueba.yaml"))


@pytest.mark.parametrize("camino", CAMINOS)
def test_t_m11_01_fixture_replay_matches_for_each_demo_path(camino: str) -> None:
    fixture = load_fixture_file(Path(f"tests/fixtures/runs/{camino}.yaml"))
    check_fixture(fixture, CATALOG)
    report = Replayer(load_engine(), SystemClock()).replay(fixture, "fixture")
    assert report.verdict == "match", report.first_divergence


def test_fixtures_cover_all_six_paths() -> None:
    assert {p.stem for p in Path("tests/fixtures/runs").glob("*.yaml")} == set(CAMINOS)
```
Run: `uv run pytest tests/m11/test_replay_fixtures.py -v`. Expected: PASS. Si hay `diverged` en un camino, **el hallazgo es real** (orden de IDs, reloj, gancho faltante): corrige la causa en el módulo dueño (M2/M3/M4/M8; no en la comparación) o documenta la limitación en el spec; nunca amplíes lo que se ignora.

- [ ] **Step 5: Prueba de mutación y modo `audit`**

Añade a `tests/m11/test_replay_fixtures.py` un caso que cambie en memoria una regla de `escalado-por-monto` (umbral de la policy) y espere `diverged` con `first_divergence.event_seq` del `rule_evaluated`; y, si la precondición 4 se cumple, un caso `audit` sobre el run grabado del camino `resuelto` (volcado a un `InMemoryStore` con `AuditLog`) que dé `match` con `ForbiddenFullSource`. Si la precondición 4 no se cumple, marca ese caso `pytest.mark.xfail(reason="…gancho audit pendiente en M2/M3/M8", strict=True)` y anótalo en el spec §11 (recorte 1 del índice).

- [ ] **Step 6: CI y definición de terminado**

En `.github/workflows/ci.yml` agrega, tras `uv run pytest`, `- run: uv run agentcore replay --help` no; en su lugar confirma que `uv run pytest tests/m11/test_replay_fixtures.py` ya corre dentro de `uv run pytest` (bloquea el merge si diverge). Actualiza `tests/m11/test_traceability.py` a `range(1, 11)`.

Run: `uv run pytest && uv run ruff check . && uv run mypy && uv run lint-imports && uv run agentcore contracts --check`
Expected: todo verde.

Marca punto por punto la **Definición de terminado** del spec:
- [ ] Fase 2: cadena (Tasks 2–3, 8), spans (6), transcript (4–5), T-M11-06…09.
- [ ] T-M11-10 con el replay (Tasks 9, 12).
- [ ] Fase 5: replay `fixture` en CI con los seis caminos y T-M11-01…05 (Tasks 10–14); `audit` si alcanza (decisión 11).
- [ ] Comandos `agentcore record` y `agentcore replay --mode fixture|audit` (Tasks 13–14).
- [ ] Comunes: interfaz pública exportada y tipada, `import-linter` verde, eventos validados contra el esquema de M0 (los produce M11 solo encadenados; `EngineEvent` no cambia), sin TODO sin issue.

- [ ] **Step 7: Commit**

```bash
git add agent_core tests .github/workflows/ci.yml
git commit -m "feat(m11): replay fixture de los seis caminos y comandos record/replay integrados con M4" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revisión (spec vs. plan)

**Cobertura del spec**

| Requisito del spec | Tarea |
|---|---|
| §3.1 cadena, `hash_0`, `seq`, append en la UoW | 2, 3 |
| §3.2 spans, atributos, captura desactivada, OTLP | 6 |
| §3.3 transcript en vista `model`, huella HMAC+`kid`, `read_rendered` | 4, 5 |
| §3.4 replay con puertos grabados, comparación, `chain_broken` | 9, 11, 12 |
| §3.5 formato de fixture, `record`, rechazo de datos no sintéticos, un fixture por camino | 10, 13, 14 |
| §4 invariantes (append-only por permiso de BD; replay sin modelos/tools reales; `audit` sin `full`; sin sha256 sin clave) | 8, 11–12 (`ForbiddenFullSource`), 4 |
| §5 fallas (cadena rota, fixture no sintético, transcript caído, backend de trazas caído) | 2/12, 10, 4, 6 |
| §7 T-M11-01…10 | 01→14; 02, 03, 04, 10→12 (+2, 9); 05→10; 06→4; 07, 08→5; 09→6, 7 |
| §8 evaluación (exportación, integridad, tiempo de replay, cobertura por caminos) | 7 (`export_events`, `chain_integrity`), 12 (`duration_ms`), 14 (seis caminos) |
| §10 comandos `agentcore record`/`replay` | 13, 14 |

**Sin placeholders:** los pasos con código muestran el código; los "ajusta…" son ajustes de tipos/imports que no cambian nombres. La Task 14 no trae código de motor porque depende de interfaces de M4/M5/M6 que no existen todavía: lista precondiciones verificables en lugar de inventar firmas.

**Consistencia de tipos:** `chain_events(run_id, events, last)`, `check_chain(run_id, events)`, `ChainCheck(ok, broken_at, reason)`, `AuditLog(sink, uow_factory)`, `ReplayReport` (con `chain_broken_at`, `duration_ms`), `ReplayCase`, `RecordedPorts` y `Fixture` se usan con los mismos nombres y firmas en las Tasks 2–14.

## Riesgos y dependencias abiertas

1. **Task 14 bloqueada por M4/M2/M5/M6/M8:** sin el motor integrado no hay T-M11-01 ni `record`/`replay` reales; hasta entonces la Task 13 deja `exit 3`.
2. **Ganchos del modo `audit`:** que M2/M3/M8 lean `RecordedReadings` en modo `audit` es un contrato nuevo; si no llega, `audit` queda en integridad de cadena (recorte 1 del índice).
3. **Orden de IDs:** `RecordedIds` asume que los IDs se crean en el orden de la cadena; una divergencia falsa aquí indica un módulo que crea IDs en otro orden.
4. **M9 debe usar `AuditLog.append_standalone`** para `access_denied` (el `AuditSink.append_outside_turn` no encadena) y **M4 debe cablear `AuditLog.recorder()`** como `ActionContext.record` y volcar los eventos pendientes antes de los de M3.
5. **Idempotencia del transcript** ante reintento parcial: abierta con la unidad 7.
6. **Versión de semconv GenAI** (`SEMCONV_VERSION`): no verificada (ADR 0003); confirmar contra el paquete instalado.
7. **Dependencias nuevas** (`psycopg`, `opentelemetry-*`) tocan `pyproject.toml` y `uv.lock`, y `.importlinter` cambia a `root_packages`; coordinar con los agentes de M6/M10 si tocan los mismos archivos.
8. **Postgres en CI:** las pruebas de integración fallan (no se omiten) con `AGENTCORE_REQUIRE_POSTGRES=1`.
