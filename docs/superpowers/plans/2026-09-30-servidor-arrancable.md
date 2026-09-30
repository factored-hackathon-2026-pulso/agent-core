# Servidor arrancable (`agentcore serve`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `agentcore serve` arranque el servidor HTTP de la demo con Postgres, gateway de LLM, JEV e identidad reales, y con dobles etiquetados (solo bajo `AGENTCORE_ALLOW_DEMO=1`) para lo que depende de las unidades 3, 6 y 7.

**Architecture:** `build_turn_engine` pasa a apoyarse en un `build_engine` que también devuelve `HandoffService` y `TranscriptReader`. Un dataclass `ServePorts` agrupa todos los puertos; `resolve_ports` los construye desde argumentos y entorno (piezas reales por defecto, dobles por ruta `modulo:atributo` solo con la variable de demo); `build_api_deps` convierte `ServePorts` en `ApiDeps`; `run_serve` lanza `uvicorn`. Es el mismo patrón que `agentcore registry` (`--verifier`, `--harness`).

**Tech Stack:** Python 3.12, FastAPI, uvicorn, psycopg 3, PyYAML, cryptography (Ed25519), pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-30-servidor-arrancable-design.md`

**Nota sobre commits:** el repo es git, pero solo se hacen commits si el usuario lo autoriza. Cada tarea termina con un punto de control (`pytest`, `lint-imports`, `mypy`, `ruff`), no con un commit.

## Global Constraints

- Python 3.12; `uv run …` para todo comando (CLAUDE.md).
- Fronteras: solo `agent_core/composition` importa adaptadores concretos y `testing/` no se importa desde `agent_core/` salvo por ruta `modulo:atributo` cargada en tiempo de ejecución (`importlib`), como ya hace `composition/registry.py` (`_load`).
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`; el tiempo sale del `Clock` y los IDs del `IdSource` inyectados (`ruff` lo verifica).
- Todo JSON entra con `agent_core.domain.loads`.
- Sin dobles sin `AGENTCORE_ALLOW_DEMO=1`: `serve` termina con código 2 y nombra cada pieza faltante.
- Con `AGENTCORE_ALLOW_DEMO=1`, `serve` imprime en stderr la lista de piezas que son dobles.
- `contracts/` no cambia (no se toca M0): `uv run agentcore contracts --check` debe seguir en verde.
- Datos sintéticos únicamente; ninguna clave ni credencial real en el repo.
- Las claves de HMAC/cifrado vienen de `EnvKeyProvider` (falla cerrado si faltan `AGENTCORE_KEYS_FINGERPRINT` y `AGENTCORE_KEYS_TOKEN_MAP`), nunca de un doble.

## Review Focus

1. **Variable de demo con valor distinto de `1`** (`"0"`, `"true"`, vacío): debe tratarse como ausente (solo `"1"` habilita). Test en Task 3.
2. **Ruta `modulo:atributo` que no se puede importar** o con forma inválida: código 2 con mensaje claro, sin traza. Test en Task 3.
3. **Archivo de claves de identidad** con clave de longitud errónea, base64url inválido, `kid` repetido o mapa vacío: debe fallar cerrado al arrancar, sin imprimir el material. Test en Task 2.
4. **Clave de JEV ausente**: el arranque no falla (el modelo real es opcional en pruebas), pero la primera llamada falla con error claro; nunca se envía una clave vacía. Test en Task 4.
5. **Un doble configurado por ruta sin `ALLOW_DEMO`**: se rechaza igual (la ruta explícita no salta la protección en piezas que son dobles). Test en Task 3.

---

### Task 1: `build_engine` expone manejadores y lector de transcript

**Files:**
- Modify: `agent_core/composition/engine.py` (función `build_turn_engine`, líneas ~77-101)
- Modify: `agent_core/composition/__init__.py`
- Modify: `agent_core/registry/postgres/runtime.py` (añadir método público `release`)
- Test: `tests/composition/test_engine.py`

**Interfaces:**
- Consumes: `EngineDeps` (existente), `HandoffService`, `AuditLog`, `TranscriptReader(store, uow_factory, views, keys, ids)` de `agent_core.audit`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class BuiltEngine:
      turns: TurnEngine
      handoffs: HandoffService
      transcripts: TranscriptReader

  def build_engine(deps: EngineDeps) -> BuiltEngine: ...
  def build_turn_engine(deps: EngineDeps) -> TurnEngine: ...   # sin cambio de firma: devuelve build_engine(deps).turns
  ```
  y `PostgresRegistry.release(self, release_id: str) -> Release` (público; hoy solo existe el privado `_release`).

- [ ] **Step 1: Write the failing test**

Añadir al final de `tests/composition/test_engine.py`:

```python
from agent_core.composition import BuiltEngine, build_engine
from testing.engine_world import EngineWorld


def test_build_engine_exposes_handoffs_and_transcripts() -> None:
    world = EngineWorld()
    built = build_engine(world.deps)
    assert isinstance(built, BuiltEngine)
    assert callable(built.turns.start_run)
    assert callable(built.handoffs.get)
    assert callable(built.transcripts.read_rendered)


def test_build_turn_engine_keeps_returning_the_engine() -> None:
    from agent_core.composition import build_turn_engine
    from agent_core.turn import TurnEngine

    assert isinstance(build_turn_engine(EngineWorld().deps), TurnEngine)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/composition/test_engine.py -k "build_engine or keeps_returning" -v`
Expected: FAIL con `ImportError: cannot import name 'BuiltEngine'`.

- [ ] **Step 3: Write minimal implementation**

En `agent_core/composition/engine.py` añadir `from agent_core.audit import TranscriptReader` junto a los imports de `AuditLog`, y reemplazar `build_turn_engine` por:

```python
@dataclass(frozen=True)
class BuiltEngine:
    """El motor y los lectores que la API necesita de M10 y M11, armados con las mismas piezas."""

    turns: TurnEngine
    handoffs: HandoffService
    transcripts: TranscriptReader


def build_engine(deps: EngineDeps) -> BuiltEngine:
    cfg = deps.config
    views = ViewService(deps.keys, deps.authz, deps.clock, deps.classifier)
    decisions = DecisionService(deps.registry, deps.providers, deps.calibrations, deps.clock, deps.ids)
    actions = ActionManager(deps.ids, deps.clock)
    runtimes = EngineRuntimeFactory(
        clock=deps.clock, ids=deps.ids, keys=deps.keys, registry=deps.registry, releases=deps.releases,
        tools=deps.tools, gateway=deps.gateway, decisions=decisions, actions=actions, views=views,
        uow_factory=deps.uow_factory, authz=deps.authz, breaker=CircuitBreaker(),
        knowledge=None if deps.knowledge is None else KnowledgeService(deps.knowledge, deps.authz),
        config=RuntimeConfig(number_format=cfg.number_format, max_regenerations=cfg.max_regenerations,
                             priority=cfg.priority, lang_thresholds=cfg.lang_thresholds))
    handoffs = HandoffService(uow_factory=deps.uow_factory, registry=deps.registry, views=views,
                              authz=deps.authz, keys=deps.keys, clock=deps.clock, ids=deps.ids)
    turns = TurnEngine(
        uow_factory=deps.uow_factory, registry=deps.registry, clock=deps.clock, ids=deps.ids,
        guards=GuardService(deps.registry, deps.clock, deps.ids, dict(cfg.lang_thresholds)),
        understand=DecisionUnderstand(UnderstandService(decisions), deps.transcript,
                                      recent_turns=cfg.recent_turns),
        actions=actions, handoff=handoffs,
        recorder=TurnRecorder(deps.transcript, deps.keys), chain=AuditLog(deps.audit),
        audit=deps.audit, runtimes=runtimes, trace=deps.trace or DerivedTrace(), config=cfg.turn,
        authz=deps.authz)
    transcripts = TranscriptReader(deps.transcript, deps.uow_factory, views, deps.keys, deps.ids)
    return BuiltEngine(turns=turns, handoffs=handoffs, transcripts=transcripts)


def build_turn_engine(deps: EngineDeps) -> TurnEngine:
    return build_engine(deps).turns
```

En `agent_core/composition/__init__.py` importar y exportar `BuiltEngine` y `build_engine` (añadirlos a `__all__`, en orden alfabético).

En `agent_core/registry/postgres/runtime.py` añadir, bajo `_release`:

```python
    def release(self, release_id: str) -> Release:
        """La release fijada de un run por su id (para `EngineDeps.releases`)."""
        return self._release(release_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/composition tests/m04 tests/m11 -q`
Expected: PASS (el motor y los demás tests siguen verdes).

- [ ] **Step 5: Punto de control**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: sin errores.

---

### Task 2: Cargador de claves de identidad

**Files:**
- Create: `agent_core/adapters/identity_keys.py`
- Test: `tests/m09/test_identity_keys.py`

**Interfaces:**
- Consumes: `JwsIdentityVerifier(principal_keys, delegation_keys, grant_active)` y `b64url_decode` de `agent_core.adapters.jws_identity`.
- Produces:
  ```python
  def load_identity_verifier(path: Path, grant_active: Callable[[str, datetime], bool]) -> JwsIdentityVerifier: ...
  ```
  Formato del archivo (YAML o JSON): `{"principal_keys": {kid: b64url_de_32_bytes}, "delegation_keys": {kid: b64url_de_32_bytes}}`. Cualquier irregularidad lanza `SchemaError` con un mensaje que nombra el mapa y el `kid`, nunca el valor.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from agent_core.adapters.identity_keys import load_identity_verifier
from agent_core.adapters.jws_identity import b64url_encode
from agent_core.domain import SchemaError
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer


def _pub(key: object) -> str:
    raw = key.public_key().public_bytes(  # type: ignore[attr-defined]
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url_encode(raw)


def _file(tmp_path: Path, issuer: TestIdentityIssuer, **over: object) -> Path:
    body = {"principal_keys": {issuer.principal_kid: _pub(issuer.principal_key)},
            "delegation_keys": {issuer.delegation_kid: _pub(issuer.delegation_key)}, **over}
    import json
    path = tmp_path / "keys.yaml"
    path.write_text(json.dumps(body), encoding="utf-8")  # JSON es YAML válido
    return path


def test_a_credential_signed_by_the_issuer_verifies(tmp_path: Path) -> None:
    issuer = TestIdentityIssuer(FakeClock())
    verifier = load_identity_verifier(_file(tmp_path, issuer), lambda ref, now: True)
    assert verifier.verify(issuer.customer()).id == "cust-001"


@pytest.mark.parametrize("bad", [
    {"principal_keys": {}},                                   # mapa vacío
    {"principal_keys": {"k1": "no-es-b64url!"}},              # base64url inválido
    {"principal_keys": {"k1": "AAAA"}},                       # longitud distinta de 32 bytes
    {"principal_keys": {"k1": 5}},                            # no es texto
])
def test_invalid_key_files_fail_closed_without_echoing_the_value(tmp_path: Path, bad: dict) -> None:  # type: ignore[type-arg]
    issuer = TestIdentityIssuer(FakeClock())
    with pytest.raises(SchemaError) as info:
        load_identity_verifier(_file(tmp_path, issuer, **bad), lambda ref, now: True)
    assert "no-es-b64url" not in str(info.value)


def test_missing_file_is_a_schema_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        load_identity_verifier(tmp_path / "nope.yaml", lambda ref, now: True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/m09/test_identity_keys.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.adapters.identity_keys`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Carga las claves públicas de identidad (principal y delegación) desde un archivo y arma el verificador.

Formato: `{"principal_keys": {kid: b64url}, "delegation_keys": {kid: b64url}}` (32 bytes Ed25519 por clave).
Falla cerrado: cualquier irregularidad es un `SchemaError` que nombra el mapa y el `kid`, nunca el valor."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_core.adapters.jws_identity import JwsIdentityVerifier, b64url_decode
from agent_core.domain import SchemaError


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
            if len(material) != 32:
                raise ValueError("longitud")
            out[kid] = Ed25519PublicKey.from_public_bytes(material)
        except ValueError:
            raise SchemaError(f"{name}[{kid}]: clave Ed25519 inválida (base64url de 32 bytes)") from None
    return out


def load_identity_verifier(path: Path,
                           grant_active: Callable[[str, datetime], bool]) -> JwsIdentityVerifier:
    try:
        data = yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError):
        raise SchemaError(f"no se pudo leer el archivo de claves de identidad ({path.name})") from None
    if not isinstance(data, dict):
        raise SchemaError("el archivo de claves de identidad debe ser un mapa")
    return JwsIdentityVerifier(_keys(data, "principal_keys"), _keys(data, "delegation_keys"), grant_active)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/m09/test_identity_keys.py -v`
Expected: PASS. Si `test_invalid_key_files…` falla porque `{"principal_keys": {}}` deja `delegation_keys` válido, el mensaje ya viene de `principal_keys`; no cambiar el test.

- [ ] **Step 5: Punto de control**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Si `lint-imports` rechaza `adapters → domain`, revisar `.importlinter`: `jws_identity.py` ya importa `agent_core.domain`, así que el mismo contrato lo cubre.

---

### Task 3: Resolución de puertos con la protección de demo

**Files:**
- Create: `agent_core/composition/serve_ports.py`
- Create: `testing/serve_demo.py`
- Test: `tests/composition/test_serve_ports.py`

**Interfaces:**
- Consumes: `EngineDeps`-tipos de puertos; `OpenAICompatGateway(registry, endpoints, env)`, `load_endpoints(env)`, `HttpJevTransport(api_key, clock)`, `JevProvider(transport)`, `EnvKeyProvider(env)`, `PostgresStore(dsn)`, `PgRegistryStore`, `PostgresRegistry`, `load_identity_verifier`.
- Produces:
  ```python
  class ServeConfigError(Exception):
      def __init__(self, problems: list[str]) -> None: ...   # .problems
  @dataclass(frozen=True)
  class ServePorts:
      clock: Clock; ids: IdSource; keys: KeyProvider
      uow_factory: UnitOfWorkFactory; audit: AuditSink; counters: CostCounters
      registry: RegistryPort; releases: Callable[[str], Release]
      gateway: LLMGateway; providers: Mapping[str, DecisionProvider]
      tools: ToolExecutor; authz: AuthzPort; transcript: TranscriptStore
      calibrations: CalibrationSource; classifier: FieldClassifier | None
      verifier: IdentityVerifier
      doubles: tuple[str, ...]          # nombres de las piezas que son dobles (vacío = todo real)
  def add_serve_parser(sub: Any) -> None: ...
  def resolve_ports(args: argparse.Namespace, env: Callable[[str], str | None],
                    clock: Clock, ids: IdSource) -> ServePorts: ...
  ```
  Piezas que son dobles y su opción/ruta por defecto en demo (`testing/serve_demo.py`, todas `factory(ctx: DemoContext) -> pieza`):
  | Opción | Pieza | Ruta por defecto en demo |
  |---|---|---|
  | `--tools` | `ToolExecutor` | `testing.serve_demo:tools` |
  | `--authz` | `AuthzPort` | `testing.serve_demo:authz` |
  | `--transcript` | `TranscriptStore` | `testing.serve_demo:transcript` |
  | `--calibration` | `CalibrationSource` | `testing.serve_demo:calibration` |
  | `--classifier` | proveedor `classifier` de decisión | `testing.serve_demo:classifier_provider` |
  | `--field-classifier` | `FieldClassifier` | `testing.serve_demo:field_classifier` |
  | `--grant-active` | `Callable[[str, datetime], bool]` | `testing.serve_demo:grant_active` |
  y `DemoContext` es `@dataclass(frozen=True) class DemoContext: clock: Clock; ids: IdSource; registry: RegistryPort`.

- [ ] **Step 1: Write the failing tests**

```python
import argparse

import pytest

from agent_core.composition.serve_ports import ServeConfigError, add_serve_parser, resolve_ports
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds

KEYS = {"AGENTCORE_KEYS_FINGERPRINT": "k1:" + "QQ" * 22 + "==",   # se sustituye abajo por claves válidas
        "AGENTCORE_KEYS_TOKEN_MAP": "k1:" + "Qg" * 22 + "=="}


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_serve_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["serve", *argv])


def _env(**extra: str) -> dict[str, str]:
    import base64
    def key(seed: bytes) -> str:
        return "k1:" + base64.b64encode(seed * 32).decode()
    base = {"AGENTCORE_KEYS_FINGERPRINT": key(b"a"), "AGENTCORE_KEYS_TOKEN_MAP": key(b"b"),
            "AGENTCORE_REGISTRY_DSN": "postgresql://ignored/ignored", **extra}
    return base


def _resolve(*argv: str, **env: str):  # type: ignore[no-untyped-def]
    return resolve_ports(_args(*argv), _env(**env).get, FakeClock(), FakeIds())


@pytest.mark.parametrize("value", [None, "0", "true", ""])
def test_without_the_demo_switch_every_missing_piece_is_named(value: str | None) -> None:
    extra = {} if value is None else {"AGENTCORE_ALLOW_DEMO": value}
    with pytest.raises(ServeConfigError) as info:
        _resolve(**extra)
    text = " ".join(info.value.problems)
    for option in ("--tools", "--authz", "--transcript", "--calibration", "--classifier",
                   "--field-classifier", "--grant-active"):
        assert option in text


def test_an_explicit_double_path_does_not_bypass_the_protection() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "testing.serve_demo:tools")
    assert "AGENTCORE_ALLOW_DEMO=1" in " ".join(info.value.problems)


def test_a_path_that_cannot_be_imported_is_a_clear_problem() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "no.existe:x", AGENTCORE_ALLOW_DEMO="1")
    assert "no.existe:x" in " ".join(info.value.problems)
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "sin-dos-puntos", AGENTCORE_ALLOW_DEMO="1")
    assert "modulo:atributo" in " ".join(info.value.problems)


def test_missing_dsn_and_missing_keys_are_reported_together() -> None:
    with pytest.raises(ServeConfigError) as info:
        resolve_ports(_args(), {}.get, FakeClock(), FakeIds())
    text = " ".join(info.value.problems)
    assert "--dsn" in text and "AGENTCORE_KEYS_FINGERPRINT" in text


def test_with_the_demo_switch_the_doubles_are_listed() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert set(ports.doubles) == {"tools", "authz", "transcript", "calibration", "classifier",
                                  "field-classifier", "grant-active", "identity"}
```

Nota: estos tests construyen `PostgresStore(dsn)` y `PgRegistryStore(...)` **sin conectar** (las conexiones se abren al usarlos). Si `PostgresRegistry` o `PgRegistryStore` conectan en el constructor, en el Step 3 se envuelven en una fábrica perezosa (`lambda: psycopg.connect(dsn, autocommit=False)`), que es como ya los usa `build_registry_service`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/composition/test_serve_ports.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.composition.serve_ports`.

- [ ] **Step 3: Write minimal implementation**

`testing/serve_demo.py` (dobles de la demo; solo datos sintéticos):

```python
"""Dobles de la demo de `agentcore serve` (solo con AGENTCORE_ALLOW_DEMO=1). Reemplazarlos es cambiar una ruta."""

from typing import Any

from agent_core.composition.serve_ports import DemoContext
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import ToolCallContext, ToolResult
from agent_core.views import FieldClassifier
from testing.engine_world import _HANDLERS, CATALOG, SyntheticAuthz, _radicar, demo_calibration
from testing.fakes.provider import ScriptedProvider
from testing.fakes.tools import FakeToolExecutor
from testing.fakes.transcript import InMemoryTranscript


class LazyDemoTools:
    """`FakeToolExecutor` que registra cada tool del registry la primera vez que se usa."""

    def __init__(self, ctx: DemoContext) -> None:
        self._inner = FakeToolExecutor(ctx.ids)
        self._registry = ctx.registry
        self._seen: set[EntityRef] = set()

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if tool not in self._seen:
            definition = self._registry.get(tool, ToolDef)
            if tool.id == "obtener_pqr":
                self._inner.register_readback(definition, of=EntityRef(id="radicar_pqr", version=tool.version))
            else:
                self._inner.register(definition, handler=_HANDLERS.get(tool.id, _radicar))
            self._seen.add(tool)
        return self._inner.execute(tool, args, bound_params, ctx, idempotency_key)

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._registry.get(tool, ToolDef)


def tools(ctx: DemoContext) -> LazyDemoTools:
    return LazyDemoTools(ctx)


def authz(ctx: DemoContext) -> SyntheticAuthz:
    return SyntheticAuthz()


def transcript(ctx: DemoContext) -> InMemoryTranscript:
    return InMemoryTranscript()


def calibration(ctx: DemoContext) -> InMemoryCalibrationSource:
    return InMemoryCalibrationSource({"cal-demo": demo_calibration()})


def classifier_provider(ctx: DemoContext) -> ScriptedProvider:
    return ScriptedProvider("classifier", clock=ctx.clock)


def field_classifier(ctx: DemoContext) -> FieldClassifier:
    return FieldClassifier(CATALOG)


def grant_active(ctx: DemoContext) -> Any:
    return lambda grant_ref, now: True  # demo: toda asignación firmada vale
```

Antes de escribir `LazyDemoTools`, verificar con `grep -n "def execute\|def definition\|def register_readback" testing/fakes/tools.py` que `FakeToolExecutor` tiene exactamente esos métodos y firmas; si `definition` no existe en el puerto `ToolExecutor`, quitar ese método del doble.

`agent_core/composition/serve_ports.py`:

```python
"""Puertos de `agentcore serve`: piezas reales por defecto; dobles por ruta solo con AGENTCORE_ALLOW_DEMO=1."""

import argparse
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from agent_core.adapters.env_keys import EnvKeyProvider, KeyConfigError
from agent_core.adapters.identity_keys import load_identity_verifier
from agent_core.adapters.llm import OpenAICompatGateway, load_endpoints
from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.decision import DecisionProvider
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.decision.providers.jev import JevProvider
from agent_core.decision.providers.jev_http import HttpJevTransport
from agent_core.domain import Release, SchemaError
from agent_core.ports import (
    AuditSink, AuthzPort, Clock, CostCounters, IdentityVerifier, IdSource, KeyProvider, LLMGateway,
    RegistryPort, ToolExecutor, TranscriptStore, UnitOfWorkFactory,
)
from agent_core.registry import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore
from agent_core.views import FieldClassifier

DEMO_ENV = "AGENTCORE_ALLOW_DEMO"
JEV_KEY_ENV = "AGENTCORE_JEV_API_KEY"
DSN_ENV = "AGENTCORE_REGISTRY_DSN"
DEMO_VERIFIER = "testing.registry_demo:demo_verifier"

# (opción, nombre de la pieza, ruta por defecto en demo)
_DOUBLES: tuple[tuple[str, str, str], ...] = (
    ("tools", "tools", "testing.serve_demo:tools"),
    ("authz", "authz", "testing.serve_demo:authz"),
    ("transcript", "transcript", "testing.serve_demo:transcript"),
    ("calibration", "calibration", "testing.serve_demo:calibration"),
    ("classifier", "classifier", "testing.serve_demo:classifier_provider"),
    ("field_classifier", "field-classifier", "testing.serve_demo:field_classifier"),
    ("grant_active", "grant-active", "testing.serve_demo:grant_active"),
)


class ServeConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class DemoContext:
    """Lo que reciben las fábricas de piezas (`modulo:atributo`) para construirse."""

    clock: Clock
    ids: IdSource
    registry: RegistryPort


@dataclass(frozen=True)
class ServePorts:
    clock: Clock
    ids: IdSource
    keys: KeyProvider
    uow_factory: UnitOfWorkFactory
    audit: AuditSink
    counters: CostCounters
    registry: RegistryPort
    releases: Callable[[str], Release]
    gateway: LLMGateway
    providers: Mapping[str, DecisionProvider]
    tools: ToolExecutor
    authz: AuthzPort
    transcript: TranscriptStore
    calibrations: CalibrationSource
    classifier: FieldClassifier | None
    verifier: IdentityVerifier
    doubles: tuple[str, ...]


def add_serve_parser(sub: Any) -> None:
    serve = sub.add_parser("serve", help="arranca el servidor HTTP (M9) con el motor compuesto")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--dsn", default=None, help=f"DSN de Postgres (o {DSN_ENV}); no se imprime nunca")
    serve.add_argument("--identity-keys", type=Path, default=None,
                       help="archivo con las claves públicas de identidad (principal y delegación)")
    for option, name, default in _DOUBLES:
        serve.add_argument(f"--{option.replace('_', '-')}", default=None,
                           help=f"{name} (modulo:atributo); en demo: {default}")


def _load(path: str) -> Any:
    module, sep, attr = path.partition(":")
    if not sep or not attr:
        raise ValueError(f"`{path}` no tiene la forma modulo:atributo")
    return getattr(importlib.import_module(module), attr)


def resolve_ports(args: argparse.Namespace, env: Callable[[str], str | None],
                  clock: Clock, ids: IdSource) -> ServePorts:
    problems: list[str] = []
    demo = env(DEMO_ENV) == "1"

    dsn = args.dsn or env(DSN_ENV)
    if not dsn:
        problems.append(f"falta --dsn (o {DSN_ENV})")
    try:
        keys: KeyProvider | None = EnvKeyProvider({k: v for k in ("AGENTCORE_KEYS_FINGERPRINT",
                                                                  "AGENTCORE_KEYS_TOKEN_MAP")
                                                   if (v := env(k))})
    except KeyConfigError as exc:
        keys = None
        problems.append(str(exc))

    chosen: dict[str, str] = {}
    for option, name, default in _DOUBLES:
        given = getattr(args, option)
        if not demo:
            problems.append(f"falta {name}: pasa --{option.replace('_', '-')} con una pieza real; "
                            f"los dobles de demo solo se usan con {DEMO_ENV}=1")
        else:
            chosen[name] = given or default

    if not dsn or keys is None or problems:
        raise ServeConfigError(problems)

    store = PostgresStore(dsn)
    pg_registry = PostgresRegistry(PgRegistryStore(lambda: psycopg.connect(dsn, autocommit=False)), clock)
    ctx = DemoContext(clock=clock, ids=ids, registry=pg_registry)
    built: dict[str, Any] = {}
    for name, path in chosen.items():
        try:
            built[name] = _load(path)(ctx)
        except (ImportError, AttributeError, ValueError) as exc:
            problems.append(f"no se pudo cargar {name} ({path}): {type(exc).__name__}: {exc}; "
                            "revisa la ruta modulo:atributo")
    if problems:
        raise ServeConfigError(problems)

    endpoints = load_endpoints(env_mapping := {k: v for k in ("LLM_ENDPOINTS",) if (v := env(k))})
    gateway = OpenAICompatGateway(pg_registry, endpoints, env_mapping)
    transport = HttpJevTransport(lambda: _jev_key(env), clock)
    providers: dict[str, DecisionProvider] = {"jev": JevProvider(transport), "classifier": built["classifier"]}
    doubles = list(built)
    if args.identity_keys is not None:
        try:
            verifier: IdentityVerifier = load_identity_verifier(args.identity_keys, built["grant-active"])
        except SchemaError as exc:
            raise ServeConfigError([str(exc)]) from None
    elif demo:
        verifier = _load(DEMO_VERIFIER)()
        doubles.append("identity")
    else:
        raise ServeConfigError(["falta --identity-keys (archivo de claves públicas de identidad)"])
    return ServePorts(
        clock=clock, ids=ids, keys=keys, uow_factory=store.uow, audit=store.audit(), counters=store.costs(),
        registry=pg_registry, releases=pg_registry.release, gateway=gateway, providers=providers,
        tools=built["tools"], authz=built["authz"], transcript=built["transcript"],
        calibrations=built["calibration"], classifier=built["field-classifier"], verifier=verifier,
        doubles=tuple(doubles))


def _jev_key(env: Callable[[str], str | None]) -> str:
    key = env(JEV_KEY_ENV)
    if not key:
        raise RuntimeError(f"falta {JEV_KEY_ENV}: no se puede llamar a JEV sin clave")
    return key
```

Verificar antes con `grep -n "^from\|^import" agent_core/ports/__init__.py` que los nombres importados de `agent_core.ports` existen (`CostCounters`, `LLMGateway`, `TranscriptStore`…) y ajustar el `import` a los que existan.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/composition/test_serve_ports.py -v`
Expected: PASS.

- [ ] **Step 5: Punto de control**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Si `lint-imports` se queja de que `composition` importa `testing`, no es así: `testing` solo se carga por `importlib` desde una ruta. Si se queja de otra frontera, añadir `agent_core.composition.serve_ports` al contrato que ya cubre `agent_core.composition`.

---

### Task 4: `build_api_deps` y turno completo por HTTP

**Files:**
- Create: `agent_core/composition/serve.py`
- Modify: `agent_core/composition/__init__.py`
- Test: `tests/composition/test_serve_app.py`

**Interfaces:**
- Consumes: `ServePorts`, `EngineDeps`, `build_engine`, `ApiDeps`, `create_app`, `registry_extension`, `build_registry_service`/`RegistryService`, `OtelSecurityLog`, `AuditLog`.
- Produces:
  ```python
  def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None) -> ApiDeps: ...
  ```

- [ ] **Step 1: Write the failing test**

El test construye `ServePorts` a mano con el mundo en memoria de `EngineWorld` (sin Postgres) y un verificador real de `TestIdentityIssuer`; no usa `resolve_ports`.

```python
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_ports import ServePorts
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from testing.fakes.storage import InMemoryCostCounters


def _ports(world: EngineWorld, issuer: TestIdentityIssuer) -> ServePorts:
    d = world.deps
    return ServePorts(
        clock=d.clock, ids=d.ids, keys=d.keys, uow_factory=d.uow_factory, audit=d.audit,
        counters=InMemoryCostCounters(world.store), registry=d.registry, releases=d.releases,
        gateway=d.gateway, providers=d.providers, tools=d.tools, authz=d.authz, transcript=d.transcript,
        calibrations=d.calibrations, classifier=d.classifier, verifier=issuer.verifier(), doubles=())


def test_a_full_turn_goes_through_http() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = TestClient(create_app(build_api_deps(_ports(world, issuer))), raise_server_exceptions=False)
    world.understands("start_flow", flow="disputa-cargo")
    auth = {"Authorization": f"Bearer {issuer.customer()}", "Idempotency-Key": "k-1"}
    created = client.post("/v1/runs", json={"agent": "atencion"}, headers=auth)
    assert created.status_code == 201, created.text
    session = created.json()["session_id"]
    turn = client.post(f"/v1/sessions/{session}/turns", json={"text": "no reconozco un cargo"},
                       headers={"Authorization": auth["Authorization"]})
    assert turn.status_code == 200, turn.text
    assert turn.json()["messages"]  # el motor real respondió


def test_the_run_transcript_is_readable_through_the_wired_reader() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = TestClient(create_app(build_api_deps(_ports(world, issuer))), raise_server_exceptions=False)
    auth = {"Authorization": f"Bearer {issuer.customer()}", "Idempotency-Key": "k-2"}
    run = client.post("/v1/runs", json={"agent": "atencion"}, headers=auth).json()
    got = client.get(f"/v1/runs/{run['run_id']}/transcript",
                     headers={"Authorization": auth["Authorization"]})
    assert got.status_code in (200, 403)  # 403 si TableAuthz no concede lectura; nunca 500/404
```

Nota: antes de fijar las aserciones sobre `session_id`, `messages` y `run_id`, correr `uv run python -c "…"` o leer `agent_core/api/schemas.py` (`publish_run`, `publish_turn`) y usar los nombres exactos de campo del JSON de respuesta; el resto del test no cambia. Si la semántica del agente "atencion" exige otro `RunInput`, copiar el cuerpo de `EngineWorld.start()` en `testing/engine_world.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/composition/test_serve_app.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.composition.serve`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Raíz de composición del servidor: `ServePorts` -> `ApiDeps` (motor real + API M9 + registry como extensión)."""

from agent_core.api.app import ApiDeps
from agent_core.api.security_log import OtelSecurityLog
from agent_core.audit import AuditLog
from agent_core.composition.engine import EngineDeps, build_engine
from agent_core.composition.serve_ports import ServePorts
from agent_core.registry import RegistryService, registry_extension


def build_api_deps(ports: ServePorts, *, registry_service: RegistryService | None = None) -> ApiDeps:
    built = build_engine(EngineDeps(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, uow_factory=ports.uow_factory, audit=ports.audit,
        registry=ports.registry, releases=ports.releases, tools=ports.tools, gateway=ports.gateway,
        providers=ports.providers, calibrations=ports.calibrations, transcript=ports.transcript,
        authz=ports.authz, classifier=ports.classifier))
    return ApiDeps(
        verifier=ports.verifier, authz=ports.authz, registry=ports.registry, uow_factory=ports.uow_factory,
        counters=ports.counters, clock=ports.clock, ids=ports.ids, turns=built.turns,
        handoffs=built.handoffs, transcripts=built.transcripts,
        denials=AuditLog(ports.audit, ports.uow_factory), security=OtelSecurityLog(),
        extensions=() if registry_service is None else (registry_extension(registry_service),))
```

Exportar `build_api_deps` en `agent_core/composition/__init__.py`. Verificar con `grep -n "registry_extension\|RegistryService" agent_core/registry/__init__.py` que ambos nombres se exportan; si no, importar desde `agent_core.registry.http`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/composition/test_serve_app.py -v`
Expected: PASS. Si `AuditLog(ports.audit, ports.uow_factory)` no coincide con la firma real, copiar la que usa `tests/integration/test_m9_postgres.py` (`AuditLog(pg.audit(), pg.uow)`); coincide.

- [ ] **Step 5: Test de clave de JEV ausente (Review Focus 4)**

Añadir a `tests/composition/test_serve_ports.py`:

```python
def test_jev_key_is_only_required_when_jev_is_called() -> None:
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")  # no lanza al construir
    with pytest.raises(RuntimeError, match="AGENTCORE_JEV_API_KEY"):
        ports.providers["jev"]._transport._api_key()  # type: ignore[attr-defined]
```

Run: `uv run pytest tests/composition -q`
Expected: PASS.

- [ ] **Step 6: Punto de control**

Run: `uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: sin errores.

---

### Task 5: Subcomando `serve`, `uvicorn` y documentación

**Files:**
- Modify: `agent_core/cli.py` (`main`, líneas ~309-316)
- Modify: `agent_core/composition/serve.py` (añadir `run_serve`)
- Modify: `pyproject.toml` (dependencia `uvicorn`)
- Modify: `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` (#13)
- Modify: `docs/specs/motor/m09-acceso-y-api.md` (§11 "Cableado real")
- Modify: `docs/specs/2026-09-29-registry-design.md` (§14)
- Modify: `docs/superpowers/specs/2026-09-30-servidor-arrancable-design.md` (§3.2/§3.4: claves por `EnvKeyProvider`, `AGENTCORE_JEV_API_KEY`, `--identity-keys`)
- Test: `tests/composition/test_serve_cli.py`

**Interfaces:**
- Consumes: `add_serve_parser`, `resolve_ports`, `ServeConfigError`, `build_api_deps`, `create_app`, `build_registry_service`.
- Produces:
  ```python
  def run_serve(args: argparse.Namespace, *, clock: Clock, ids: IdSource,
                env: Callable[[str], str | None],
                serve: Callable[..., None] | None = None) -> int: ...
  ```
  `serve` es `uvicorn.run` por defecto; se inyecta en pruebas.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from agent_core.cli import main


def test_serve_help_lists_the_piece_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["serve", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--host", "--port", "--dsn", "--identity-keys", "--tools", "--authz", "--transcript"):
        assert option in out


def test_serve_without_demo_and_without_pieces_exits_2_and_names_them(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTCORE_ALLOW_DEMO", raising=False)
    code = main(["serve", "--dsn", "postgresql://x/y"])
    err = capsys.readouterr().err
    assert code == 2 and "--tools" in err and "AGENTCORE_ALLOW_DEMO=1" in err
```

Y en `tests/composition/test_serve_app.py` un test de `run_serve` con `serve` inyectado:

```python
def test_run_serve_prints_the_doubles_and_starts_uvicorn(capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from agent_core.composition import serve as mod

    started: dict[str, object] = {}
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    monkeypatch.setattr(mod, "resolve_ports", lambda *a, **k: _ports_with_doubles(world, issuer))
    args = argparse.Namespace(host="127.0.0.1", port=8123)
    code = mod.run_serve(args, clock=world.clock, ids=world.ids, env={}.get,
                         serve=lambda app, **kw: started.update(kw))
    assert code == 0 and started == {"host": "127.0.0.1", "port": 8123}
    assert "tools" in capsys.readouterr().err
```

con `_ports_with_doubles` igual a `_ports` pero con `doubles=("tools",)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/composition/test_serve_cli.py tests/composition/test_serve_app.py -k "serve" -v`
Expected: FAIL (el subcomando `serve` no existe).

- [ ] **Step 3: Write minimal implementation**

En `agent_core/composition/serve.py` añadir:

```python
import argparse
import sys
from collections.abc import Callable

from agent_core.api.app import create_app
from agent_core.composition.serve_ports import ServeConfigError, resolve_ports
from agent_core.ports import Clock, IdSource


def run_serve(args: argparse.Namespace, *, clock: Clock, ids: IdSource,
              env: Callable[[str], str | None], serve: Callable[..., None] | None = None) -> int:
    try:
        ports = resolve_ports(args, env, clock, ids)
    except ServeConfigError as exc:
        print("agentcore serve no puede arrancar:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    if ports.doubles:
        print("AVISO: piezas que son DOBLES de demo (no producción): " + ", ".join(ports.doubles),
              file=sys.stderr)
    app = create_app(build_api_deps(ports))
    if serve is None:
        import uvicorn

        serve = uvicorn.run
    serve(app, host=args.host, port=args.port)
    return 0
```

En `agent_core/cli.py` (`main`), tras `add_registry_parser(sub)`:

```python
    from agent_core.composition.serve import run_serve
    from agent_core.composition.serve_ports import add_serve_parser

    add_serve_parser(sub)
```

y antes del `if args.command == "sweep"`:

```python
    if args.command == "serve":
        return run_serve(args, clock=SystemClock(), ids=SystemIds(), env=os.environ.get)
```

Actualizar el docstring del módulo (`…, \`registry\` y \`serve\``). En `pyproject.toml`, bajo `dependencies`, añadir `"uvicorn>=0.30",` (orden alfabético) y correr `uv lock`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/composition tests/m09 -q`
Expected: PASS.

- [ ] **Step 5: Documentación**

- `TEMAS-ABIERTOS-PENDIENTES.md`: en la tabla, #13 pasa a **"Resuelto salvo las piezas reales de las unidades 3, 6 y 7 (2026-09-30)"**; el encabezado de estado deja de listar #13 como abierto; la sección `## 13.` reemplaza su cuerpo por: decisión (modelo real, resto doble), enlace a la spec y a este plan, y la lista de piezas que siguen pendientes (ToolExecutor real, AuthzPort real, TranscriptStore persistente, calibración real, artefactos del classifier, servicio de identidad real con `grant_active`).
- `m09-acceso-y-api.md` §11: marcar "Cableado real" como resuelto (`agentcore serve`, `agent_core/composition/serve*.py`) y documentar el formato del archivo de claves de identidad.
- `registry-design.md` §14: anotar que `serve` monta el registry como extensión (`registry_extension`).
- Spec del servidor §3.2/§3.4: claves por `EnvKeyProvider`, `AGENTCORE_JEV_API_KEY`, `--identity-keys`, y `identity` entre los dobles en demo.

- [ ] **Step 6: Punto de control final**

Run: `uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo en verde.

Prueba manual de humo (con Postgres local: `docker compose up -d postgres`):

```bash
AGENTCORE_ALLOW_DEMO=1 AGENTCORE_KEYS_FINGERPRINT=… AGENTCORE_KEYS_TOKEN_MAP=… \
  AGENTCORE_REGISTRY_DSN=postgresql://… uv run agentcore serve --port 8000
```

Esperado: aviso con la lista de dobles en stderr y `curl -s localhost:8000/openapi.json` responde. (Las claves de ejemplo: `kid:base64` de 32 bytes; ver `agent_core/adapters/env_keys.py`.)

---

## Self-Review

- **Cobertura de la spec:** §3.1 fábrica → Task 4; §3.2 piezas reales → Task 3 (Postgres, gateway, JEV, claves, identidad) y Task 2; §3.3 archivo de claves → Task 2; §3.4 dobles con protección → Task 3; §3.5 lectores → Task 1; §3.6 comando y `uvicorn` → Task 5; §4 pruebas → Tasks 1-5; §6 documentación → Task 5. El aviso de alias de LLM sin configurar (gateway §5) **no** está cubierto: se añade como Task 6 si se quiere (el gateway expone los alias; requiere leer `OpenAICompatGateway` y los `model_profile` de la release). Lo dejo fuera de este plan y queda anotado como abierto en la spec §7.
- **Ambigüedad resuelta:** en la spec las claves de identidad eran un supuesto; el plan fija `--identity-keys` (YAML/JSON). Los nombres de opciones de §7 quedan fijados en Task 3.
- **Consistencia de tipos:** `ServePorts.doubles` (Task 3) lo usan `run_serve` (Task 5) y los tests de Task 4; `BuiltEngine` (Task 1) lo usa `build_api_deps` (Task 4); `DemoContext` vive en `serve_ports.py` (Task 3, punto 2).
- **Verificaciones que el ejecutor debe hacer con comando** (no son huecos: cada una trae el `grep` a correr): nombres exportados de `agent_core.ports` y `agent_core.registry`; campos JSON exactos de `publish_run`/`publish_turn`; métodos de `FakeToolExecutor`; y que `testing.registry_demo:demo_verifier` sigue existiendo (Task 3).
