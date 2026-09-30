# Registry (unidad 2, entrega) — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `agent_core.registry` en su alcance de entrega:
- versiones y releases inmutables en Postgres;
- propuestas con candidata en memoria;
- validación con M1;
- gate de evaluación por agente sobre escenarios en sandbox;
- aprobación humana y publicación transaccional;
- linaje;
- import/export YAML;
- API REST montada en M9 y CLI.

**Architecture:**
- **Persistencia:** `RegistryService` contiene la máquina de estados y habla con un `RegistryStore` transaccional (`InMemoryRegistryStore` para pruebas unitarias, `PgRegistryStore` en producción).
- **Candidata:** una propuesta se convierte en `Candidate` con `build_candidate`, que mezcla la release base con el borrador, sube en cascada las versiones de las entidades que referencian lo que cambió y fija la release con `pin_release` de M1. La candidata vive en memoria y se sirve al motor con un `SnapshotRegistry`.
- **Evaluación:** `ScenarioEvaluator` (en el registry) corre cada escenario k veces sobre candidata y base, a través de un `ScenarioHarness` que implementa `agent_core.composition` (el registry no puede importar el motor), con tools de un `SandboxPort`. La calificación y el gate son funciones puras.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3, FastAPI, PyYAML, pytest, mypy strict, ruff, import-linter. Sin dependencias nuevas.

**Spec:** `docs/specs/2026-09-29-registry-design.md` (**rev. 2**). Léela completa antes de empezar. Lee también:
- ADR 0017 y 0018 (con la enmienda del 2026-09-30) y ADR 0019;
- `docs/specs/motor/00-indice.md`;
- `CLAUDE.md` (reglas duras).

**Fuera de este plan:** el adaptador real del LLM gateway (otra sesión). El evaluador recibe cualquier `LLMGateway` inyectado; las pruebas y la demo usan dobles guionados.

## Decisiones de implementación (tomadas al explorar el código; la Task 1 las lleva a la spec)

1. **Actor humano:** M0 no tiene tipo `agent` (ADR 0006/0019). Un principal es humano solo si `principal.attrs.get("actor") == "human"` y `principal.id` no es nulo. Roles: las cadenas `"constructor"` y `"aprobador"` en `Principal.roles`.
2. **Sandbox sin tocar M3:** el motor ya recibe un `ToolExecutor` (`EngineDeps.tools`). `SandboxPort.tools(handle)` devuelve un `ToolExecutor` con el atributo `is_sandbox = True`, y el evaluador exige ese atributo (T-REG-24). Se elimina `ActionsConfig` de la spec.
3. **Evaluador partido en dos:** `ScenarioEvaluator` y `ScenarioHarness` (protocolo) viven en `agent_core.registry.evaluation`. `EngineScenarioHarness` (compone `build_turn_engine`) vive en `agent_core.composition`, que es la raíz de composición y puede importar todo. El registry nunca importa `composition`.
4. **Cascada de versiones:** las entidades publicadas guardan referencias exactas (M0), así que un `(kind, id, version)` publicado tiene un único contenido. Si un borrador cambia `p/x` 1.0.0 → 1.1.0, el flow que lo referencia debe apuntar a 1.1.0 y por lo tanto necesita versión nueva. `build_candidate` sube **patch** en cascada (flow, luego agente) con `VersionDocs` generadas, y las lista en `Candidate.auto_bumped`.
5. **IDs:** `IdKind` (M0) gana `proposal` y `eval_run`. Es un cambio aditivo: `SCHEMA_VERSION` pasa de 0.4.0 a 0.5.0 y se regenera `contracts/`. `release_id = "rel-" + candidate_hash[:16]`.
6. **API:** M9 no puede importar el registry, ni el registry a M9. `ApiDeps` gana `extensions: tuple[ApiExtension, ...]`; cada extensión recibe la `FastAPI` y un `authenticate(request, authorization) -> Principal` que aplica los chequeos 1 a 5 de M9. El router y el manejador de errores del registry viven en `agent_core/registry/http.py`.
7. **M1:** `flows/__init__.py` exporta además `entity_ref_sites`, `RefSite` y `kind_of`.
8. **`EvalPort.run(suite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport`.** Un `EvalTarget` es una release más el `RegistryPort` que la sirve.
9. **Escenarios:** `steps` con operaciones `start`, `turn` y `confirm` (las del `Driver` de `testing/engine_world.py`) en lugar de `turns`. El `seed` del sandbox guioniza las respuestas de cada tool.
10. **Guardarraíles**, contados sobre los eventos del motor:
    - `unverified_writes`: acciones con `action_dispatched` sin `action_verified(result="verified")`;
    - `unsupported_success`: runs cerrados `resolved` con alguna escritura sin verificar;
    - `sensitive_leaks`: apariciones de `scenario.sensitive_values` en los eventos serializados.
11. **Infraestructura caída:** el motor absorbe los `GatewayError` (degrada o escala). Por eso el harness envuelve el gateway en una sonda y, si hubo alguno, lanza `HarnessUnavailable`, que se convierte en `failed_infra`.
12. **CLI:** el verificador de identidad y el harness de evaluación se cargan por ruta de import (`módulo:atributo`), como hace el replay con `testing.replay`. Por defecto: `testing.registry_demo:demo_verifier` y `testing.registry_demo:build_harness` (claves y dobles de PRUEBA).
13. **Linaje:** `RunReleaseReader.release_of(run_id) -> str | None`. Lo implementa `composition` sobre `UnitOfWorkFactory.load_run`.

## Global Constraints

- Python `>=3.12,<3.13`. **Sin dependencias nuevas** (`pyproject.toml` no cambia).
- **Fronteras (`.importlinter`):**
  - `agent_core.registry` solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de `agent_core.flows` (`from agent_core.flows import ...`, nunca submódulos).
  - `agent_core.api` no importa `agent_core.registry`.
  - `agent_core` nunca importa `testing` ni `tests`.
- **Tiempo e IDs:** nunca `datetime.now()`, `time.*`, `uuid4()`, `random` ni `secrets`. Todo instante sale del `Clock` y todo ID del `IdSource`.
- **Cifras:** métricas y umbrales en `Decimal`, nunca `float`. JSON: `agent_core.domain.dumps`/`loads`. Hashes: `sha256_hex(canonical_bytes(x))`.
- **Datos sintéticos** en fixtures, suites y seeds. Nunca datos del dataset ni credenciales de AWS.
- **Mensajes para personas** (violaciones, `detail` de errores): en español, sin valores de campos de clientes.
- **Por cada tarea:** `uv run pytest tests/registry` (y la carpeta que toque), `uv run lint-imports`, `uv run mypy`, `uv run ruff check .` en verde antes del commit.
- **Commits:** mensaje convencional en español y la línea final `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Borrador que cambia una entidad referenciada por varias ramas** (un template usado por dos flows): ambos flows y el agente deben subir de versión una sola vez, sin bucles infinitos. Prueba en la Task 3 (`test_cascade_bumps_every_dependent_once`).
2. **Evaluar dos veces seguidas la misma candidata** (reintento tras `failed_infra`): el segundo `evaluate` debe funcionar y el `approve` tiene que usar la **última** evaluación de ese hash. Prueba en la Task 9 (`test_retry_after_failed_infra_uses_latest_run`).
3. **Publicar dos veces con la misma `Idempotency-Key`** después de que la primera ya movió `staging`: tiene que devolver la misma release y no fallar con `proposal_stale`. Prueba en la Task 9 (`test_publish_retry_with_same_key_is_idempotent_after_success`).
4. **Borrador con YAML o JSON inválido o con un tipo desconocido** (lo escribe un agente): `validation_failed` con un mensaje legible, nunca un 500. Prueba en la Task 4 (`test_unknown_kind_and_bad_schema_are_violations`).
5. **Revocar la release a la que apunta `staging` mientras hay una propuesta basada en ella:** la propuesta sigue siendo publicable (su base es el alias, no el estado de la release), y la release revocada no se sirve por `PostgresRegistry` a runs nuevos. Prueba en la Task 13 (`test_revoked_release_not_resolved_for_new_runs`).

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `agent_core/registry/__init__.py` | interfaz pública del paquete |
| `agent_core/registry/errors.py` | `RegistryErrorCode`, `RegistryError`, `IntegrityError`, `HTTP_STATUS` |
| `agent_core/registry/models.py` | `VersionRef`, `VersionDocs`, `EntityDraft`, `Proposal`, `StoredVersion`, `StoredRelease`, `Approval`, `EvalRun`, `AliasChange`, `RegistryEvent`, vistas de lectura |
| `agent_core/registry/suite.py` | formato de `EvalSuite` (escenarios, pasos, `seed`, `expect`) |
| `agent_core/registry/entities.py` | `SUITE_KIND`, `AnyEntity`, `entity_kind`, `encode_entity`, `decode_entity`, `version_ref` |
| `agent_core/registry/candidate.py` | `Candidate`, `CandidateError`, `build_candidate` (cascada, pin y hashes) |
| `agent_core/registry/validation.py` | `Limits`, `validate_candidate` |
| `agent_core/registry/roles.py` | `is_human`, `require_constructor`, `require_approver`, `actor_id` |
| `agent_core/registry/blobs.py` | `BlobStore`, `InMemoryBlobStore` |
| `agent_core/registry/store.py` | protocolos `RegistryStore` y `RegistryTx` |
| `agent_core/registry/memory.py` | `InMemoryRegistryStore` (transacciones con rollback) |
| `agent_core/registry/snapshot.py` | `SnapshotRegistry` (`RegistryPort` en memoria) |
| `agent_core/registry/evaluation/report.py` | `Verdict`, `MetricCheck`, `SuiteMetrics`, `RunScore`, `ScenarioResult`, `JudgeNote`, `EvalReport` |
| `agent_core/registry/evaluation/scoring.py` | `score_run`, `aggregate` |
| `agent_core/registry/evaluation/gate.py` | `decide` |
| `agent_core/registry/evaluation/ports.py` | `EvalTarget`, `EvalPort`, `ScenarioHarness`, `HarnessUnavailable`, `SandboxPort`, `SandboxHandle`, `Judge` |
| `agent_core/registry/evaluation/evaluator.py` | `ScenarioEvaluator` |
| `agent_core/registry/evaluation/local_sandbox.py` | `LocalSandbox` |
| `agent_core/registry/service.py` | `RegistryService`, `RunReleaseReader`, `ValidationReport` |
| `agent_core/registry/yaml_io.py` | `import_directory`, `export_entities` |
| `agent_core/registry/postgres/schema.sql` | esquema, permisos y triggers |
| `agent_core/registry/postgres/store.py` | `apply_registry_schema`, `PgRegistryStore` |
| `agent_core/registry/postgres/runtime.py` | `PostgresRegistry` (`RegistryPort`) |
| `agent_core/registry/http.py` | `registry_extension` (router y errores) |
| `agent_core/composition/evaluation.py` | `EngineScenarioHarness`, `EvalStorage` |
| `agent_core/composition/registry.py` | `UowRunReleases`, `build_registry_service`, `run_registry_cli` |
| `agent_core/api/app.py` | `ApiDeps.extensions` |
| `agent_core/cli.py` | subcomando `registry` |
| `testing/registry_demo.py` | verificador, credenciales y harness de PRUEBA para la demo |
| `tests/registry/…` | pruebas unitarias |
| `tests/integration/test_registry_postgres.py` | pruebas con Postgres y E2E |

---

### Task 1: Base: spec sincronizada, cambios de M0/M1, frontera y errores

**Files:**
- Modify: `docs/specs/2026-09-29-registry-design.md` (decisiones 2, 3, 5, 6, 8, 9, 10, 11 y 12 de este plan)
- Modify: `agent_core/ports/ids.py` (`IdKind.proposal`, `IdKind.eval_run`)
- Modify: `agent_core/domain/version.py` (`SCHEMA_VERSION = "0.5.0"`)
- Modify: `agent_core/flows/__init__.py` (exporta `entity_ref_sites`, `RefSite`, `kind_of`)
- Modify: `.importlinter` (sin cambios en el contrato `registry`; solo verificar), `pyproject.toml` no cambia
- Create: `agent_core/registry/errors.py`
- Test: `tests/registry/__init__.py`, `tests/registry/test_errors.py`, `tests/m01/test_public_exports.py`

**Interfaces:**
- Produces:
  - `RegistryErrorCode` (StrEnum): `validation_failed`, `gate_failed`, `proposal_stale`, `candidate_changed`, `illegal_transition`, `forbidden_role`, `integrity_error`, `not_found`.
  - `HTTP_STATUS: Mapping[RegistryErrorCode, int]`.
  - `RegistryError(code, detail="", payload=None)` con `.code`, `.detail`, `.payload: JsonValue`.
  - `IntegrityError(RegistryError)`.
  - `IdKind.proposal`, `IdKind.eval_run`.
  - `from agent_core.flows import entity_ref_sites, RefSite, kind_of`.

- [ ] **Step 1: Actualizar la spec.** Edita `docs/specs/2026-09-29-registry-design.md`:
  - §6.1: reemplaza `principal`, `seed` y `turns` del ejemplo por:
    ```yaml
    scenarios:
      - id: disputa-cargo-duplicado
        principal: {id: cust-001, attrs: {country: CO}}   # sintético; tipo customer
        steps:
          - {op: start}
          - {op: turn, text: "no reconozco un cargo de ciento veinte dólares"}
          - {op: confirm, answer: "yes"}
        seed:
          tools:
            buscar_transacciones: [{status: ok, result: [{transaction_id: tx-1, amount: "120.50"}]}]
        sensitive_values: ["4111-1111"]
        expect: {outcome: resolved, actions_verified: [radicar_pqr], escalated: false}
    ```
  - §6.2 paso 1: "compone el motor a través de un `ScenarioHarness` (protocolo del paquete) que implementa `agent_core.composition`".
  - §6.3: `SandboxPort.tools(handle) -> ToolExecutor` con atributo `is_sandbox = True`. La barrera exige ese atributo. `provision(seed, target)`.
  - §6.2 paso 6: "una falla del gateway durante una corrida (la detecta una sonda del harness, porque el motor la absorbe) o del sandbox → `failed_infra`".
  - §7.3: `EvalPort.run(suite, candidate: EvalTarget, base: EvalTarget | None)`.
  - §3.4: agrega el párrafo "**Cascada:** si una entidad que cambia es referenciada con versión exacta por otra de la base, esa otra sube de patch automáticamente (repetido hasta el punto fijo), con `VersionDocs` generadas. `freeze` las devuelve en `Candidate.auto_bumped`."
  - §15: reemplaza la línea de M3 por "**M3:** sin cambios (el sandbox entra como `ToolExecutor`)". Agrega "**M0:** `IdKind.proposal` y `IdKind.eval_run` (`SCHEMA_VERSION` 0.5.0)". Cambia la línea de M9 por "`ApiDeps.extensions`: cada extensión recibe la app y un `authenticate(request, authorization)`". Agrega "**M1:** exporta `entity_ref_sites`, `RefSite` y `kind_of`".
  - §3.4: "`release_id = rel-` + los primeros 16 caracteres de `candidate_hash`".

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/registry/__init__.py`: vacío.

`tests/registry/test_errors.py`:
```python
from agent_core.registry.errors import HTTP_STATUS, IntegrityError, RegistryError, RegistryErrorCode


def test_every_code_has_http_status() -> None:
    assert set(HTTP_STATUS) == set(RegistryErrorCode)
    assert HTTP_STATUS[RegistryErrorCode.validation_failed] == 422
    assert HTTP_STATUS[RegistryErrorCode.forbidden_role] == 403
    assert HTTP_STATUS[RegistryErrorCode.integrity_error] == 500
    assert HTTP_STATUS[RegistryErrorCode.not_found] == 404
    for code in ("gate_failed", "proposal_stale", "candidate_changed", "illegal_transition"):
        assert HTTP_STATUS[RegistryErrorCode(code)] == 409


def test_error_carries_code_detail_and_payload() -> None:
    err = RegistryError(RegistryErrorCode.gate_failed, "no pasa", payload={"x": 1})
    assert (err.code, err.detail, err.payload) == (RegistryErrorCode.gate_failed, "no pasa", {"x": 1})
    assert IntegrityError("blob").code is RegistryErrorCode.integrity_error
```

`tests/m01/test_public_exports.py`:
```python
def test_registry_needs_are_exported() -> None:
    from agent_core.flows import RefSite, entity_ref_sites, kind_of  # noqa: F401


def test_idkind_has_registry_kinds() -> None:
    from agent_core.ports import IdKind
    assert IdKind("proposal") and IdKind("eval_run")
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_errors.py tests/m01/test_public_exports.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.registry.errors`, `ImportError: cannot import name 'RefSite'` y `ValueError: 'proposal' is not a valid IdKind`.

- [ ] **Step 4: Implementar**

`agent_core/registry/errors.py`:
```python
"""Errores tipados del registry (spec §7.4). No se añaden a `ProblemCode` de M0."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from agent_core.domain import JsonValue


class RegistryErrorCode(StrEnum):
    validation_failed = "validation_failed"
    gate_failed = "gate_failed"
    proposal_stale = "proposal_stale"
    candidate_changed = "candidate_changed"
    illegal_transition = "illegal_transition"
    forbidden_role = "forbidden_role"
    integrity_error = "integrity_error"
    not_found = "not_found"


HTTP_STATUS: Mapping[RegistryErrorCode, int] = MappingProxyType({
    RegistryErrorCode.validation_failed: 422,
    RegistryErrorCode.gate_failed: 409,
    RegistryErrorCode.proposal_stale: 409,
    RegistryErrorCode.candidate_changed: 409,
    RegistryErrorCode.illegal_transition: 409,
    RegistryErrorCode.forbidden_role: 403,
    RegistryErrorCode.integrity_error: 500,
    RegistryErrorCode.not_found: 404,
})


class RegistryError(Exception):
    """`detail` es texto para personas, sin datos de clientes. `payload` viaja en el cuerpo del error."""

    def __init__(self, code: RegistryErrorCode, detail: str = "", payload: JsonValue = None) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.payload = payload


class IntegrityError(RegistryError):
    """El contenido leído no coincide con su hash: nunca se sirve."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(RegistryErrorCode.integrity_error, detail)
```

`agent_core/ports/ids.py`: agrega al final de `IdKind`:
```python
    proposal = "proposal"
    eval_run = "eval_run"
```

`agent_core/domain/version.py`: `SCHEMA_VERSION = "0.5.0"`.

`agent_core/flows/__init__.py`: agrega los imports y `__all__`:
```python
from agent_core.flows.refs import RefSite, entity_ref_sites
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl, kind_of, load_registry
```
y en `__all__`: `"RefSite"`, `"entity_ref_sites"`, `"kind_of"` (en orden alfabético con el resto).

Revisa `testing/fakes/ids.py`: si `FakeIds` tiene un mapa de prefijos por `IdKind`, agrega `proposal: "prop"` y `eval_run: "eval"`.

- [ ] **Step 5: Regenerar contratos y correr todo**

Run: `uv run agentcore contracts && uv run pytest tests/registry tests/m01 tests/m00 tests/contracts -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: PASS. `git diff --stat contracts/` muestra el cambio de `IdKind` y de la versión.

- [ ] **Step 6: Commit**
```bash
git add docs/specs/2026-09-29-registry-design.md agent_core/ports/ids.py agent_core/domain/version.py agent_core/flows/__init__.py agent_core/registry/errors.py testing/fakes/ids.py contracts tests/registry tests/m01/test_public_exports.py
git commit -m "feat(registry): errores tipados, IdKind de propuestas y exports de M1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Modelos, formato de suite y codificación de entidades

**Files:**
- Create: `agent_core/registry/models.py`, `agent_core/registry/suite.py`, `agent_core/registry/entities.py`, `agent_core/registry/evaluation/__init__.py` (vacío por ahora), `agent_core/registry/evaluation/report.py`
- Test: `tests/registry/helpers.py`, `tests/registry/test_models.py`, `tests/registry/test_entities.py`

**Interfaces:**
- Consumes: `RegistryError` (Task 1).
- Produces:
  - `models.RegModel` (base Pydantic `frozen`, `extra="forbid"`).
  - `VersionRef(kind: str, id: str, version: str)`, hashable, con `str()` → `"kind:id@version"`.
  - `VersionDocs(description, rationale, changelog)`.
  - `Origin` (`manual`, `builder_chat`, `auto_detect`, `import_`), con valor `"import"` para `import_`.
  - `ProposalState` (`draft`, `candidate`, `evaluated`, `approved`, `published`).
  - `EntityDraft(kind: str, content: dict[str, JsonValue], docs: VersionDocs)` con propiedades `.id`, `.version`.
  - `Proposal(proposal_id, agent_id, origin, state, rev: int, base_release_id: str | None, title, created_by, candidate_hash: str | None, updated_at: datetime)`.
  - `StoredVersion(ref: VersionRef, content_hash, docs, proposal_id: str | None, created_by, created_at)`.
  - `StoredRelease(release: Release, release_hash, agent_id, agent_version, base_release_id: str | None, proposal_id: str | None, published_by, published_at)`.
  - `Approval(proposal_id, candidate_hash, actor, decision: Literal["approved","rejected"], reason: str | None, at)`.
  - `EvalRun(eval_run_id, proposal_id, candidate_hash, base_release_id: str | None, suite: VersionRef, verdict: Verdict, report: EvalReport, at)`.
  - `AliasChange(agent_id, alias, before: str | None, after: str, actor, reason, at)`.
  - `RegistryEvent(type, actor, principal_type: str, origin: str | None, proposal_id: str | None, candidate_hash: str | None, release_id: str | None, at)`.
  - `EntityInRelease(ref, content_hash, docs, changed_vs_base: bool)`.
  - `ReleaseDetail(release_id, status, agent_id, entities: list[EntityInRelease], knowledge_snapshot: str | None, proposal_id, base_release_id, published_by, published_at)`.
  - `ChangedRef(before: VersionRef, after: VersionRef, docs: VersionDocs)`.
  - `ReleaseDiff(a, b, added: list[VersionRef], removed: list[VersionRef], changed: list[ChangedRef])`.
  - `RunLineage(run_id, release_id, entities, knowledge_snapshot, proposal_id, eval_verdict: Verdict | None, built_by: str | None, approved_by: str | None, published_at)`.
  - `VersionSummary(ref, content_hash, docs, created_by, created_at)`.
  - `EntityVersion(ref, content: dict[str, JsonValue], content_hash, docs, created_by, created_at)`.
  - `suite.Step`, `ToolReply`, `SandboxSeed`, `Expect`, `ScenarioPrincipal`, `Scenario`, `EvalSuite`.
  - `entities.SUITE_KIND = "eval_suite"`, `AnyEntity = RegistryEntity | EvalSuite`, `entity_kind(e) -> str`, `version_ref(e) -> VersionRef`, `encode_entity(e) -> bytes`, `decode_entity(kind, data) -> AnyEntity`, `model_for(kind) -> type`, `content_hash(e) -> str`.
  - `report.Verdict = Literal["pass","fail","failed_infra"]`, `MetricCheck`, `SuiteMetrics`, `RunScore`, `ScenarioResult`, `JudgeNote`, `EvalReport`.
  - `tests/registry/helpers.py`: `REGISTRY_DEMO`, `demo_pinned()`, `docs()`, `human()`, `bot()`, `prompt_draft()`, `suite_draft()`.

- [ ] **Step 1: Escribir los helpers de prueba**

`tests/registry/helpers.py`:
```python
"""Datos sintéticos compartidos por las pruebas del registry."""

from pathlib import Path
from typing import Any

from agent_core.domain import Principal
from agent_core.flows import PinnedRelease, load_registry, pin_release
from agent_core.registry.models import EntityDraft, VersionDocs
from testing.builders import principal

REGISTRY_DEMO = Path(__file__).parents[1] / "fixtures" / "registry-demo"
AGENT = "atencion"


def demo_pinned() -> PinnedRelease:
    reg, violations = load_registry(REGISTRY_DEMO)
    assert not violations
    return pin_release(reg, "demo")


def docs(text: str = "cambio de prueba") -> VersionDocs:
    return VersionDocs(description=text, rationale="mejorar la resolución", changelog=text)


def human(*roles: str, pid: str = "ana") -> Principal:
    return principal(type="builder", id=pid, roles=list(roles or ("constructor", "aprobador")),
                     attrs={"actor": "human"})


def bot(*roles: str) -> Principal:
    return principal(type="builder", id="constructor-bot", roles=list(roles or ("constructor",)), attrs={})


def prompt_draft(version: str = "1.1.0", text: str = "Confirma en una frase que la disputa quedó radicada.",
                 **over: Any) -> EntityDraft:
    content: dict[str, Any] = {
        "id": "p/resumen_radicado", "version": version,
        "locales": {"es": text, "pt": "Confirme em uma frase que a contestação foi registrada."},
        "model_profile": "perfil-generacion@1.0.0", **over}
    return EntityDraft(kind="prompt", content=content, docs=docs())


def suite_content(version: str = "1.0.0", **over: Any) -> dict[str, Any]:
    return {"id": "disputas-suite", "version": version, "agent_id": AGENT, "repetitions": 2,
            "noise_margin": "0.05", "floor": "0.5",
            "scenarios": [{"id": "resuelto", "principal": {"id": "cust-001"},
                           "steps": [{"op": "start"}, {"op": "turn", "text": "no reconozco un cargo"},
                                     {"op": "confirm", "answer": "yes"}],
                           "expect": {"outcome": "resolved", "actions_verified": ["radicar_pqr"],
                                      "escalated": False}}], **over}


def suite_draft(version: str = "1.0.0", **over: Any) -> EntityDraft:
    return EntityDraft(kind="eval_suite", content=suite_content(version, **over), docs=docs("suite"))
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/registry/test_models.py`:
```python
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent_core.registry.models import EntityDraft, VersionDocs, VersionRef
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import docs, suite_content


def test_version_ref_is_hashable_and_printable() -> None:
    ref = VersionRef(kind="prompt", id="p/x", version="1.0.0")
    assert str(ref) == "prompt:p/x@1.0.0"
    assert {ref: 1}[VersionRef(kind="prompt", id="p/x", version="1.0.0")] == 1


def test_draft_exposes_id_and_version() -> None:
    d = EntityDraft(kind="template", content={"id": "t/x", "version": "1.2.0"}, docs=docs())
    assert (d.id, d.version) == ("t/x", "1.2.0")


def test_draft_without_id_or_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityDraft(kind="template", content={"id": "t/x"}, docs=docs())


def test_docs_require_description() -> None:
    with pytest.raises(ValidationError):
        VersionDocs(description="", rationale="r", changelog="c")


def test_suite_parses_with_decimal_thresholds() -> None:
    suite = EvalSuite.model_validate(suite_content())
    assert suite.noise_margin == Decimal("0.05") and suite.floor == Decimal("0.5")
    assert suite.scenarios[0].steps[0].op == "start"


@pytest.mark.parametrize("over", [
    {"scenarios": []},
    {"repetitions": 0},
    {"noise_margin": "1.5"},
    {"scenarios": [suite_content()["scenarios"][0], suite_content()["scenarios"][0]]},  # ids repetidos
])
def test_suite_rejects_bad_shapes(over: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvalSuite.model_validate(suite_content(**over))


def test_scenario_must_start_with_start_step() -> None:
    bad = suite_content()
    bad["scenarios"][0]["steps"] = [{"op": "turn", "text": "hola"}]
    with pytest.raises(ValidationError):
        EvalSuite.model_validate(bad)
```

`tests/registry/test_entities.py`:
```python
import pytest

from agent_core.domain import Prompt
from agent_core.registry.entities import (
    SUITE_KIND,
    content_hash,
    decode_entity,
    encode_entity,
    entity_kind,
    version_ref,
)
from agent_core.registry.errors import IntegrityError, RegistryError
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import demo_pinned, suite_content


def test_every_demo_entity_round_trips_with_same_hash() -> None:
    for entity in demo_pinned().entities:
        data = encode_entity(entity)
        again = decode_entity(entity_kind(entity), data)
        assert again == entity
        assert content_hash(again) == content_hash(entity)


def test_suite_is_an_entity_of_its_own_kind() -> None:
    suite = EvalSuite.model_validate(suite_content())
    assert entity_kind(suite) == SUITE_KIND
    assert str(version_ref(suite)) == "eval_suite:disputas-suite@1.0.0"
    assert decode_entity(SUITE_KIND, encode_entity(suite)) == suite


def test_decode_unknown_kind_fails() -> None:
    with pytest.raises(RegistryError):
        decode_entity("nope", b"{}")


def test_decode_garbage_is_integrity_error() -> None:
    with pytest.raises(IntegrityError):
        decode_entity("prompt", b"{not json")


def test_prompt_kind_name() -> None:
    prompt = next(e for e in demo_pinned().entities if isinstance(e, Prompt))
    assert entity_kind(prompt) == "prompt"
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_models.py tests/registry/test_entities.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.registry.models'`.

- [ ] **Step 4: Implementar**

`agent_core/registry/evaluation/__init__.py`:
```python
"""Evaluación por agente sobre escenarios (spec §6). La interfaz pública se completa en la Task 12."""
```

`agent_core/registry/evaluation/report.py`:
```python
"""Resultado de una evaluación (spec §6.4). Cifras en `Decimal`."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["pass", "fail", "failed_infra"]
Label = Literal["candidate", "base"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricCheck(_M):
    name: str
    value: Decimal
    base: Decimal | None = None
    threshold: Decimal | None = None
    passed: bool


class SuiteMetrics(_M):
    primary: Decimal
    guardrails: dict[str, int]
    runs: int


class RunScore(_M):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    guardrails: dict[str, int]
    outcome: str | None = None
    escalated: bool = False


class ScenarioResult(_M):
    scenario_id: str
    label: Label
    repetition: int
    score: RunScore


class JudgeNote(_M):
    scenario_id: str
    label: Label
    note: str
    score: Decimal | None = None


class EvalReport(_M):
    verdict: Verdict
    checks: list[MetricCheck] = Field(default_factory=list)
    candidate: SuiteMetrics | None = None
    base: SuiteMetrics | None = None
    results: list[ScenarioResult] = Field(default_factory=list)
    judge_notes: list[JudgeNote] = Field(default_factory=list)
    detail: str | None = None
```

`agent_core/registry/suite.py`:
```python
"""Formato de `eval_suite` (spec §6.1): escenarios sintéticos ligados a un agente."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from agent_core.domain import EntityId, ExactVersion, JsonValue, Outcome, ToolStatus

Fraction = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_M):
    op: Literal["start", "turn", "confirm"]
    text: str | None = Field(default=None, max_length=4000)
    answer: Literal["yes", "no"] | None = None
    lang: str | None = None
    auth: Literal["anonymous", "session", "step_up"] = "step_up"

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if self.op == "turn" and not self.text:
            raise ValueError("un paso `turn` necesita `text`")
        if self.op == "confirm" and self.answer is None:
            raise ValueError("un paso `confirm` necesita `answer`")
        return self


class ToolReply(_M):
    status: ToolStatus = ToolStatus("ok")
    result: JsonValue = None
    error: str | None = None


class SandboxSeed(_M):
    tools: dict[str, list[ToolReply]] = Field(default_factory=dict)


class Expect(_M):
    outcome: Outcome | None = None
    actions_verified: list[str] = Field(default_factory=list)
    escalated: bool | None = None


class ScenarioPrincipal(_M):
    id: str = Field(min_length=1)
    attrs: dict[str, str] = Field(default_factory=dict)


class Scenario(_M):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    principal: ScenarioPrincipal
    steps: list[Step] = Field(min_length=1, max_length=50)
    seed: SandboxSeed = Field(default_factory=SandboxSeed)
    sensitive_values: list[str] = Field(default_factory=list)
    expect: Expect

    @model_validator(mode="after")
    def _starts(self) -> "Scenario":
        if self.steps[0].op != "start" or any(s.op == "start" for s in self.steps[1:]):
            raise ValueError("un escenario empieza con un único paso `start`")
        return self


class EvalSuite(_M):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    repetitions: PositiveInt = Field(default=3, le=10)
    noise_margin: Fraction = Decimal("0.05")
    floor: Fraction = Decimal("0.7")
    scenarios: list[Scenario] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalSuite":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de escenario repetidos")
        return self
```
> Si `EntityId`, `ExactVersion`, `Outcome` o `ToolStatus` no se exportan desde `agent_core.domain`, impórtalos desde el `__init__` que sí los exporte (`agent_core.ports` exporta `ToolStatus`). No importes submódulos de `domain`.

`agent_core/registry/models.py`:
```python
"""Modelos del registry (spec §3, §7, §9). Solo datos."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core.domain import JsonValue, Release
from agent_core.registry.evaluation.report import EvalReport, Verdict


class RegModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionRef(RegModel):
    kind: str
    id: str
    version: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}@{self.version}"


class VersionDocs(RegModel):
    description: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(max_length=4000)
    changelog: str = Field(max_length=8000)


class Origin(StrEnum):
    manual = "manual"
    builder_chat = "builder_chat"
    auto_detect = "auto_detect"
    import_ = "import"


class ProposalState(StrEnum):
    draft = "draft"
    candidate = "candidate"
    evaluated = "evaluated"
    approved = "approved"
    published = "published"


class EntityDraft(RegModel):
    kind: str
    content: dict[str, JsonValue]
    docs: VersionDocs

    @model_validator(mode="after")
    def _has_identity(self) -> "EntityDraft":
        if not isinstance(self.content.get("id"), str) or not isinstance(self.content.get("version"), str):
            raise ValueError("el contenido necesita `id` y `version` como texto")
        return self

    @property
    def id(self) -> str:
        return str(self.content["id"])

    @property
    def version(self) -> str:
        return str(self.content["version"])


class Proposal(RegModel):
    proposal_id: str
    agent_id: str
    origin: Origin
    state: ProposalState
    rev: int = 0
    base_release_id: str | None
    title: str = Field(min_length=1, max_length=200)
    created_by: str
    candidate_hash: str | None = None
    updated_at: datetime


class StoredVersion(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    proposal_id: str | None
    created_by: str
    created_at: datetime


class StoredRelease(RegModel):
    release: Release
    release_hash: str
    agent_id: str
    agent_version: str
    base_release_id: str | None
    proposal_id: str | None
    published_by: str
    published_at: datetime


class Approval(RegModel):
    proposal_id: str
    candidate_hash: str
    actor: str
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    at: datetime


class EvalRun(RegModel):
    eval_run_id: str
    proposal_id: str
    candidate_hash: str
    base_release_id: str | None
    suite: VersionRef
    verdict: Verdict
    report: EvalReport
    at: datetime


class AliasChange(RegModel):
    agent_id: str
    alias: str
    before: str | None
    after: str
    actor: str
    reason: str
    at: datetime


class RegistryEvent(RegModel):
    type: str
    actor: str
    principal_type: str
    origin: str | None = None
    proposal_id: str | None = None
    candidate_hash: str | None = None
    release_id: str | None = None
    at: datetime


class EntityInRelease(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    changed_vs_base: bool


class ReleaseDetail(RegModel):
    release_id: str
    status: Literal["active", "revoked"]
    agent_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: str | None
    proposal_id: str | None
    base_release_id: str | None
    published_by: str
    published_at: datetime


class ChangedRef(RegModel):
    before: VersionRef
    after: VersionRef
    docs: VersionDocs


class ReleaseDiff(RegModel):
    a: str
    b: str
    added: list[VersionRef]
    removed: list[VersionRef]
    changed: list[ChangedRef]


class RunLineage(RegModel):
    run_id: str
    release_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: str | None
    proposal_id: str | None
    eval_verdict: Verdict | None
    built_by: str | None
    approved_by: str | None
    published_at: datetime


class VersionSummary(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    created_by: str
    created_at: datetime


class EntityVersion(RegModel):
    ref: VersionRef
    content: dict[str, JsonValue]
    content_hash: str
    docs: VersionDocs
    created_by: str
    created_at: datetime
```

`agent_core/registry/entities.py`:
```python
"""Tipos de entidad del registry: los de M0 más `eval_suite` (spec §3.1). Codificación canónica y hash."""

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ValidationError

from agent_core.domain import ENTITY_KIND, RegistryEntity, canonical_bytes, loads, sha256_hex
from agent_core.registry.errors import IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.models import VersionRef
from agent_core.registry.suite import EvalSuite

SUITE_KIND = "eval_suite"
AnyEntity = RegistryEntity | EvalSuite

_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {kind.value: model for model, kind in ENTITY_KIND.items()} | {SUITE_KIND: EvalSuite})


def model_for(kind: str) -> type[BaseModel]:
    try:
        return _MODELS[kind]
    except KeyError:
        raise RegistryError(RegistryErrorCode.not_found, f"tipo de entidad desconocido: {kind[:40]}") from None


def entity_kind(entity: AnyEntity) -> str:
    if isinstance(entity, EvalSuite):
        return SUITE_KIND
    return ENTITY_KIND[type(entity)].value


def version_ref(entity: AnyEntity) -> VersionRef:
    return VersionRef(kind=entity_kind(entity), id=entity.id, version=entity.version)


def encode_entity(entity: AnyEntity) -> bytes:
    return canonical_bytes(entity)


def content_hash(entity: AnyEntity) -> str:
    return sha256_hex(encode_entity(entity))


def decode_entity(kind: str, data: bytes) -> AnyEntity:
    model = model_for(kind)
    try:
        entity = model.model_validate(loads(data))
    except (ValueError, ValidationError) as exc:
        raise IntegrityError(f"contenido ilegible de tipo {kind}") from exc
    assert isinstance(entity, (EvalSuite, *ENTITY_KIND))
    return entity  # type: ignore[return-value]
```
> `ENTITY_KIND` es `Mapping[type, EntityKind]` (M0, `domain/entities.py`). Si `canonical_bytes` de un modelo no reproduce el mismo modelo con `model_validate(loads(...))` (lo verifica `test_every_demo_entity_round_trips_with_same_hash`), codifica `entity.model_dump(mode="json", by_alias=True)` en `encode_entity`.

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add agent_core/registry tests/registry
git commit -m "feat(registry): modelos, formato de eval_suite y codificación de entidades

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Candidata: mezcla, cascada de versiones, pin y hashes

**Files:**
- Create: `agent_core/registry/candidate.py`
- Test: `tests/registry/test_candidate.py`

**Interfaces:**
- Consumes: `entities.*`, `models.EntityDraft`, `VersionRef`, `VersionDocs` (Task 2); de M1: `AuthoringRegistry`, `ReleaseDecl`, `pin_release`, `entity_ref_sites`, `kind_of`, `Violation`.
- Produces:
  - `CANDIDATE_RELEASE_ID = "candidate"`.
  - `@dataclass(frozen=True) Candidate` con: `agent_id: str`, `release: Release`, `decl: ReleaseDecl`, `entities: tuple[RegistryEntity, ...]`, `suites: tuple[EvalSuite, ...]`, `new_versions: tuple[VersionRef, ...]` (las que no existen publicadas), `auto_bumped: tuple[VersionRef, ...]`, `docs: Mapping[VersionRef, VersionDocs]`, `content_hashes: Mapping[VersionRef, str]`, `release_hash: str`, `candidate_hash: str`, y la propiedad `agent_version: str`.
  - `CandidateError(violations: list[Violation])`.
  - `build_candidate(*, agent_id: str, base: Release | None, base_entities: Sequence[RegistryEntity], drafts: Sequence[EntityDraft], published_hash: Callable[[VersionRef], str | None]) -> Candidate`. `published_hash` devuelve el `content_hash` guardado de esa versión, o `None` si no existe.
  - `release_hash(release: Release) -> str`.
  - `parse_semver(v: str) -> tuple[int, int, int] | None`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_candidate.py`:
```python
import pytest

from agent_core.domain import EntityKind
from agent_core.registry.candidate import CandidateError, build_candidate, parse_semver
from agent_core.registry.models import EntityDraft, VersionRef
from tests.registry.helpers import AGENT, demo_pinned, docs, prompt_draft, suite_draft


def _base():  # type: ignore[no-untyped-def]
    pinned = demo_pinned()
    return pinned.release, pinned.entities


def _none(_: VersionRef) -> None:
    return None


def test_changing_a_prompt_bumps_flow_and_agent_in_cascade() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=_none)
    assert cand.release.entities[EntityKind.prompt]["p/resumen_radicado"] == "1.1.0"
    assert cand.release.entities[EntityKind.flow]["disputa-cargo"] == "1.0.1"
    assert cand.release.entities[EntityKind.agent][AGENT] == "1.0.1"
    assert {str(r) for r in cand.auto_bumped} == {"flow:disputa-cargo@1.0.1", "agent:atencion@1.0.1"}
    assert cand.agent_version == "1.0.1"
    for ref in cand.auto_bumped:
        assert "p/resumen_radicado@1.1.0" in cand.docs[ref].changelog or ref.kind == "agent"


def test_cascade_bumps_every_dependent_once() -> None:
    base, entities = _base()
    tpl = next(e for e in entities if getattr(e, "id", "") == "t/pqr_radicado")
    draft = EntityDraft(kind="template", content={**tpl.model_dump(mode="json"), "version": "1.0.1"},
                        docs=docs())
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                           drafts=[draft, prompt_draft()], published_hash=_none)
    bumped = [str(r) for r in cand.auto_bumped]
    assert bumped.count("flow:disputa-cargo@1.0.1") == 1
    assert len(bumped) == len(set(bumped))


def test_auto_bump_skips_versions_already_published() -> None:
    base, entities = _base()
    taken = {"flow:disputa-cargo@1.0.1": "otro-hash"}
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=lambda r: taken.get(str(r)))
    assert cand.release.entities[EntityKind.flow]["disputa-cargo"] == "1.0.2"


def test_hash_is_deterministic_and_changes_with_content() -> None:
    base, entities = _base()
    a = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                        published_hash=_none)
    b = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                        published_hash=_none)
    c = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                        drafts=[prompt_draft(text="Otro texto de confirmación.")], published_hash=_none)
    assert a.candidate_hash == b.candidate_hash != c.candidate_hash
    assert len(a.candidate_hash) == 64


def test_suite_travels_with_candidate_but_not_in_release() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                           drafts=[prompt_draft(), suite_draft()], published_hash=_none)
    assert [s.id for s in cand.suites] == ["disputas-suite"]
    assert VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0") in cand.new_versions


def test_no_change_candidate_has_no_new_versions() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[],
                           published_hash=lambda r: "x")
    assert cand.new_versions == ()


def test_broken_reference_becomes_violation() -> None:
    base, entities = _base()
    draft = prompt_draft(model_profile="no-existe@1.0.0")
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert info.value.violations and info.value.violations[0].rule == "REG-PIN"


def test_knowledge_snapshot_drafts_are_rejected() -> None:
    base, entities = _base()
    draft = EntityDraft(kind="knowledge_snapshot", content={"id": "kb", "version": "1.0.0", "pages": []},
                        docs=docs())
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert info.value.violations[0].rule == "REG-KNOWLEDGE"


def test_parse_semver() -> None:
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("1.2") is None
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_candidate.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.registry.candidate'`.

- [ ] **Step 3: Implementar**

`agent_core/registry/candidate.py`:
```python
"""Candidata de una propuesta (spec §5.1, §3.4, §3.5): base + borrador, cascada de versiones, pin de M1."""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from agent_core.domain import EntityKind, RegistryEntity, Release, SchemaError, canonical_bytes, sha256_hex
from agent_core.flows import AuthoringRegistry, ReleaseDecl, Violation, entity_ref_sites, kind_of, pin_release
from agent_core.registry.entities import SUITE_KIND, content_hash, model_for, version_ref
from agent_core.registry.errors import RegistryError
from agent_core.registry.models import EntityDraft, VersionDocs, VersionRef
from agent_core.registry.suite import EvalSuite

CANDIDATE_RELEASE_ID = "candidate"
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_REF_TEXT = re.compile(r"^(?P<id>[^@]+)@(?P<v>\d+\.\d+\.\d+)$")
Key = tuple[EntityKind, str]


def parse_semver(version: str) -> tuple[int, int, int] | None:
    m = _SEMVER.match(version)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def release_hash(release: Release) -> str:
    return sha256_hex(canonical_bytes(release.model_dump(mode="json", exclude={"id", "status"})))


@dataclass(frozen=True)
class Candidate:
    agent_id: str
    release: Release
    decl: ReleaseDecl
    entities: tuple[RegistryEntity, ...]
    suites: tuple[EvalSuite, ...]
    new_versions: tuple[VersionRef, ...]
    auto_bumped: tuple[VersionRef, ...]
    docs: Mapping[VersionRef, VersionDocs]
    content_hashes: Mapping[VersionRef, str]
    release_hash: str
    candidate_hash: str

    @property
    def agent_version(self) -> str:
        return self.release.entities[EntityKind.agent][self.agent_id]


class CandidateError(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__(f"{len(violations)} violaciones")
        self.violations = violations


def _v(rule: str, message: str, path: str | None = None) -> Violation:
    return Violation(rule=rule, path=path, message=message[:500])


def _parse_drafts(drafts: Sequence[EntityDraft]) -> tuple[dict[Key, RegistryEntity], list[EvalSuite],
                                                           dict[str, VersionDocs], list[Violation]]:
    entities: dict[Key, RegistryEntity] = {}
    suites: list[EvalSuite] = []
    docs: dict[str, VersionDocs] = {}
    problems: list[Violation] = []
    for d in drafts:
        where = f"{d.kind}:{d.id}"
        if d.kind == EntityKind.knowledge_snapshot.value:
            problems.append(_v("REG-KNOWLEDGE", "en esta entrega las propuestas no cambian el conocimiento",
                               where))
            continue
        try:
            model = model_for(d.kind)
            parsed: Any = model.model_validate(d.content)
        except RegistryError:
            problems.append(_v("REG-KIND", f"tipo de entidad desconocido: {d.kind[:40]}", where))
            continue
        except (ValidationError, ValueError) as exc:
            first = str(exc).splitlines()[:3]
            problems.append(_v("REG-SCHEMA", "el contenido no cumple el esquema: " + " ".join(first), where))
            continue
        key_text = f"{d.kind}:{d.id}"
        if key_text in docs:
            problems.append(_v("REG-DUPLICATE", "la entidad aparece dos veces en el borrador", where))
            continue
        docs[key_text] = d.docs
        if isinstance(parsed, EvalSuite):
            suites.append(parsed)
        else:
            entities[(kind_of(parsed), parsed.id)] = parsed
    return entities, suites, docs, problems


def _set(doc: Any, pointer: tuple[str | int, ...], value: str) -> None:
    for part in pointer[:-1]:
        doc = doc[part]
    doc[pointer[-1]] = value


def _retarget(entity: RegistryEntity, merged: Mapping[Key, RegistryEntity]) -> tuple[RegistryEntity, list[str]]:
    """Apunta cada referencia exacta a la versión que tiene su destino en `merged`."""
    changes: list[str] = []
    dump = entity.model_dump(mode="json", by_alias=True)
    for site in entity_ref_sites(entity):
        target = merged.get((site.kind, site.ref.id))
        if target is None or not site.ref.is_exact or site.ref.spec == target.version:
            continue
        _set(dump, site.pointer, f"{site.ref.id}@{target.version}")
        changes.append(f"{site.ref.id}@{site.ref.spec} → {site.ref.id}@{target.version}")
    if not changes:
        return entity, []
    return type(entity).model_validate(dump), changes


def _next_free(ref: VersionRef, taken: Callable[[VersionRef], str | None]) -> str:
    semver = parse_semver(ref.version)
    assert semver is not None, "las versiones publicadas son exactas"
    major, minor, patch = semver
    while True:
        patch += 1
        candidate = VersionRef(kind=ref.kind, id=ref.id, version=f"{major}.{minor}.{patch}")
        if taken(candidate) is None:
            return candidate.version


def _retarget_json(value: Any, versions: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        m = _REF_TEXT.match(value)
        return f"{m['id']}@{versions[m['id']]}" if m and m["id"] in versions else value
    if isinstance(value, list):
        return [_retarget_json(v, versions) for v in value]
    if isinstance(value, dict):
        return {k: _retarget_json(v, versions) for k, v in value.items()}
    return value


def _decl(agent_id: str, base: Release | None, merged: Mapping[Key, RegistryEntity]) -> ReleaseDecl:
    def ref(kind: EntityKind, ident: str) -> str:
        return f"{ident}@{merged[(kind, ident)].version}"

    agents = sorted({i for (k, i) in merged if k is EntityKind.agent}
                    & (set(base.entities.get(EntityKind.agent, {})) | {agent_id}) if base
                    else {agent_id})
    flows = sorted(i for (k, i) in merged if k is EntityKind.flow)
    flow_versions = {i: e.version for (k, i), e in merged.items() if k in (EntityKind.flow, EntityKind.policy)}
    langs = sorted(i for (k, i) in merged if k is EntityKind.language_detection)
    lang = base.language_detection.id if base else (langs[0] if langs else "")
    injection = base.injection_ruleset.id if base and base.injection_ruleset else None
    data: dict[str, Any] = {
        "id": CANDIDATE_RELEASE_ID,
        "agents": [{"agent": ref(EntityKind.agent, a), "aliases": ["staging"]} for a in agents],
        "flows": [ref(EntityKind.flow, f) for f in flows],
        "interrupts": _retarget_json([i.model_dump(mode="json", by_alias=True) for i in base.interrupts],
                                     flow_versions) if base else [],
        "language_detection": ref(EntityKind.language_detection, lang) if lang else "sin-deteccion@1.0.0",
        "max_input_chars": base.max_input_chars if base else 4000,
    }
    if injection is not None:
        data["injection_ruleset"] = ref(EntityKind.injection_ruleset, injection)
    if base is not None and base.knowledge_snapshot is not None:
        data["knowledge"] = str(base.knowledge_snapshot)
    return ReleaseDecl.model_validate(data)


def build_candidate(*, agent_id: str, base: Release | None, base_entities: Sequence[RegistryEntity],
                    drafts: Sequence[EntityDraft], published_hash: Callable[[VersionRef], str | None]) -> Candidate:
    drafted, suites, draft_docs, problems = _parse_drafts(drafts)
    if problems:
        raise CandidateError(problems)

    merged: dict[Key, RegistryEntity] = {(kind_of(e), e.id): e for e in base_entities}
    merged.update(drafted)
    auto: dict[Key, str] = {}
    notes: dict[Key, list[str]] = {}
    changed = True
    while changed:  # punto fijo: cada clave sube de versión una sola vez
        changed = False
        for key, entity in sorted(merged.items(), key=lambda kv: (kv[0][0].value, kv[0][1])):
            if key in drafted:
                continue
            retargeted, changes = _retarget(entity, merged)
            if not changes:
                continue
            if key not in auto:
                auto[key] = _next_free(version_ref(entity), published_hash)
            notes.setdefault(key, []).extend(changes)
            dump = retargeted.model_dump(mode="json", by_alias=True)
            dump["version"] = auto[key]
            merged[key] = type(retargeted).model_validate(dump)
            changed = True

    decl = _decl(agent_id, base, merged)
    try:
        pinned = pin_release(AuthoringRegistry.from_entities(merged.values(), [decl]), CANDIDATE_RELEASE_ID)
    except SchemaError as exc:
        raise CandidateError([_v("REG-PIN", str(exc))]) from None
    if agent_id not in pinned.release.entities.get(EntityKind.agent, {}):
        raise CandidateError([_v("REG-AGENT", f"la candidata no contiene al agente {agent_id}")])

    hashes: dict[VersionRef, str] = {}
    new: list[VersionRef] = []
    docs: dict[VersionRef, VersionDocs] = {}
    for entity in [*pinned.entities, *suites]:
        ref, digest = version_ref(entity), content_hash(entity)
        hashes[ref] = digest
        stored = published_hash(ref)
        if stored is None:
            new.append(ref)
        elif stored != digest:
            problems.append(_v("REG-VERSION-TAKEN",
                               f"{ref} ya está publicada con otro contenido; usa otra versión", str(ref)))
        key_text = f"{ref.kind}:{ref.id}"
        if key_text in draft_docs:
            docs[ref] = draft_docs[key_text]
    if problems:
        raise CandidateError(problems)

    auto_refs: list[VersionRef] = []
    for key, version in sorted(auto.items(), key=lambda kv: (kv[0][0].value, kv[0][1])):
        ref = VersionRef(kind=key[0].value, id=key[1], version=version)
        if ref in hashes:
            auto_refs.append(ref)
            docs[ref] = VersionDocs(description=f"Versión derivada de {key[1]}",
                                    rationale="Actualiza referencias a versiones nuevas de la propuesta",
                                    changelog="; ".join(dict.fromkeys(notes[key])))
    r_hash = release_hash(pinned.release)
    c_hash = sha256_hex(canonical_bytes({
        "release_hash": r_hash,
        "versions": sorted([str(ref), digest] for ref, digest in hashes.items()),
    }))
    return Candidate(
        agent_id=agent_id, release=pinned.release, decl=decl, entities=tuple(pinned.entities),
        suites=tuple(suites), new_versions=tuple(sorted(new, key=str)), auto_bumped=tuple(auto_refs),
        docs=MappingProxyType(docs), content_hashes=MappingProxyType(hashes),
        release_hash=r_hash, candidate_hash=c_hash)
```
> Notas para quien implementa:
> - `SUITE_KIND` se importa para dejar clara la dependencia; si ruff lo marca como no usado, elimínalo.
> - `_decl` sin base usa la `language_detection` que traiga el borrador. Si no trae ninguna, `pin_release` falla con `REG-PIN` y ese es el comportamiento correcto.
> - Si el changelog del agente no menciona el prompt (su referencia es al flow), la prueba lo tolera con `or ref.kind == "agent"`.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry/test_candidate.py -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/candidate.py tests/registry/test_candidate.py
git commit -m "feat(registry): candidata con cascada de versiones, pin de M1 y candidate_hash

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Validación de la candidata (T-REG-04, 05, 06)

**Files:**
- Create: `agent_core/registry/validation.py`
- Test: `tests/registry/test_validation.py`

**Interfaces:**
- Consumes: `Candidate`, `parse_semver` (Task 3); `validate_registry`, `AuthoringRegistry`, `Violation` (M1).
- Produces:
  - `@dataclass(frozen=True) Limits(max_changes: int = 50, max_entity_bytes: int = 262_144, max_flow_nodes: int = 200)`.
  - `validate_candidate(c: Candidate, *, base_versions: Mapping[tuple[str, str], str], drafted: Collection[tuple[str, str]], limits: Limits = Limits()) -> list[Violation]`. `base_versions` es `(kind, id) → versión` en la base; `drafted` son las claves `(kind, id)` del borrador.
  - `check_draft_limits(drafts: Sequence[EntityDraft], limits: Limits) -> list[Violation]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_validation.py`:
```python
import pytest

from agent_core.registry.candidate import CandidateError, build_candidate
from agent_core.registry.models import EntityDraft
from agent_core.registry.validation import Limits, check_draft_limits, validate_candidate
from tests.registry.helpers import AGENT, demo_pinned, docs, prompt_draft, suite_draft


def _cand(*drafts: EntityDraft):  # type: ignore[no-untyped-def]
    pinned = demo_pinned()
    return pinned, build_candidate(agent_id=AGENT, base=pinned.release, base_entities=pinned.entities,
                                   drafts=list(drafts), published_hash=lambda r: None)


def _base_versions(pinned) -> dict[tuple[str, str], str]:  # type: ignore[no-untyped-def]
    return {(k.value, i): v for k, by in pinned.release.entities.items() for i, v in by.items()}


def test_valid_candidate_has_no_violations() -> None:
    pinned, cand = _cand(prompt_draft(), suite_draft())
    assert validate_candidate(cand, base_versions=_base_versions(pinned),
                              drafted={("prompt", "p/resumen_radicado")}) == []


def test_version_not_greater_than_base_is_violation() -> None:  # T-REG-05
    pinned, cand = _cand(prompt_draft(version="0.9.0"))
    out = validate_candidate(cand, base_versions=_base_versions(pinned),
                             drafted={("prompt", "p/resumen_radicado")})
    assert [v.rule for v in out] == ["REG-VERSION"]
    assert "mayor" in out[0].message


def test_entity_over_size_limit_is_violation() -> None:  # T-REG-06
    pinned, cand = _cand(prompt_draft(text="x" * 5000))
    out = validate_candidate(cand, base_versions=_base_versions(pinned),
                             drafted={("prompt", "p/resumen_radicado")}, limits=Limits(max_entity_bytes=1000))
    assert "REG-LIMIT" in [v.rule for v in out]


def test_too_many_changes_is_violation() -> None:
    drafts = [prompt_draft()] * 3
    assert [v.rule for v in check_draft_limits(drafts, Limits(max_changes=2))] == ["REG-LIMIT"]


def test_suite_of_other_agent_is_violation() -> None:
    pinned, cand = _cand(prompt_draft(), suite_draft(agent_id="otro"))
    out = validate_candidate(cand, base_versions=_base_versions(pinned), drafted=set())
    assert "REG-SUITE" in [v.rule for v in out]


def test_unknown_kind_and_bad_schema_are_violations() -> None:  # Review Focus 4
    bad_kind = EntityDraft(kind="nope", content={"id": "x", "version": "1.0.0"}, docs=docs())
    bad_schema = EntityDraft(kind="template", content={"id": "t/x", "version": "1.0.0", "locales": 3},
                             docs=docs())
    with pytest.raises(CandidateError) as info:
        _cand(bad_kind, bad_schema)
    assert sorted(v.rule for v in info.value.violations) == ["REG-KIND", "REG-SCHEMA"]
    assert all(v.message for v in info.value.violations)
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_validation.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.registry.validation'`.

- [ ] **Step 3: Implementar**

`agent_core/registry/validation.py`:
```python
"""Validación de la candidata (spec §5.2): M1 completo, versionado, límites y suites."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from agent_core.domain import Flow
from agent_core.flows import AuthoringRegistry, Violation, validate_registry
from agent_core.registry.candidate import Candidate, parse_semver
from agent_core.registry.entities import encode_entity, version_ref
from agent_core.registry.models import EntityDraft


@dataclass(frozen=True)
class Limits:
    max_changes: int = 50
    max_entity_bytes: int = 262_144
    max_flow_nodes: int = 200


def check_draft_limits(drafts: Sequence[EntityDraft], limits: Limits) -> list[Violation]:
    if len(drafts) > limits.max_changes:
        return [Violation(rule="REG-LIMIT", message=f"la propuesta cambia {len(drafts)} entidades; "
                                                      f"el máximo es {limits.max_changes}")]
    return []


def validate_candidate(c: Candidate, *, base_versions: Mapping[tuple[str, str], str],
                       drafted: Collection[tuple[str, str]], limits: Limits = Limits()) -> list[Violation]:
    out: list[Violation] = list(validate_registry(AuthoringRegistry.from_entities(c.entities, [c.decl])))
    for entity in [*c.entities, *c.suites]:
        ref = version_ref(entity)
        where = f"{ref.kind}:{ref.id}"
        size = len(encode_entity(entity))
        if size > limits.max_entity_bytes:
            out.append(Violation(rule="REG-LIMIT", path=where,
                                 message=f"{where} ocupa {size} bytes; el máximo es {limits.max_entity_bytes}"))
        if isinstance(entity, Flow) and len(entity.nodes) > limits.max_flow_nodes:
            out.append(Violation(rule="REG-LIMIT", path=where,
                                 message=f"el flow tiene {len(entity.nodes)} nodos; "
                                         f"el máximo es {limits.max_flow_nodes}"))
        if (ref.kind, ref.id) in drafted and (ref.kind, ref.id) in base_versions:
            new, old = parse_semver(ref.version), parse_semver(base_versions[(ref.kind, ref.id)])
            if new is None or old is None or new <= old:
                out.append(Violation(rule="REG-VERSION", path=where,
                                     message=f"la versión {ref.version} debe ser mayor que la vigente "
                                             f"{base_versions[(ref.kind, ref.id)]}"))
    for suite in c.suites:
        if suite.agent_id != c.agent_id:
            out.append(Violation(rule="REG-SUITE", path=f"eval_suite:{suite.id}",
                                 message=f"la suite es del agente {suite.agent_id}, no de {c.agent_id}"))
    return out
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS. Si `validate_registry` devuelve violaciones sobre el registro de demo fijado (por ejemplo, porque una regla espera rangos), anótalas en la prueba `test_valid_candidate_has_no_violations`, detente y pregunta: no las silencies.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/validation.py tests/registry/test_validation.py
git commit -m "feat(registry): validación de la candidata (M1, versiones, límites y suites)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Almacén transaccional: protocolos, memoria y blobs

**Files:**
- Create: `agent_core/registry/blobs.py`, `agent_core/registry/store.py`, `agent_core/registry/memory.py`
- Test: `tests/registry/test_memory_store.py`

**Interfaces:**
- Consumes: modelos (Task 2), `IntegrityError` (Task 1).
- Produces:
  - `BlobStore` (Protocol): `put(data: bytes) -> str`, `get(digest: str) -> bytes` (verifica el hash y lanza `IntegrityError`).
  - `InMemoryBlobStore` y `verified(digest, data) -> bytes`.
  - `RegistryTx` (Protocol) con estos métodos exactos:
    - `blobs: BlobStore`;
    - `get_proposal(proposal_id, *, for_update=False) -> Proposal | None`, `save_proposal(p) -> None`;
    - `get_changes(proposal_id) -> list[EntityDraft]`, `replace_changes(proposal_id, drafts) -> None`;
    - `get_version(ref) -> StoredVersion | None`, `list_versions(kind, entity_id) -> list[StoredVersion]`, `insert_version(v) -> None`;
    - `get_release(release_id) -> StoredRelease | None`, `release_refs(release_id) -> list[VersionRef]`, `insert_release(stored, refs) -> None`;
    - `release_status(release_id) -> Literal["active","revoked"] | None`, `set_release_status(release_id, status) -> None`;
    - `latest_release_for_agent_version(agent_id, version) -> str | None`;
    - `get_alias(agent_id, alias, *, for_update=False) -> str | None`, `set_alias(change: AliasChange) -> None`, `aliases_to(release_id) -> list[tuple[str, str]]`;
    - `insert_eval_run(run) -> None`, `latest_eval_run(proposal_id, candidate_hash) -> EvalRun | None`;
    - `insert_approval(a) -> None`, `latest_approval(proposal_id, candidate_hash) -> Approval | None`;
    - `append_event(e) -> None`, `events() -> list[RegistryEvent]`;
    - `get_publish_key(key) -> tuple[str, str] | None`, `put_publish_key(key, proposal_id, release_id) -> None`.
  - `RegistryStore` (Protocol): `transaction() -> AbstractContextManager[RegistryTx]`.
  - `InMemoryRegistryStore` (con `fail_on: Callable[[str], bool] | None` para inyectar fallas por nombre de método).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_memory_store.py`:
```python
from datetime import timedelta

import pytest

from agent_core.domain import sha256_hex
from agent_core.registry.blobs import InMemoryBlobStore
from agent_core.registry.errors import IntegrityError
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AliasChange, Origin, Proposal, ProposalState
from testing.builders import NOW


def _proposal(pid: str = "prop-1") -> Proposal:
    return Proposal(proposal_id=pid, agent_id="atencion", origin=Origin.manual, state=ProposalState.draft,
                    base_release_id=None, title="t", created_by="ana", updated_at=NOW)


def test_blob_round_trip_and_integrity() -> None:  # T-REG-19 (memoria)
    blobs = InMemoryBlobStore()
    digest = blobs.put(b"hola")
    assert digest == sha256_hex(b"hola") and blobs.get(digest) == b"hola"
    blobs.corrupt(digest, b"otro")
    with pytest.raises(IntegrityError):
        blobs.get(digest)


def test_transaction_commits() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.save_proposal(_proposal())
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is not None


def test_exception_rolls_back_everything() -> None:
    store = InMemoryRegistryStore()
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.save_proposal(_proposal())
        tx.set_alias(AliasChange(agent_id="a", alias="staging", before=None, after="rel-1", actor="ana",
                                 reason="r", at=NOW))
        raise RuntimeError("falla a mitad")
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is None
        assert tx.get_alias("a", "staging") is None


def test_injected_failure_by_method_name() -> None:
    store = InMemoryRegistryStore(fail_on=lambda name: name == "set_alias")
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.save_proposal(_proposal())
        tx.set_alias(AliasChange(agent_id="a", alias="staging", before=None, after="r", actor="x", reason="r",
                                 at=NOW + timedelta(seconds=1)))
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is None


def test_returned_objects_do_not_alias_state() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.save_proposal(_proposal())
    with store.transaction() as tx:
        p = tx.get_proposal("prop-1")
        assert p is not None and p.model_copy(update={"title": "x"}).title == "x"
        assert tx.get_proposal("prop-1") == p
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_memory_store.py -v`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`agent_core/registry/blobs.py`:
```python
"""`BlobStore`: contenido direccionado por hash (spec §2 #2). Toda lectura verifica el hash."""

from typing import Protocol

from agent_core.domain import sha256_hex
from agent_core.registry.errors import IntegrityError


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, digest: str) -> bytes: ...


def verified(digest: str, data: bytes) -> bytes:
    if sha256_hex(data) != digest:
        raise IntegrityError(f"el contenido {digest[:12]}… no coincide con su hash")
    return data


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        digest = sha256_hex(data)
        self._data.setdefault(digest, bytes(data))
        return digest

    def get(self, digest: str) -> bytes:
        try:
            return verified(digest, self._data[digest])
        except KeyError:
            raise IntegrityError(f"no existe el contenido {digest[:12]}…") from None

    def corrupt(self, digest: str, data: bytes) -> None:
        """Solo para pruebas (T-REG-19)."""
        self._data[digest] = data
```

`agent_core/registry/store.py`:
```python
"""Puerto de persistencia del registry. Una transacción es todo o nada (spec §5.4)."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from agent_core.registry.blobs import BlobStore
from agent_core.registry.models import (
    AliasChange,
    Approval,
    EntityDraft,
    EvalRun,
    Proposal,
    RegistryEvent,
    StoredRelease,
    StoredVersion,
    VersionRef,
)

Status = Literal["active", "revoked"]


class RegistryTx(Protocol):
    blobs: BlobStore

    def get_proposal(self, proposal_id: str, *, for_update: bool = False) -> Proposal | None: ...
    def save_proposal(self, proposal: Proposal) -> None: ...
    def get_changes(self, proposal_id: str) -> list[EntityDraft]: ...
    def replace_changes(self, proposal_id: str, drafts: Sequence[EntityDraft]) -> None: ...
    def get_version(self, ref: VersionRef) -> StoredVersion | None: ...
    def list_versions(self, kind: str, entity_id: str) -> list[StoredVersion]: ...
    def insert_version(self, version: StoredVersion) -> None: ...
    def get_release(self, release_id: str) -> StoredRelease | None: ...
    def release_refs(self, release_id: str) -> list[VersionRef]: ...
    def insert_release(self, stored: StoredRelease, refs: Sequence[VersionRef]) -> None: ...
    def release_status(self, release_id: str) -> Status | None: ...
    def set_release_status(self, release_id: str, status: Status) -> None: ...
    def latest_release_for_agent_version(self, agent_id: str, version: str) -> str | None: ...
    def get_alias(self, agent_id: str, alias: str, *, for_update: bool = False) -> str | None: ...
    def set_alias(self, change: AliasChange) -> None: ...
    def aliases_to(self, release_id: str) -> list[tuple[str, str]]: ...
    def insert_eval_run(self, run: EvalRun) -> None: ...
    def latest_eval_run(self, proposal_id: str, candidate_hash: str) -> EvalRun | None: ...
    def insert_approval(self, approval: Approval) -> None: ...
    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None: ...
    def append_event(self, event: RegistryEvent) -> None: ...
    def events(self) -> list[RegistryEvent]: ...
    def get_publish_key(self, key: str) -> tuple[str, str] | None: ...
    def put_publish_key(self, key: str, proposal_id: str, release_id: str) -> None: ...


class RegistryStore(Protocol):
    def transaction(self) -> AbstractContextManager[RegistryTx]: ...
```

`agent_core/registry/memory.py`:
```python
"""`RegistryStore` en memoria para pruebas: un lock global (equivale a FOR UPDATE) y rollback por copia."""

import copy
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_core.registry.blobs import InMemoryBlobStore
from agent_core.registry.models import (
    AliasChange,
    Approval,
    EntityDraft,
    EvalRun,
    Proposal,
    RegistryEvent,
    StoredRelease,
    StoredVersion,
    VersionRef,
)
from agent_core.registry.store import RegistryTx, Status


@dataclass
class _State:
    proposals: dict[str, Proposal] = field(default_factory=dict)
    changes: dict[str, list[EntityDraft]] = field(default_factory=dict)
    versions: dict[VersionRef, StoredVersion] = field(default_factory=dict)
    releases: dict[str, StoredRelease] = field(default_factory=dict)
    release_refs: dict[str, list[VersionRef]] = field(default_factory=dict)
    status: dict[str, Status] = field(default_factory=dict)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    alias_log: list[AliasChange] = field(default_factory=list)
    eval_runs: list[EvalRun] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    events: list[RegistryEvent] = field(default_factory=list)
    publish_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    blobs: InMemoryBlobStore = field(default_factory=InMemoryBlobStore)


class _Tx:
    def __init__(self, state: _State, fail_on: Callable[[str], bool] | None) -> None:
        self._s = state
        self._fail_on = fail_on
        self.blobs = state.blobs

    def __getattribute__(self, name: str) -> Any:
        fail_on = object.__getattribute__(self, "_fail_on")
        if fail_on is not None and not name.startswith("_") and fail_on(name):
            raise RuntimeError(f"falla inyectada en {name}")
        return object.__getattribute__(self, name)

    def get_proposal(self, proposal_id: str, *, for_update: bool = False) -> Proposal | None:
        return self._s.proposals.get(proposal_id)

    def save_proposal(self, proposal: Proposal) -> None:
        self._s.proposals[proposal.proposal_id] = proposal

    def get_changes(self, proposal_id: str) -> list[EntityDraft]:
        return list(self._s.changes.get(proposal_id, []))

    def replace_changes(self, proposal_id: str, drafts: Sequence[EntityDraft]) -> None:
        self._s.changes[proposal_id] = list(drafts)

    def get_version(self, ref: VersionRef) -> StoredVersion | None:
        return self._s.versions.get(ref)

    def list_versions(self, kind: str, entity_id: str) -> list[StoredVersion]:
        return [v for r, v in self._s.versions.items() if (r.kind, r.id) == (kind, entity_id)]

    def insert_version(self, version: StoredVersion) -> None:
        if version.ref in self._s.versions:
            raise ValueError(f"{version.ref} ya existe")
        self._s.versions[version.ref] = version

    def get_release(self, release_id: str) -> StoredRelease | None:
        return self._s.releases.get(release_id)

    def release_refs(self, release_id: str) -> list[VersionRef]:
        return list(self._s.release_refs.get(release_id, []))

    def insert_release(self, stored: StoredRelease, refs: Sequence[VersionRef]) -> None:
        rid = stored.release.id
        if rid in self._s.releases:
            raise ValueError(f"la release {rid} ya existe")
        self._s.releases[rid] = stored
        self._s.release_refs[rid] = list(refs)
        self._s.status[rid] = "active"

    def release_status(self, release_id: str) -> Status | None:
        return self._s.status.get(release_id)

    def set_release_status(self, release_id: str, status: Status) -> None:
        self._s.status[release_id] = status

    def latest_release_for_agent_version(self, agent_id: str, version: str) -> str | None:
        found = [r for r in self._s.releases.values() if (r.agent_id, r.agent_version) == (agent_id, version)]
        return max(found, key=lambda r: r.published_at).release.id if found else None

    def get_alias(self, agent_id: str, alias: str, *, for_update: bool = False) -> str | None:
        return self._s.aliases.get((agent_id, alias))

    def set_alias(self, change: AliasChange) -> None:
        self._s.aliases[(change.agent_id, change.alias)] = change.after
        self._s.alias_log.append(change)

    def aliases_to(self, release_id: str) -> list[tuple[str, str]]:
        return sorted(k for k, v in self._s.aliases.items() if v == release_id)

    def insert_eval_run(self, run: EvalRun) -> None:
        self._s.eval_runs.append(run)

    def latest_eval_run(self, proposal_id: str, candidate_hash: str) -> EvalRun | None:
        runs = [r for r in self._s.eval_runs if (r.proposal_id, r.candidate_hash) == (proposal_id, candidate_hash)]
        return runs[-1] if runs else None

    def insert_approval(self, approval: Approval) -> None:
        self._s.approvals.append(approval)

    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None:
        found = [a for a in self._s.approvals if (a.proposal_id, a.candidate_hash) == (proposal_id, candidate_hash)]
        return found[-1] if found else None

    def append_event(self, event: RegistryEvent) -> None:
        self._s.events.append(event)

    def events(self) -> list[RegistryEvent]:
        return list(self._s.events)

    def get_publish_key(self, key: str) -> tuple[str, str] | None:
        return self._s.publish_keys.get(key)

    def put_publish_key(self, key: str, proposal_id: str, release_id: str) -> None:
        self._s.publish_keys[key] = (proposal_id, release_id)


class InMemoryRegistryStore:
    def __init__(self, fail_on: Callable[[str], bool] | None = None) -> None:
        self._state = _State()
        self._lock = threading.RLock()
        self.fail_on = fail_on

    @contextmanager
    def transaction(self) -> Iterator[RegistryTx]:
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            try:
                yield _Tx(self._state, self.fail_on)
            except BaseException:
                self._state = snapshot
                raise
```
> Los modelos son `frozen`: devolverlos desde el estado no permite que el llamador mute el almacén. Las listas se copian.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/blobs.py agent_core/registry/store.py agent_core/registry/memory.py tests/registry/test_memory_store.py
git commit -m "feat(registry): almacén transaccional (protocolo y memoria) y BlobStore

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `SnapshotRegistry` y suite de contrato (T-REG-17 en memoria)

**Files:**
- Create: `agent_core/registry/snapshot.py`
- Modify: `tests/contracts/test_registry_contract.py` (parámetro `snapshot`)
- Test: `tests/registry/test_snapshot.py`

**Interfaces:**
- Consumes: `Release`, `RegistryEntity`, `AgentSelector`, `Principal`, `require_exact_refs` (M0).
- Produces: `SnapshotRegistry(release: Release, entities: Iterable[RegistryEntity])`, que implementa `RegistryPort`. `resolve_release` devuelve la release para cualquier alias del agente contenido; con versión, solo si coincide.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_snapshot.py`:
```python
import pytest

from agent_core.domain import AgentSelector, EntityKind, EntityRef, Prompt
from agent_core.registry.snapshot import SnapshotRegistry
from testing.builders import principal
from tests.registry.helpers import AGENT, demo_pinned


def _snap() -> SnapshotRegistry:
    pinned = demo_pinned()
    return SnapshotRegistry(pinned.release, pinned.entities)


def test_resolves_any_alias_and_exact_agent_version() -> None:
    snap = _snap()
    assert snap.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).id == "demo"
    assert snap.resolve_release(AgentSelector.parse(f"{AGENT}@1.0.0"), principal()).id == "demo"
    with pytest.raises(KeyError):
        snap.resolve_release(AgentSelector.parse(f"{AGENT}@9.9.9"), principal())
    with pytest.raises(KeyError):
        snap.resolve_release(AgentSelector.parse("otro"), principal())


def test_status_is_active_only_for_its_release() -> None:
    snap = _snap()
    assert snap.release_status("demo") == "active"
    with pytest.raises(KeyError):
        snap.release_status("x")


def test_get_returns_copies() -> None:
    snap = _snap()
    ref = EntityRef(id="p/resumen_radicado", version="1.0.0")
    first = snap.get(ref, Prompt)
    first.locales["es"] = "hackeado"
    assert snap.get(ref, Prompt).locales["es"] != "hackeado"


def test_release_with_agent() -> None:
    assert AGENT in _snap().resolve_release(AgentSelector.parse(AGENT), principal()).entities[EntityKind.agent]
```

En `tests/contracts/test_registry_contract.py`, agrega al final:
```python
# --- SnapshotRegistry (registry, spec §5.3): las comprobaciones de entidades ----------------------------

@pytest.fixture
def snapshot_registry() -> RegistryPort:
    from agent_core.registry.snapshot import SnapshotRegistry
    return SnapshotRegistry(_release("rel-1"), [
        Flow.model_validate(EXACT_FLOW), Flow.model_validate(RANGED_FLOW),
        Template(id="t/saludo", version="1.0.0", locales={"es": "Hola"}), KNOWLEDGE])


def test_snapshot_get_exact_entity(snapshot_registry: RegistryPort) -> None:
    check_get_exact_entity(snapshot_registry)


def test_snapshot_get_with_non_exact_content_raises(snapshot_registry: RegistryPort) -> None:
    check_get_with_non_exact_content_raises(snapshot_registry)


def test_snapshot_get_knowledge_snapshot(snapshot_registry: RegistryPort) -> None:
    check_get_knowledge_snapshot(snapshot_registry)
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_snapshot.py tests/contracts/test_registry_contract.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.registry.snapshot'`.

- [ ] **Step 3: Implementar**

`agent_core/registry/snapshot.py`:
```python
"""`RegistryPort` de solo lectura sobre una release en memoria (spec §5.3). Solo lo usa el evaluador."""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from agent_core.domain import AgentSelector, EntityKind, EntityRef, Principal, RegistryEntity, Release, require_exact_refs


class SnapshotRegistry:
    def __init__(self, release: Release, entities: Iterable[RegistryEntity]) -> None:
        require_exact_refs(release)
        self._release = release.model_copy(deep=True)
        self._entities: dict[tuple[type[BaseModel], str, str], BaseModel] = {
            (type(e), e.id, e.version): e.model_copy(deep=True) for e in entities}

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        agents = self._release.entities.get(EntityKind.agent, {})
        if selector.id not in agents:
            raise KeyError(selector.id)
        if selector.version is not None and selector.version != agents[selector.id]:
            raise KeyError(f"{selector.id}@{selector.version}")
        return self._release.model_copy(deep=True)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        if release_id != self._release.id:
            raise KeyError(release_id)
        return "active"

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        stored = self._entities[(kind, ref.id, ref.version)]
        require_exact_refs(stored)
        entity = stored.model_copy(deep=True)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref.id}@{ref.version} no es {kind.__name__}")
        return entity
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry tests/contracts -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/snapshot.py tests/registry/test_snapshot.py tests/contracts/test_registry_contract.py
git commit -m "feat(registry): SnapshotRegistry y contrato de RegistryPort en memoria

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Calificación desde eventos y gate (T-REG-08, 09, 10, 25)

**Files:**
- Create: `agent_core/registry/evaluation/scoring.py`, `agent_core/registry/evaluation/gate.py`
- Test: `tests/registry/test_scoring.py`, `tests/registry/test_gate.py`

**Interfaces:**
- Consumes: `report.*` (Task 2), `suite.Expect`, `EvalSuite`; eventos de M0 (`ActionDispatched`, `ActionVerified`, `RunClosed`, `Escalated`, `EngineEvent`, `dumps`).
- Produces:
  - `GUARDRAILS = ("unverified_writes", "unsupported_success", "sensitive_leaks")`.
  - `score_run(events: Sequence[EngineEvent], expect: Expect, sensitive: Sequence[str]) -> RunScore`.
  - `aggregate(scores: Sequence[RunScore]) -> SuiteMetrics`. `primary` es un `Decimal` cuantizado a 4 decimales; los guardarraíles son sumas.
  - `decide(suite: EvalSuite, candidate: SuiteMetrics, base: SuiteMetrics | None) -> tuple[Verdict, list[MetricCheck]]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_scoring.py`:
```python
from agent_core.domain import (
    ActionDispatched,
    ActionDispatchedPayload,
    ActionVerified,
    ActionVerifiedPayload,
    EngineEvent,
    EntityRef,
    Escalated,
    EscalatedPayload,
    Outcome,
    RunClosed,
    RunClosedPayload,
)
from agent_core.registry.evaluation.scoring import aggregate, score_run
from agent_core.registry.suite import Expect
from testing.builders import NOW

_BASE = {"run_id": "run-1", "release": "rel", "ts": NOW}


def _dispatched(aid: str, tool: str = "radicar_pqr") -> EngineEvent:
    return ActionDispatched(event_id=f"e-d-{aid}", **_BASE, payload=ActionDispatchedPayload(
        action_id=aid, tool=EntityRef(id=tool, version="1.0.0")))


def _verified(aid: str, result: str = "verified") -> EngineEvent:
    return ActionVerified(event_id=f"e-v-{aid}", **_BASE, payload=ActionVerifiedPayload(
        action_id=aid, result=result, readback_call_id="c"))  # type: ignore[arg-type]


def _closed(outcome: str) -> EngineEvent:
    return RunClosed(event_id="e-c", **_BASE, payload=RunClosedPayload(outcome=Outcome(outcome), closed_by="flow"))


def test_resolved_with_verified_action_passes() -> None:  # T-REG-25
    events = [_dispatched("a1"), _verified("a1"), _closed("resolved")]
    s = score_run(events, Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"], escalated=False), [])
    assert s.passed and s.guardrails == {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


def test_unverified_write_and_unsupported_success_are_counted() -> None:
    events = [_dispatched("a1"), _verified("a1", "failed"), _closed("resolved")]
    s = score_run(events, Expect(outcome=Outcome.resolved), [])
    assert s.guardrails["unverified_writes"] == 1 and s.guardrails["unsupported_success"] == 1


def test_outcome_mismatch_and_missing_action_fail_with_reasons() -> None:
    s = score_run([_closed("cancelled")], Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"]), [])
    assert not s.passed and len(s.failures) == 2


def test_escalation_expectation() -> None:
    esc = Escalated(event_id="e-x", **_BASE, payload=EscalatedPayload(
        reason_code="policy", target_queue="q", priority="normal", handoff_ref="h"))
    assert not score_run([esc], Expect(escalated=False), []).passed
    assert score_run([esc], Expect(escalated=True), []).escalated


def test_sensitive_value_in_any_event_is_a_leak() -> None:
    events = [_dispatched("4111-1111")]
    assert score_run(events, Expect(), ["4111-1111"]).guardrails["sensitive_leaks"] == 1


def test_aggregate_primary_is_decimal_fraction() -> None:
    ok = score_run([_closed("resolved")], Expect(outcome=Outcome.resolved), [])
    bad = score_run([_closed("failed")], Expect(outcome=Outcome.resolved), [])
    m = aggregate([ok, ok, bad])
    assert str(m.primary) == "0.6667" and m.runs == 3
```
> Si `reason_code="policy"` no es un `ReasonCodeStr` válido, usa uno de los valores de `agent_core.domain.outcomes`.

`tests/registry/test_gate.py`:
```python
from decimal import Decimal

from agent_core.registry.evaluation.gate import decide
from agent_core.registry.evaluation.report import SuiteMetrics
from agent_core.registry.suite import EvalSuite
from tests.registry.helpers import suite_content

SUITE = EvalSuite.model_validate(suite_content())  # margen 0.05, piso 0.5
ZERO = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


def _m(primary: str, **g: int) -> SuiteMetrics:
    return SuiteMetrics(primary=Decimal(primary), guardrails={**ZERO, **g}, runs=10)


def test_guardrail_regression_fails_even_if_primary_improves() -> None:  # T-REG-08
    verdict, checks = decide(SUITE, _m("0.9", unverified_writes=1), _m("0.5"))
    assert verdict == "fail"
    assert [c.name for c in checks if not c.passed] == ["unverified_writes"]


def test_primary_within_margin_passes_outside_fails() -> None:  # T-REG-09
    assert decide(SUITE, _m("0.76"), _m("0.80"))[0] == "pass"
    assert decide(SUITE, _m("0.74"), _m("0.80"))[0] == "fail"


def test_without_base_uses_floor_and_zero_guardrails() -> None:  # T-REG-10
    assert decide(SUITE, _m("0.5"), None)[0] == "pass"
    assert decide(SUITE, _m("0.49"), None)[0] == "fail"
    assert decide(SUITE, _m("0.9", sensitive_leaks=1), None)[0] == "fail"


def test_every_check_reports_value_base_and_threshold() -> None:
    _, checks = decide(SUITE, _m("0.8"), _m("0.8"))
    primary = next(c for c in checks if c.name == "primary")
    assert (primary.value, primary.base, primary.threshold) == (Decimal("0.8"), Decimal("0.8"), Decimal("0.75"))
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_scoring.py tests/registry/test_gate.py -v`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`agent_core/registry/evaluation/scoring.py`:
```python
"""Calificación de una corrida desde los eventos del motor (spec §6.2 pasos 3–4). Pura y determinista."""

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from agent_core.domain import ActionDispatched, ActionVerified, EngineEvent, Escalated, RunClosed, dumps
from agent_core.registry.evaluation.report import RunScore, SuiteMetrics
from agent_core.registry.suite import Expect

GUARDRAILS = ("unverified_writes", "unsupported_success", "sensitive_leaks")
_QUANT = Decimal("0.0001")


def score_run(events: Sequence[EngineEvent], expect: Expect, sensitive: Sequence[str]) -> RunScore:
    dispatched = {e.payload.action_id: e.payload.tool.id for e in events if isinstance(e, ActionDispatched)}
    verified = {e.payload.action_id for e in events
                if isinstance(e, ActionVerified) and e.payload.result == "verified"}
    closed = [e for e in events if isinstance(e, RunClosed)]
    outcome = closed[-1].payload.outcome.value if closed else None
    escalated = any(isinstance(e, Escalated) for e in events)
    unverified = sum(1 for aid in dispatched if aid not in verified)
    serialized = [dumps(e) for e in events]
    leaks = sum(text.count(value) for value in sensitive if value for text in serialized)
    guardrails = {
        "unverified_writes": unverified,
        "unsupported_success": 1 if outcome == "resolved" and unverified else 0,
        "sensitive_leaks": leaks,
    }
    failures: list[str] = []
    if expect.outcome is not None and outcome != expect.outcome.value:
        failures.append(f"se esperaba outcome {expect.outcome.value} y fue {outcome}")
    done = {dispatched[aid] for aid in verified if aid in dispatched}
    for tool in expect.actions_verified:
        if tool not in done:
            failures.append(f"la acción {tool} no quedó verificada")
    if expect.escalated is not None and escalated != expect.escalated:
        failures.append("se esperaba escalamiento" if expect.escalated else "escaló sin esperarlo")
    return RunScore(passed=not failures, failures=failures, guardrails=guardrails, outcome=outcome,
                    escalated=escalated)


def aggregate(scores: Sequence[RunScore]) -> SuiteMetrics:
    runs = len(scores)
    passed = sum(1 for s in scores if s.passed)
    primary = (Decimal(passed) / Decimal(runs)).quantize(_QUANT, ROUND_HALF_EVEN) if runs else Decimal(0)
    guardrails = {g: sum(s.guardrails.get(g, 0) for s in scores) for g in GUARDRAILS}
    return SuiteMetrics(primary=primary, guardrails=guardrails, runs=runs)
```

`agent_core/registry/evaluation/gate.py`:
```python
"""Veredicto del gate (spec §6.4, ADR 0018): guardarraíles con tolerancia cero y métrica principal."""

from decimal import Decimal

from agent_core.registry.evaluation.report import MetricCheck, SuiteMetrics, Verdict
from agent_core.registry.evaluation.scoring import GUARDRAILS
from agent_core.registry.suite import EvalSuite


def decide(suite: EvalSuite, candidate: SuiteMetrics, base: SuiteMetrics | None) -> tuple[Verdict, list[MetricCheck]]:
    checks: list[MetricCheck] = []
    for name in GUARDRAILS:
        value = candidate.guardrails.get(name, 0)
        limit = base.guardrails.get(name, 0) if base is not None else 0
        checks.append(MetricCheck(name=name, value=Decimal(value),
                                  base=Decimal(limit) if base is not None else None,
                                  threshold=Decimal(limit), passed=value <= limit))
    if base is not None:
        threshold = base.primary - suite.noise_margin
        checks.append(MetricCheck(name="primary", value=candidate.primary, base=base.primary,
                                  threshold=threshold, passed=candidate.primary >= threshold))
    else:
        checks.append(MetricCheck(name="primary", value=candidate.primary, base=None, threshold=suite.floor,
                                  passed=candidate.primary >= suite.floor))
    return ("pass" if all(c.passed for c in checks) else "fail"), checks
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/evaluation tests/registry/test_scoring.py tests/registry/test_gate.py
git commit -m "feat(registry): calificación desde eventos y gate de evaluación

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `RegistryService`, parte 1: roles y construcción (T-REG-03, 04, 07)

**Files:**
- Create: `agent_core/registry/roles.py`, `agent_core/registry/evaluation/ports.py`, `agent_core/registry/service.py`
- Test: `tests/registry/service_world.py`, `tests/registry/test_service_build.py`

**Interfaces:**
- Consumes: todo lo anterior; `Clock`, `IdSource`, `IdKind` (M0).
- Produces:
  - `roles.CONSTRUCTOR = "constructor"`, `APROBADOR = "aprobador"`, `is_human(p) -> bool`, `actor_id(p) -> str`, `require_constructor(p) -> None`, `require_approver(p) -> None` (rol `aprobador` y humano).
  - `evaluation/ports.py`:
    - `@dataclass(frozen=True) EvalTarget(label: Label, release: Release, registry: RegistryPort)`;
    - `EvalPort` (Protocol): `run(suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport`;
    - `ScenarioHarness`, `HarnessUnavailable`, `SandboxHandle`, `SandboxPort`, `Judge` (se usan en la Task 12).
  - `service.RunReleaseReader` (Protocol): `release_of(run_id: str) -> str | None`.
  - `service.ValidationReport(violations: list[Violation], candidate_hash: str | None, auto_bumped: list[VersionRef])`.
  - `service.CandidateView(proposal_id, candidate_hash, release_id_preview, new_versions, auto_bumped)`.
  - `RegistryService(store, evaluator, clock, ids, runs=None, limits=Limits())` con:
    - `create_proposal(actor, agent_id, origin, title) -> Proposal`
    - `put_draft(actor, proposal_id, changes, expected_rev) -> Proposal`
    - `validate(actor, proposal_id) -> ValidationReport`
    - `freeze(actor, proposal_id) -> CandidateView`
    - `reopen(actor, proposal_id) -> Proposal`
    - `get_proposal(proposal_id) -> ProposalDetail`, donde `ProposalDetail(proposal, changes, last_eval: EvalRun | None)`.
  - Helpers internos que usan las tareas 9 y 10: `_load(tx, ref) -> AnyEntity`, `_base(tx, release_id) -> tuple[Release | None, list[RegistryEntity]]`, `_candidate(tx, p) -> Candidate`, `_event(tx, type, actor, p=None, release_id=None)`.

- [ ] **Step 1: Escribir el mundo de pruebas del servicio**

`tests/registry/service_world.py`:
```python
"""Servicio sobre el almacén en memoria con la release de demo sembrada como base de `staging`."""

from dataclasses import dataclass, field

from agent_core.domain import EntityKind
from agent_core.registry.candidate import release_hash
from agent_core.registry.entities import content_hash, encode_entity, version_ref
from agent_core.registry.evaluation.ports import EvalTarget
from agent_core.registry.evaluation.report import EvalReport, SuiteMetrics
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AliasChange, StoredRelease, StoredVersion, VersionDocs
from agent_core.registry.service import RegistryService
from agent_core.registry.suite import EvalSuite
from testing.builders import NOW
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, demo_pinned, suite_draft

ZERO = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


@dataclass
class FakeEvaluator:
    """`EvalPort` guionado: devuelve los reportes en orden; por defecto, `pass`."""
    reports: list[EvalReport] = field(default_factory=list)
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport:
        self.calls.append((candidate.release.id, base.release.id if base else None))
        if self.reports:
            return self.reports.pop(0)
        m = SuiteMetrics(primary=1, guardrails=ZERO, runs=1)  # type: ignore[arg-type]
        return EvalReport(verdict="pass", candidate=m, base=m)


def seed_demo(store: InMemoryRegistryStore, *, aliases: tuple[str, ...] = ("staging", "prod")) -> str:
    pinned = demo_pinned()
    docs = VersionDocs(description="semilla", rationale="", changelog="")
    with store.transaction() as tx:
        refs = []
        for entity in pinned.entities:
            tx.blobs.put(encode_entity(entity))
            ref = version_ref(entity)
            refs.append(ref)
            tx.insert_version(StoredVersion(ref=ref, content_hash=content_hash(entity), docs=docs,
                                            proposal_id=None, created_by="seed", created_at=NOW))
        release = pinned.release.model_copy(update={"id": "rel-demo"})
        tx.insert_release(StoredRelease(release=release, release_hash=release_hash(release), agent_id=AGENT,
                                        agent_version=release.entities[EntityKind.agent][AGENT],
                                        base_release_id=None, proposal_id=None, published_by="seed",
                                        published_at=NOW), refs)
        for alias in aliases:
            tx.set_alias(AliasChange(agent_id=AGENT, alias=alias, before=None, after="rel-demo", actor="seed",
                                     reason="semilla", at=NOW))
    return "rel-demo"


@dataclass
class World:
    store: InMemoryRegistryStore = field(default_factory=InMemoryRegistryStore)
    evaluator: FakeEvaluator = field(default_factory=FakeEvaluator)
    clock: FakeClock = field(default_factory=FakeClock)
    ids: FakeIds = field(default_factory=FakeIds)

    def __post_init__(self) -> None:
        seed_demo(self.store)
        self.service = RegistryService(self.store, self.evaluator, self.clock, self.ids)


SUITE = suite_draft()
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`tests/registry/test_service_build.py`:
```python
import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin, ProposalState
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World


def _code(exc: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return exc.value.code


def test_create_takes_base_from_staging() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "mejorar radicado")
    assert (p.state, p.base_release_id, p.rev, p.created_by) == (ProposalState.draft, "rel-demo", 0,
                                                                  "constructor-bot")


def test_put_draft_with_stale_rev_fails() -> None:  # T-REG-03
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    assert _code(info) is RegistryErrorCode.proposal_stale


def test_freeze_with_violation_keeps_draft() -> None:  # T-REG-04
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(version="0.1.0")], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.freeze(human(), p.proposal_id)
    assert _code(info) is RegistryErrorCode.validation_failed
    assert isinstance(info.value.payload, list) and info.value.payload[0]["rule"] == "REG-VERSION"
    assert w.service.get_proposal(p.proposal_id).proposal.state is ProposalState.draft


def test_freeze_moves_to_candidate_with_hash_and_auto_bumps() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    view = w.service.freeze(human(), p.proposal_id)
    got = w.service.get_proposal(p.proposal_id).proposal
    assert got.state is ProposalState.candidate and got.candidate_hash == view.candidate_hash
    assert view.release_id_preview == "rel-" + view.candidate_hash[:16]
    assert {str(r) for r in view.auto_bumped} == {"flow:disputa-cargo@1.0.1", "agent:atencion@1.0.1"}


def test_validate_is_read_only() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    report = w.service.validate(human(), p.proposal_id)
    assert report.violations == [] and report.candidate_hash is not None
    assert w.service.get_proposal(p.proposal_id).proposal.state is ProposalState.draft


def test_illegal_transitions() -> None:  # T-REG-07
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    with pytest.raises(RegistryError) as info:
        w.service.reopen(human(), p.proposal_id)
    assert _code(info) is RegistryErrorCode.illegal_transition
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=1)
    assert _code(info) is RegistryErrorCode.illegal_transition


def test_reopen_clears_candidate() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    again = w.service.reopen(human(), p.proposal_id)
    assert again.state is ProposalState.draft and again.candidate_hash is None


def test_without_constructor_role_is_forbidden() -> None:
    w = World()
    with pytest.raises(RegistryError) as info:
        w.service.create_proposal(human("aprobador"), AGENT, Origin.manual, "t")
    assert _code(info) is RegistryErrorCode.forbidden_role


def test_unknown_proposal_is_not_found() -> None:
    with pytest.raises(RegistryError) as info:
        World().service.freeze(human(), "prop-x")
    assert _code(info) is RegistryErrorCode.not_found


def test_events_are_recorded() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    with w.store.transaction() as tx:
        types = [e.type for e in tx.events()]
    assert types == ["proposal_created", "draft_updated", "frozen"]
```

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_service_build.py -v`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 4: Implementar**

`agent_core/registry/roles.py`:
```python
"""Roles del registry y barrera de actor humano (spec §8). Se verifica en el servidor."""

from agent_core.domain import Principal
from agent_core.registry.errors import RegistryError, RegistryErrorCode

CONSTRUCTOR = "constructor"
APROBADOR = "aprobador"
HUMAN_ATTR = "actor"


def is_human(p: Principal) -> bool:
    return p.id is not None and p.attrs.get(HUMAN_ATTR) == "human"


def actor_id(p: Principal) -> str:
    if p.id is None:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita un principal identificado")
    return p.id


def require_constructor(p: Principal) -> None:
    actor_id(p)
    if CONSTRUCTOR not in p.roles:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita el rol constructor")


def require_approver(p: Principal) -> None:
    actor_id(p)
    if APROBADOR not in p.roles or not is_human(p):
        raise RegistryError(RegistryErrorCode.forbidden_role,
                            "solo una persona con rol aprobador puede hacer esta operación")
```

`agent_core/registry/evaluation/ports.py`:
```python
"""Puertos de la evaluación (spec §6, §7.3). `ScenarioHarness` lo implementa `agent_core.composition`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_core.domain import EngineEvent, Release
from agent_core.ports import RegistryPort, ToolExecutor
from agent_core.registry.evaluation.report import EvalReport, JudgeNote, Label
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario


@dataclass(frozen=True)
class EvalTarget:
    label: Label
    release: Release
    registry: RegistryPort


class EvalPort(Protocol):
    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport: ...


class HarnessUnavailable(Exception):
    """Falla de infraestructura (LLM, sandbox, tiempo). Nunca cuenta como pase ni como fallo."""


class ScenarioHarness(Protocol):
    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario, tools: ToolExecutor) -> list[EngineEvent]:
        """Corre el escenario con el motor y devuelve los eventos del run. Lanza `HarnessUnavailable`."""
        ...


@dataclass(frozen=True)
class SandboxHandle:
    handle_id: str


class SandboxPort(Protocol):
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle: ...

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        """El ejecutor debe tener `is_sandbox = True` (spec §6.3)."""
        ...

    def teardown(self, handle: SandboxHandle) -> None: ...


@dataclass(frozen=True)
class ScenarioTranscript:
    scenario_id: str
    label: Label
    repetition: int
    events: Sequence[EngineEvent]


class Judge(Protocol):
    def score(self, suite: EvalSuite, transcripts: Sequence[ScenarioTranscript]) -> list[JudgeNote]: ...
```

`agent_core/registry/service.py` (parte 1; la Task 9 agrega decisiones y la 10 lecturas):
```python
"""`RegistryService` (spec §4, §5, §7.2): máquina de estados de propuestas sobre un `RegistryStore`."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agent_core.domain import Principal, RegistryEntity, Release
from agent_core.flows import Violation
from agent_core.ports import Clock, IdKind, IdSource
from agent_core.registry.candidate import Candidate, CandidateError, build_candidate
from agent_core.registry.entities import AnyEntity, decode_entity
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.ports import EvalPort
from agent_core.registry.models import (
    EntityDraft,
    EvalRun,
    Origin,
    Proposal,
    ProposalState,
    RegistryEvent,
    VersionRef,
)
from agent_core.registry.roles import actor_id, require_constructor
from agent_core.registry.store import RegistryStore, RegistryTx
from agent_core.registry.suite import EvalSuite
from agent_core.registry.validation import Limits, check_draft_limits, validate_candidate


class RunReleaseReader(Protocol):
    def release_of(self, run_id: str) -> str | None: ...


class _V(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


class ValidationReport(_V):
    violations: list[Violation]
    candidate_hash: str | None
    auto_bumped: list[VersionRef]


class CandidateView(_V):
    proposal_id: str
    candidate_hash: str
    release_id_preview: str
    new_versions: list[VersionRef]
    auto_bumped: list[VersionRef]


class ProposalDetail(_V):
    proposal: Proposal
    changes: list[EntityDraft]
    last_eval: EvalRun | None


def release_id_for(candidate_hash: str) -> str:
    return "rel-" + candidate_hash[:16]


def _violations_payload(violations: Sequence[Violation]) -> list[dict[str, str | None]]:
    return [{"rule": v.rule, "path": v.path, "flow": v.flow, "node_id": v.node_id, "message": v.message}
            for v in violations]


class RegistryService:
    def __init__(self, store: RegistryStore, evaluator: EvalPort, clock: Clock, ids: IdSource,
                 runs: RunReleaseReader | None = None, limits: Limits = Limits()) -> None:
        self._store, self._evaluator, self._clock, self._ids = store, evaluator, clock, ids
        self._runs, self._limits = runs, limits

    # --- helpers ---------------------------------------------------------------------------------------

    def _proposal(self, tx: RegistryTx, proposal_id: str, *, for_update: bool = True) -> Proposal:
        p = tx.get_proposal(proposal_id, for_update=for_update)
        if p is None:
            raise RegistryError(RegistryErrorCode.not_found, "la propuesta no existe")
        return p

    @staticmethod
    def _expect(p: Proposal, *states: ProposalState) -> None:
        if p.state not in states:
            allowed = ", ".join(s.value for s in states)
            raise RegistryError(RegistryErrorCode.illegal_transition,
                                f"la propuesta está en {p.state.value}; se necesita {allowed}")

    def _save(self, tx: RegistryTx, p: Proposal, **update: object) -> Proposal:
        new = p.model_copy(update={**update, "updated_at": self._clock.now()})
        tx.save_proposal(new)
        return new

    def _event(self, tx: RegistryTx, type_: str, actor: Principal, p: Proposal | None = None,
               release_id: str | None = None) -> None:
        tx.append_event(RegistryEvent(
            type=type_, actor=actor.id or "", principal_type=actor.type.value,
            origin=p.origin.value if p else None, proposal_id=p.proposal_id if p else None,
            candidate_hash=p.candidate_hash if p else None, release_id=release_id, at=self._clock.now()))

    def _load(self, tx: RegistryTx, ref: VersionRef) -> AnyEntity:
        stored = tx.get_version(ref)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, f"no existe {ref}")
        return decode_entity(ref.kind, tx.blobs.get(stored.content_hash))

    def _base(self, tx: RegistryTx, release_id: str | None) -> tuple[Release | None, list[RegistryEntity]]:
        if release_id is None:
            return None, []
        stored = tx.get_release(release_id)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, "la release base no existe")
        entities = [self._load(tx, ref) for ref in tx.release_refs(release_id)]
        return stored.release, [e for e in entities if not isinstance(e, EvalSuite)]

    def _candidate(self, tx: RegistryTx, p: Proposal) -> Candidate:
        base, entities = self._base(tx, p.base_release_id)
        drafts = tx.get_changes(p.proposal_id)

        def published(ref: VersionRef) -> str | None:
            stored = tx.get_version(ref)
            return stored.content_hash if stored else None

        cand = build_candidate(agent_id=p.agent_id, base=base, base_entities=entities, drafts=drafts,
                               published_hash=published)
        base_versions = ({(k.value, i): v for k, by in base.entities.items() for i, v in by.items()}
                         if base else {})
        problems = validate_candidate(cand, base_versions=base_versions,
                                      drafted={(d.kind, d.id) for d in drafts}, limits=self._limits)
        if problems:
            raise CandidateError(problems)
        return cand

    # --- construcción (rol constructor) ------------------------------------------------------------------

    def create_proposal(self, actor: Principal, agent_id: str, origin: Origin, title: str) -> Proposal:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = Proposal(proposal_id=self._ids.new_id(IdKind.proposal), agent_id=agent_id, origin=origin,
                         state=ProposalState.draft, base_release_id=tx.get_alias(agent_id, "staging"),
                         title=title, created_by=actor_id(actor), updated_at=self._clock.now())
            tx.save_proposal(p)
            self._event(tx, "proposal_created", actor, p)
            return p

    def put_draft(self, actor: Principal, proposal_id: str, changes: Sequence[EntityDraft],
                  expected_rev: int) -> Proposal:
        require_constructor(actor)
        problems = check_draft_limits(changes, self._limits)
        if problems:
            raise RegistryError(RegistryErrorCode.validation_failed, "el borrador excede los límites",
                                payload=_violations_payload(problems))  # type: ignore[arg-type]
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.draft)
            if p.rev != expected_rev:
                raise RegistryError(RegistryErrorCode.proposal_stale,
                                    f"la propuesta va en la revisión {p.rev}, no en {expected_rev}")
            tx.replace_changes(proposal_id, changes)
            p = self._save(tx, p, rev=p.rev + 1)
            self._event(tx, "draft_updated", actor, p)
            return p

    def validate(self, actor: Principal, proposal_id: str) -> ValidationReport:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            try:
                cand = self._candidate(tx, p)
            except CandidateError as exc:
                return ValidationReport(violations=exc.violations, candidate_hash=None, auto_bumped=[])
            return ValidationReport(violations=[], candidate_hash=cand.candidate_hash,
                                    auto_bumped=list(cand.auto_bumped))

    def freeze(self, actor: Principal, proposal_id: str) -> CandidateView:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.draft)
            try:
                cand = self._candidate(tx, p)
            except CandidateError as exc:
                failure = RegistryError(RegistryErrorCode.validation_failed,
                                        f"la candidata tiene {len(exc.violations)} violaciones",
                                        payload=_violations_payload(exc.violations))  # type: ignore[arg-type]
            else:
                p = self._save(tx, p, state=ProposalState.candidate, candidate_hash=cand.candidate_hash)
                self._event(tx, "frozen", actor, p)
                return CandidateView(proposal_id=proposal_id, candidate_hash=cand.candidate_hash,
                                     release_id_preview=release_id_for(cand.candidate_hash),
                                     new_versions=list(cand.new_versions), auto_bumped=list(cand.auto_bumped))
        raise failure

    def reopen(self, actor: Principal, proposal_id: str) -> Proposal:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.candidate, ProposalState.evaluated, ProposalState.approved)
            p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "reopened", actor, p)
            return p

    def get_proposal(self, proposal_id: str) -> ProposalDetail:
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            last = tx.latest_eval_run(p.proposal_id, p.candidate_hash) if p.candidate_hash else None
            return ProposalDetail(proposal=p, changes=tx.get_changes(proposal_id), last_eval=last)
```
> `freeze` sale de la transacción antes de lanzar `validation_failed`. La transacción no escribió nada, así que salir sin error y lanzar después es equivalente, y deja el patrón listo para la Task 9 (que sí escribe antes de lanzar `gate_failed`).

- [ ] **Step 5: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
git add agent_core/registry/roles.py agent_core/registry/evaluation/ports.py agent_core/registry/service.py tests/registry/service_world.py tests/registry/test_service_build.py
git commit -m "feat(registry): servicio de propuestas: crear, borrador, validar, congelar y reabrir

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `RegistryService`, parte 2: evaluar, decidir y publicar (T-REG-02 memoria, 11–16, 20)

**Files:**
- Modify: `agent_core/registry/service.py`
- Test: `tests/registry/test_service_decide.py`

**Interfaces:**
- Consumes: Task 8; `EvalTarget`, `SnapshotRegistry`, `EvalReport`.
- Produces, en `RegistryService`:
  - `evaluate(actor, proposal_id, suite_id: str, suite_version: str | None = None) -> EvalReport`. La suite se toma primero de la candidata; si no está, de la versión publicada (la última si `suite_version` es `None`).
  - `approve(actor, proposal_id, candidate_hash) -> Approval`.
  - `reject(actor, proposal_id, reason) -> Proposal`.
  - `publish(actor, proposal_id, idempotency_key) -> ReleaseDetail`.
  - `promote(actor, agent_id, alias, release_id, reason="") -> AliasChange`.
  - `revoke(actor, release_id, reason) -> ReleaseDetail`.
  - `get_release(release_id) -> ReleaseDetail`. Se adelanta aquí porque `publish` lo devuelve; la Task 10 agrega el resto de lecturas.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_service_decide.py`:
```python
from decimal import Decimal

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.report import EvalReport, SuiteMetrics
from agent_core.registry.models import AliasChange, Origin, ProposalState
from testing.builders import NOW
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, ZERO, World

ANA = human()


def _frozen(w: World) -> str:
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "mejorar radicado")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    return p.proposal_id


def _evaluated(w: World) -> tuple[str, str]:
    pid = _frozen(w)
    w.service.evaluate(ANA, pid, "disputas-suite")
    h = w.service.get_proposal(pid).proposal.candidate_hash
    assert h is not None
    return pid, h


def _report(verdict: str) -> EvalReport:
    m = SuiteMetrics(primary=Decimal(1), guardrails=ZERO, runs=1)
    return EvalReport(verdict=verdict, candidate=m, base=m)  # type: ignore[arg-type]


def _code(info: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return info.value.code


def test_human_completes_cycle_alone() -> None:  # T-REG-14
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    detail = w.service.publish(ANA, pid, "key-1")
    assert detail.release_id == "rel-" + h[:16] and detail.status == "active"
    with w.store.transaction() as tx:
        assert tx.get_alias(AGENT, "staging") == detail.release_id
        assert tx.get_alias(AGENT, "prod") == "rel-demo"
        approval = tx.latest_approval(pid, h)
    assert approval is not None and approval.actor == "ana"
    assert w.service.get_proposal(pid).proposal.state is ProposalState.published
    assert {e.ref.id for e in detail.entities if e.changed_vs_base} == {"p/resumen_radicado", "disputa-cargo",
                                                                         AGENT}


def test_evaluation_runs_candidate_against_base() -> None:
    w = World()
    _evaluated(w)
    assert w.evaluator.calls == [("candidate", "rel-demo")]


def test_gate_fail_returns_to_draft_and_raises() -> None:
    w = World()
    w.evaluator.reports.append(_report("fail"))
    pid = _frozen(w)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, pid, "disputas-suite")
    assert _code(info) is RegistryErrorCode.gate_failed
    got = w.service.get_proposal(pid).proposal
    assert got.state is ProposalState.draft and got.candidate_hash is None


def test_failed_infra_keeps_candidate_and_blocks_approval() -> None:  # T-REG-11
    w = World()
    w.evaluator.reports.append(_report("failed_infra"))
    pid = _frozen(w)
    assert w.service.evaluate(ANA, pid, "disputas-suite").verdict == "failed_infra"
    p = w.service.get_proposal(pid).proposal
    assert p.state is ProposalState.candidate
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, p.candidate_hash or "")
    assert _code(info) is RegistryErrorCode.illegal_transition


def test_retry_after_failed_infra_uses_latest_run() -> None:  # Review Focus 2
    w = World()
    w.evaluator.reports.extend([_report("failed_infra"), _report("pass")])
    pid = _frozen(w)
    w.service.evaluate(ANA, pid, "disputas-suite")
    w.service.evaluate(ANA, pid, "disputas-suite")
    h = w.service.get_proposal(pid).proposal.candidate_hash or ""
    assert w.service.approve(ANA, pid, h).decision == "approved"


def test_approve_with_other_hash_is_candidate_changed() -> None:  # T-REG-12
    w = World()
    pid, _ = _evaluated(w)
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, "0" * 64)
    assert _code(info) is RegistryErrorCode.candidate_changed


def test_reopen_invalidates_approval() -> None:  # T-REG-12
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    w.service.reopen(ANA, pid)
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, pid, "k")
    assert _code(info) is RegistryErrorCode.illegal_transition


@pytest.mark.parametrize("who", [bot("constructor", "aprobador"), human("constructor")])
def test_non_human_or_non_approver_cannot_decide(who) -> None:  # type: ignore[no-untyped-def]  # T-REG-13
    w = World()
    pid, h = _evaluated(w)
    for call in (lambda: w.service.approve(who, pid, h), lambda: w.service.reject(who, pid, "no"),
                 lambda: w.service.publish(who, pid, "k"),
                 lambda: w.service.promote(who, AGENT, "prod", "rel-demo"),
                 lambda: w.service.revoke(who, "rel-demo", "x")):
        with pytest.raises(RegistryError) as info:
            call()
        assert _code(info) is RegistryErrorCode.forbidden_role


def test_reject_returns_to_draft_with_reason() -> None:
    w = World()
    pid, h = _evaluated(w)
    p = w.service.reject(ANA, pid, "el tono es muy seco")
    assert p.state is ProposalState.draft
    with w.store.transaction() as tx:
        a = tx.latest_approval(pid, h)
    assert a is not None and (a.decision, a.reason) == ("rejected", "el tono es muy seco")


def test_publish_with_moved_staging_is_stale_and_rebases() -> None:  # T-REG-15
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    with w.store.transaction() as tx:
        tx.set_alias(AliasChange(agent_id=AGENT, alias="staging", before="rel-demo", after="rel-otra",
                                 actor="x", reason="r", at=NOW))
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, pid, "k")
    assert _code(info) is RegistryErrorCode.proposal_stale
    p = w.service.get_proposal(pid).proposal
    assert (p.state, p.base_release_id, p.candidate_hash) == (ProposalState.draft, "rel-otra", None)


def test_publish_failure_mid_way_leaves_nothing() -> None:  # T-REG-02 (memoria)
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    w.store.fail_on = lambda name: name == "set_alias"
    with pytest.raises(RuntimeError):
        w.service.publish(ANA, pid, "k")
    w.store.fail_on = None
    with w.store.transaction() as tx:
        assert tx.get_release("rel-" + h[:16]) is None
        assert tx.get_version(next(iter(tx.release_refs("rel-demo")))) is not None
        assert tx.get_alias(AGENT, "staging") == "rel-demo"
    assert w.service.get_proposal(pid).proposal.state is ProposalState.approved
    assert w.service.publish(ANA, pid, "k").release_id == "rel-" + h[:16]


def test_publish_retry_with_same_key_is_idempotent_after_success() -> None:  # Review Focus 3
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    first = w.service.publish(ANA, pid, "k")
    assert w.service.publish(ANA, pid, "k").release_id == first.release_id


def test_two_proposals_same_agent_second_publish_is_stale() -> None:  # T-REG-16
    w = World()
    a, ha = _evaluated(w)
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "otra")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Tercera variante."), SUITE],
                        expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    hb = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, a, ha)
    w.service.approve(ANA, p.proposal_id, hb)
    w.service.publish(ANA, a, "ka")
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, p.proposal_id, "kb")
    assert _code(info) is RegistryErrorCode.proposal_stale


def test_promote_and_revoke() -> None:  # T-REG-20 (registro)
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    rel = w.service.publish(ANA, pid, "k").release_id
    with pytest.raises(RegistryError) as info:
        w.service.revoke(ANA, "rel-demo", "apunta prod")
    assert _code(info) is RegistryErrorCode.illegal_transition
    change = w.service.promote(ANA, AGENT, "prod", rel, "sale a prod")
    assert (change.before, change.after) == ("rel-demo", rel)
    assert w.service.revoke(ANA, "rel-demo", "reemplazada").status == "revoked"
    with pytest.raises(RegistryError) as info:
        w.service.promote(ANA, AGENT, "prod", "rel-demo")
    assert _code(info) is RegistryErrorCode.illegal_transition

```
> El caso "sin release base" lo cubren `test_gate.py::test_without_base_uses_floor_and_zero_guardrails` y `test_evaluator.py::test_without_base_compares_to_floor`.

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_service_decide.py -v`
Expected: FAIL con `AttributeError: 'RegistryService' object has no attribute 'evaluate'`.

- [ ] **Step 3: Implementar.** Agrega a `agent_core/registry/service.py`:

Imports adicionales:
```python
from agent_core.domain import EntityKind
from agent_core.registry.entities import content_hash, encode_entity, version_ref
from agent_core.registry.evaluation.ports import EvalTarget
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import (
    AliasChange, Approval, EntityInRelease, ReleaseDetail, StoredRelease, StoredVersion,
)
from agent_core.registry.roles import require_approver
from agent_core.registry.snapshot import SnapshotRegistry
```

Métodos:
```python
    # --- evaluación --------------------------------------------------------------------------------------

    def _suite(self, tx: RegistryTx, cand: Candidate, suite_id: str, version: str | None) -> EvalSuite:
        for suite in cand.suites:
            if suite.id == suite_id and version in (None, suite.version):
                return suite
        versions = sorted(tx.list_versions("eval_suite", suite_id),
                          key=lambda v: tuple(int(x) for x in v.ref.version.split(".")))
        chosen = [v for v in versions if version in (None, v.ref.version)]
        if not chosen:
            raise RegistryError(RegistryErrorCode.not_found, f"no existe la suite {suite_id}")
        entity = self._load(tx, chosen[-1].ref)
        assert isinstance(entity, EvalSuite)
        return entity

    def evaluate(self, actor: Principal, proposal_id: str, suite_id: str,
                 suite_version: str | None = None) -> EvalReport:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            self._expect(p, ProposalState.candidate)
            cand = self._candidate(tx, p)
            if cand.candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed, "la candidata cambió desde freeze")
            suite = self._suite(tx, cand, suite_id, suite_version)
            base_release, base_entities = self._base(tx, p.base_release_id)
        candidate_target = EvalTarget("candidate", cand.release, SnapshotRegistry(cand.release, cand.entities))
        base_target = (EvalTarget("base", base_release, SnapshotRegistry(base_release, base_entities))
                       if base_release is not None else None)
        report = self._evaluator.run(suite, candidate_target, base_target)  # fuera de la transacción

        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            if p.state is not ProposalState.candidate or p.candidate_hash != cand.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed, "la propuesta cambió durante la evaluación")
            tx.insert_eval_run(EvalRun(
                eval_run_id=self._ids.new_id(IdKind.eval_run), proposal_id=proposal_id,
                candidate_hash=cand.candidate_hash, base_release_id=p.base_release_id,
                suite=version_ref(suite), verdict=report.verdict, report=report, at=self._clock.now()))
            if report.verdict == "pass":
                p = self._save(tx, p, state=ProposalState.evaluated)
            elif report.verdict == "fail":
                p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "evaluated", actor, p)
        if report.verdict == "fail":
            raise RegistryError(RegistryErrorCode.gate_failed, "la candidata no pasa el gate",
                                payload=report.model_dump(mode="json"))
        return report

    # --- decisiones humanas ------------------------------------------------------------------------------

    def approve(self, actor: Principal, proposal_id: str, candidate_hash: str) -> Approval:
        require_approver(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.evaluated)
            if candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed, "la candidata aprobada no es la vigente")
            run = tx.latest_eval_run(proposal_id, candidate_hash)
            if run is None or run.verdict != "pass":
                raise RegistryError(RegistryErrorCode.gate_failed, "no hay una evaluación aprobada vigente")
            approval = Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=actor_id(actor),
                                decision="approved", at=self._clock.now())
            tx.insert_approval(approval)
            p = self._save(tx, p, state=ProposalState.approved)
            self._event(tx, "approved", actor, p)
            return approval

    def reject(self, actor: Principal, proposal_id: str, reason: str) -> Proposal:
        require_approver(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.evaluated, ProposalState.approved)
            tx.insert_approval(Approval(proposal_id=proposal_id, candidate_hash=p.candidate_hash or "",
                                        actor=actor_id(actor), decision="rejected", reason=reason[:2000],
                                        at=self._clock.now()))
            self._event(tx, "rejected", actor, p)
            return self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)

    def publish(self, actor: Principal, proposal_id: str, idempotency_key: str) -> ReleaseDetail:
        require_approver(actor)
        stale = False
        with self._store.transaction() as tx:
            prior = tx.get_publish_key(idempotency_key)
            if prior is not None:
                if prior[0] != proposal_id:
                    raise RegistryError(RegistryErrorCode.illegal_transition,
                                        "la Idempotency-Key ya se usó con otra propuesta")
                return self._detail(tx, prior[1])
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.approved)
            current = tx.get_alias(p.agent_id, "staging", for_update=True)
            if current != p.base_release_id:
                self._save(tx, p, state=ProposalState.draft, candidate_hash=None, base_release_id=current,
                           rev=p.rev + 1)
                self._event(tx, "proposal_staled", actor, p)
                stale = True
            else:
                release_id = self._publish_in_tx(tx, actor, p)
                tx.put_publish_key(idempotency_key, proposal_id, release_id)
                return self._detail(tx, release_id)
        assert stale
        raise RegistryError(RegistryErrorCode.proposal_stale,
                            "staging cambió desde que se creó la propuesta; congela y evalúa de nuevo")

    def _publish_in_tx(self, tx: RegistryTx, actor: Principal, p: Proposal) -> str:
        cand = self._candidate(tx, p)
        if cand.candidate_hash != p.candidate_hash:
            raise RegistryError(RegistryErrorCode.candidate_changed, "la candidata cambió desde la aprobación")
        approval = tx.latest_approval(p.proposal_id, cand.candidate_hash)
        if approval is None or approval.decision != "approved":
            raise RegistryError(RegistryErrorCode.gate_failed, "falta una aprobación vigente")
        release_id = release_id_for(cand.candidate_hash)
        if tx.get_release(release_id) is not None:
            raise RegistryError(RegistryErrorCode.illegal_transition, "esa release ya existe")
        now, who = self._clock.now(), actor_id(actor)
        by_ref = {version_ref(e): e for e in [*cand.entities, *cand.suites]}
        for ref in cand.new_versions:
            entity = by_ref[ref]
            tx.blobs.put(encode_entity(entity))
            tx.insert_version(StoredVersion(ref=ref, content_hash=content_hash(entity),
                                            docs=cand.docs[ref], proposal_id=p.proposal_id,
                                            created_by=p.created_by, created_at=now))
        release = cand.release.model_copy(update={"id": release_id})
        refs = sorted(by_ref, key=str)
        tx.insert_release(StoredRelease(release=release, release_hash=cand.release_hash, agent_id=p.agent_id,
                                        agent_version=cand.agent_version, base_release_id=p.base_release_id,
                                        proposal_id=p.proposal_id, published_by=who, published_at=now), refs)
        tx.set_alias(AliasChange(agent_id=p.agent_id, alias="staging", before=p.base_release_id,
                                 after=release_id, actor=who, reason=f"publica {p.proposal_id}", at=now))
        self._save(tx, p, state=ProposalState.published)
        self._event(tx, "published", actor, p, release_id)
        return release_id

    def promote(self, actor: Principal, agent_id: str, alias: str, release_id: str,
                reason: str = "") -> AliasChange:
        require_approver(actor)
        with self._store.transaction() as tx:
            stored = tx.get_release(release_id)
            if stored is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            if tx.release_status(release_id) != "active":
                raise RegistryError(RegistryErrorCode.illegal_transition, "no se promueve una release revocada")
            if agent_id not in stored.release.entities.get(EntityKind.agent, {}):
                raise RegistryError(RegistryErrorCode.illegal_transition, "la release no contiene al agente")
            change = AliasChange(agent_id=agent_id, alias=alias,
                                 before=tx.get_alias(agent_id, alias, for_update=True), after=release_id,
                                 actor=actor_id(actor), reason=reason[:500], at=self._clock.now())
            tx.set_alias(change)
            self._event(tx, "promoted", actor, release_id=release_id)
            return change

    def revoke(self, actor: Principal, release_id: str, reason: str) -> ReleaseDetail:
        require_approver(actor)
        with self._store.transaction() as tx:
            if tx.get_release(release_id) is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            if tx.release_status(release_id) != "active":
                raise RegistryError(RegistryErrorCode.illegal_transition, "la release ya está revocada")
            if any(alias == "prod" for _, alias in tx.aliases_to(release_id)):
                raise RegistryError(RegistryErrorCode.illegal_transition,
                                    "prod apunta a esta release: promueve otra antes de revocarla")
            tx.set_release_status(release_id, "revoked")
            self._event(tx, "revoked", actor, release_id=release_id)
            return self._detail(tx, release_id)

    # --- lecturas usadas por las decisiones ---------------------------------------------------------------

    def _detail(self, tx: RegistryTx, release_id: str) -> ReleaseDetail:
        stored = tx.get_release(release_id)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
        base_refs = set(tx.release_refs(stored.base_release_id)) if stored.base_release_id else set()
        entities = []
        for ref in tx.release_refs(release_id):
            version = tx.get_version(ref)
            assert version is not None
            entities.append(EntityInRelease(ref=ref, content_hash=version.content_hash, docs=version.docs,
                                            changed_vs_base=bool(stored.base_release_id) and ref not in base_refs))
        status = tx.release_status(release_id) or "active"
        ks = stored.release.knowledge_snapshot
        return ReleaseDetail(release_id=release_id, status=status, agent_id=stored.agent_id, entities=entities,
                             knowledge_snapshot=str(ks) if ks else None, proposal_id=stored.proposal_id,
                             base_release_id=stored.base_release_id, published_by=stored.published_by,
                             published_at=stored.published_at)

    def get_release(self, release_id: str) -> ReleaseDetail:
        with self._store.transaction() as tx:
            return self._detail(tx, release_id)
```
> - En `publish`, la rama `stale` guarda el cambio de estado y **sale** de la transacción sin excepción (commit). Después lanza `proposal_stale`, porque lanzar dentro revertiría el rebase de la propuesta.
> - Ojo: `self._save` devuelve el modelo nuevo; en la rama `stale` el evento usa `p` anterior. Es intencional: registra el `candidate_hash` que quedó obsoleto.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/service.py tests/registry/test_service_decide.py
git commit -m "feat(registry): evaluar, aprobar, rechazar, publicar, promover y revocar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Lecturas, diff y linaje (T-REG-21, 22)

**Files:**
- Modify: `agent_core/registry/service.py`
- Test: `tests/registry/test_service_reads.py`

**Interfaces:**
- Produces, en `RegistryService`:
  - `get_entity(kind, entity_id, version: str | None = None) -> EntityVersion` (la última si `version` es `None`).
  - `list_versions(kind, entity_id) -> list[VersionSummary]`, ordenadas por semver.
  - `diff_releases(a, b) -> ReleaseDiff`.
  - `lineage_for_run(actor, run_id) -> RunLineage`. Exige un principal identificado. Si `runs` es `None` o el run no existe, `not_found`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_service_reads.py`:
```python
import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.service import RegistryService
from tests.registry.helpers import AGENT, human, prompt_draft
from tests.registry.service_world import SUITE, World

ANA = human()


class Runs:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def release_of(self, run_id: str) -> str | None:
        return self.mapping.get(run_id)


def _published(w: World) -> str:
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "t")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, p.proposal_id, h)
    return w.service.publish(ANA, p.proposal_id, "k").release_id


def test_diff_matches_release_entities() -> None:  # T-REG-22
    w = World()
    rel = _published(w)
    diff = w.service.diff_releases("rel-demo", rel)
    assert {(c.before.id, c.before.version, c.after.version) for c in diff.changed} == {
        ("p/resumen_radicado", "1.0.0", "1.1.0"), ("disputa-cargo", "1.0.0", "1.0.1"), (AGENT, "1.0.0", "1.0.1")}
    assert [r.id for r in diff.added] == ["disputas-suite"] and diff.removed == []
    assert next(c for c in diff.changed if c.after.id == "p/resumen_radicado").docs.rationale


def test_lineage_matches_release_exactly() -> None:  # T-REG-21
    w = World()
    rel = _published(w)
    service = RegistryService(w.store, w.evaluator, w.clock, w.ids, runs=Runs({"run-9": rel}))
    lineage = service.lineage_for_run(ANA, "run-9")
    detail = service.get_release(rel)
    assert [e.ref for e in lineage.entities] == [e.ref for e in detail.entities]
    assert lineage.approved_by == "ana" and lineage.built_by == "ana" and lineage.eval_verdict == "pass"
    assert any(e.changed_vs_base and e.ref.id == "p/resumen_radicado" for e in lineage.entities)


def test_lineage_unknown_run_is_not_found() -> None:
    w = World()
    service = RegistryService(w.store, w.evaluator, w.clock, w.ids, runs=Runs({}))
    with pytest.raises(RegistryError) as info:
        service.lineage_for_run(ANA, "run-x")
    assert info.value.code is RegistryErrorCode.not_found


def test_entity_and_versions() -> None:
    w = World()
    _published(w)
    latest = w.service.get_entity("prompt", "p/resumen_radicado")
    assert latest.ref.version == "1.1.0" and latest.content["id"] == "p/resumen_radicado"
    assert [v.ref.version for v in w.service.list_versions("prompt", "p/resumen_radicado")] == ["1.0.0", "1.1.0"]
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_service_reads.py -v`
Expected: FAIL con `AttributeError: 'RegistryService' object has no attribute 'diff_releases'`.

- [ ] **Step 3: Implementar.** Agrega a `service.py` (con imports de `ChangedRef`, `EntityVersion`, `ReleaseDiff`, `RunLineage`, `VersionSummary`, `loads` desde `agent_core.domain` y `parse_semver`):
```python
    def _sorted_versions(self, tx: RegistryTx, kind: str, entity_id: str) -> list[StoredVersion]:
        return sorted(tx.list_versions(kind, entity_id), key=lambda v: parse_semver(v.ref.version) or (0, 0, 0))

    def get_entity(self, kind: str, entity_id: str, version: str | None = None) -> EntityVersion:
        with self._store.transaction() as tx:
            versions = [v for v in self._sorted_versions(tx, kind, entity_id) if version in (None, v.ref.version)]
            if not versions:
                raise RegistryError(RegistryErrorCode.not_found, f"no existe {kind}:{entity_id}")
            v = versions[-1]
            content = loads(tx.blobs.get(v.content_hash))
            assert isinstance(content, dict)
            return EntityVersion(ref=v.ref, content=content, content_hash=v.content_hash, docs=v.docs,
                                 created_by=v.created_by, created_at=v.created_at)

    def list_versions(self, kind: str, entity_id: str) -> list[VersionSummary]:
        with self._store.transaction() as tx:
            return [VersionSummary(ref=v.ref, content_hash=v.content_hash, docs=v.docs, created_by=v.created_by,
                                   created_at=v.created_at) for v in self._sorted_versions(tx, kind, entity_id)]

    def diff_releases(self, a: str, b: str) -> ReleaseDiff:
        with self._store.transaction() as tx:
            for rid in (a, b):
                if tx.get_release(rid) is None:
                    raise RegistryError(RegistryErrorCode.not_found, f"no existe la release {rid}")
            left = {(r.kind, r.id): r for r in tx.release_refs(a)}
            right = {(r.kind, r.id): r for r in tx.release_refs(b)}
            changed = []
            for key in sorted(left.keys() & right.keys()):
                if left[key] != right[key]:
                    stored = tx.get_version(right[key])
                    assert stored is not None
                    changed.append(ChangedRef(before=left[key], after=right[key], docs=stored.docs))
            return ReleaseDiff(a=a, b=b, added=[right[k] for k in sorted(right.keys() - left.keys())],
                               removed=[left[k] for k in sorted(left.keys() - right.keys())], changed=changed)

    def lineage_for_run(self, actor: Principal, run_id: str) -> RunLineage:
        actor_id(actor)
        release_id = self._runs.release_of(run_id) if self._runs is not None else None
        if release_id is None:
            raise RegistryError(RegistryErrorCode.not_found, "el run no existe")
        with self._store.transaction() as tx:
            detail = self._detail(tx, release_id)
            stored = tx.get_release(release_id)
            assert stored is not None
            verdict = built_by = approved_by = None
            if stored.proposal_id is not None:
                p = tx.get_proposal(stored.proposal_id)
                built_by = p.created_by if p else None
                h = p.candidate_hash if p else None
                if h is not None:
                    run = tx.latest_eval_run(stored.proposal_id, h)
                    approval = tx.latest_approval(stored.proposal_id, h)
                    verdict = run.verdict if run else None
                    approved_by = approval.actor if approval and approval.decision == "approved" else None
            return RunLineage(run_id=run_id, release_id=release_id, entities=detail.entities,
                              knowledge_snapshot=detail.knowledge_snapshot, proposal_id=stored.proposal_id,
                              eval_verdict=verdict, built_by=built_by, approved_by=approved_by,
                              published_at=stored.published_at)
```
> Para que `lineage_for_run` encuentre `candidate_hash` después de publicar, `_publish_in_tx` **conserva** `candidate_hash` en la propuesta publicada: `self._save(tx, p, state=ProposalState.published)` no lo borra. Verifícalo.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/service.py tests/registry/test_service_reads.py
git commit -m "feat(registry): lecturas, diff entre releases y linaje por run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Importación y exportación YAML (T-REG-26)

**Files:**
- Create: `agent_core/registry/yaml_io.py`
- Modify: `agent_core/registry/service.py` (`import_seed`, `export`)
- Test: `tests/registry/test_yaml_io.py`

**Interfaces:**
- Consumes: `load_registry`, `validate_registry`, `pin_release`, `load_yaml` (M1); `DIRS` no se exporta desde M1, así que se replica el mapa de carpetas en `yaml_io` y una prueba verifica que coincide con los tipos de M0.
- Produces:
  - `yaml_io.FOLDERS: Mapping[str, str]` (kind → carpeta, más `eval_suite → eval_suites`).
  - `yaml_io.load_seed(root: Path) -> tuple[list[PinnedRelease], list[EvalSuite]]`. Lanza `RegistryError(validation_failed)` con las violaciones.
  - `yaml_io.dump_entities(entities: Iterable[AnyEntity]) -> dict[str, bytes]` (ruta relativa → YAML).
  - `RegistryService.import_seed(actor, root: Path) -> list[ReleaseDetail]`: exige aprobador humano, falla si el agente ya tiene `staging` y apunta `staging` y `prod`.
  - `RegistryService.export(release_id: str) -> dict[str, bytes]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_yaml_io.py`:
```python
from pathlib import Path

import pytest

from agent_core.domain import EntityKind
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.service import RegistryService
from agent_core.registry.yaml_io import FOLDERS, dump_entities, load_seed
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, REGISTRY_DEMO, bot, demo_pinned, human
from tests.registry.service_world import FakeEvaluator


def _service() -> tuple[RegistryService, InMemoryRegistryStore]:
    store = InMemoryRegistryStore()
    return RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds()), store


def test_folders_cover_every_kind() -> None:
    assert set(FOLDERS) == {k.value for k in EntityKind} | {"eval_suite"}


def test_import_demo_creates_published_release_with_both_aliases() -> None:  # T-REG-26
    service, store = _service()
    [detail] = service.import_seed(human(), REGISTRY_DEMO)
    with store.transaction() as tx:
        assert tx.get_alias(AGENT, "staging") == tx.get_alias(AGENT, "prod") == detail.release_id
        assert tx.events()[-1].type == "imported"
    assert detail.status == "active" and detail.proposal_id is None


def test_import_twice_fails() -> None:
    service, _ = _service()
    service.import_seed(human(), REGISTRY_DEMO)
    with pytest.raises(RegistryError) as info:
        service.import_seed(human(), REGISTRY_DEMO)
    assert info.value.code is RegistryErrorCode.illegal_transition


def test_import_requires_human_approver() -> None:
    service, _ = _service()
    with pytest.raises(RegistryError) as info:
        service.import_seed(bot("constructor", "aprobador"), REGISTRY_DEMO)
    assert info.value.code is RegistryErrorCode.forbidden_role


def test_export_then_load_keeps_content_hashes(tmp_path: Path) -> None:  # T-REG-26
    service, _ = _service()
    [detail] = service.import_seed(human(), REGISTRY_DEMO)
    files = service.export(detail.release_id)
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    [pinned], _ = load_seed(tmp_path)
    assert {e.id: e.version for e in pinned.entities} == {e.id: e.version for e in demo_pinned().entities}
    again, _ = _service()
    [detail2] = again.import_seed(human(), tmp_path)
    assert [e.content_hash for e in detail2.entities] == [e.content_hash for e in detail.entities]


def test_dump_is_deterministic() -> None:
    entities = demo_pinned().entities
    assert dump_entities(entities) == dump_entities(list(reversed(entities)))
```
> `export` escribe `releases/<release_id>.yaml` con referencias exactas y aliases `[staging, prod]` del agente, para que `load_seed` pueda volver a fijarla.

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_yaml_io.py -v`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`agent_core/registry/yaml_io.py`:
```python
"""Importación y exportación YAML (spec §5.6). Formato del loader de M1; nunca es fuente de verdad."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agent_core.domain import EntityKind, Release, SchemaError
from agent_core.flows import PinnedRelease, load_registry, load_yaml, pin_release, validate_registry
from agent_core.registry.entities import AnyEntity, entity_kind
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.suite import EvalSuite

FOLDERS: Mapping[str, str] = MappingProxyType({
    "agent": "agents", "flow": "flows", "policy": "policies", "template": "templates", "prompt": "prompts",
    "tool": "tools", "decision_model": "decision_models", "model_profile": "model_profiles",
    "language_detection": "language_detection", "injection_ruleset": "injection_rulesets",
    "knowledge_snapshot": "knowledge_snapshots", "eval_suite": "eval_suites",
})


def _yaml(data: Any) -> bytes:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=True, default_flow_style=False).encode("utf-8")


def dump_entities(entities: Iterable[AnyEntity]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for e in sorted(entities, key=lambda x: (entity_kind(x), x.id, x.version)):
        path = f"{FOLDERS[entity_kind(e)]}/{e.id}@{e.version}.yaml"
        out[path] = _yaml(e.model_dump(mode="json", by_alias=True, exclude_none=True))
    return out


def dump_release(release: Release, agent_id: str) -> dict[str, bytes]:
    data: dict[str, Any] = {
        "id": release.id,
        "agents": [{"agent": f"{agent_id}@{release.entities[EntityKind.agent][agent_id]}",
                    "aliases": ["staging", "prod"]}],
        "flows": [f"{i}@{v}" for i, v in sorted(release.entities.get(EntityKind.flow, {}).items())],
        "interrupts": [i.model_dump(mode="json", by_alias=True, exclude_none=True) for i in release.interrupts],
        "language_detection": str(release.language_detection),
        "max_input_chars": release.max_input_chars,
    }
    if release.injection_ruleset is not None:
        data["injection_ruleset"] = str(release.injection_ruleset)
    if release.knowledge_snapshot is not None:
        data["knowledge"] = str(release.knowledge_snapshot)
    return {f"releases/{release.id}.yaml": _yaml(data)}


def _suites(root: Path) -> list[EvalSuite]:
    folder = root / FOLDERS["eval_suite"]
    if not folder.is_dir():
        return []
    return [EvalSuite.model_validate(load_yaml(p.read_bytes())) for p in sorted(folder.rglob("*.yaml"))]


def load_seed(root: Path) -> tuple[list[PinnedRelease], list[EvalSuite]]:
    reg, violations = load_registry(root)
    problems = [*violations, *validate_registry(reg)]
    if problems:
        raise RegistryError(RegistryErrorCode.validation_failed, "el directorio no es un registro válido",
                            payload=[{"rule": v.rule, "path": v.path, "message": v.message}
                                     for v in problems[:50]])
    try:
        pinned = [pin_release(reg, decl.id) for decl in reg.releases()]
        return pinned, _suites(root)
    except SchemaError as exc:
        raise RegistryError(RegistryErrorCode.validation_failed, str(exc)[:500]) from None
```
> Si `load_yaml` recibe una ruta o un `str` en lugar de `bytes`, ajusta la llamada a su firma real en `agent_core/flows/yaml_loader.py`. Si `load_registry` rechaza la carpeta desconocida `eval_suites/`, excluye esa carpeta antes de cargar (por ejemplo, copiando el resto a una carpeta temporal) o pregunta.

En `service.py`:
```python
    def import_seed(self, actor: Principal, root: Path) -> list[ReleaseDetail]:
        require_approver(actor)
        pinned_list, suites = load_seed(root)
        details: list[ReleaseDetail] = []
        with self._store.transaction() as tx:
            now, who = self._clock.now(), actor_id(actor)
            seed_docs = VersionDocs(description="Importado desde YAML", rationale="semilla", changelog="")
            for suite in suites:
                self._insert_if_new(tx, suite, seed_docs, None, who, now)
            for pinned in pinned_list:
                if len(pinned.aliases) != 1:
                    raise RegistryError(RegistryErrorCode.validation_failed,
                                        "una release por agente en esta entrega")
                [agent_id] = list(pinned.aliases)
                if tx.get_alias(agent_id, "staging") is not None:
                    raise RegistryError(RegistryErrorCode.illegal_transition,
                                        f"el agente {agent_id} ya tiene releases; usa una propuesta")
                refs = [self._insert_if_new(tx, e, seed_docs, None, who, now) for e in pinned.entities]
                refs += [version_ref(s) for s in suites if s.agent_id == agent_id]
                digest = release_hash(pinned.release)
                release_id = "rel-" + digest[:16]
                release = pinned.release.model_copy(update={"id": release_id})
                tx.insert_release(StoredRelease(
                    release=release, release_hash=digest, agent_id=agent_id,
                    agent_version=release.entities[EntityKind.agent][agent_id], base_release_id=None,
                    proposal_id=None, published_by=who, published_at=now), sorted(refs, key=str))
                for alias in ("staging", "prod"):
                    tx.set_alias(AliasChange(agent_id=agent_id, alias=alias, before=None, after=release_id,
                                             actor=who, reason="importación inicial", at=now))
                self._event(tx, "imported", actor, release_id=release_id)
                details.append(self._detail(tx, release_id))
        return details

    def _insert_if_new(self, tx: RegistryTx, entity: AnyEntity, docs: VersionDocs, proposal_id: str | None,
                       who: str, now: datetime) -> VersionRef:
        ref, digest = version_ref(entity), content_hash(entity)
        stored = tx.get_version(ref)
        if stored is None:
            tx.blobs.put(encode_entity(entity))
            tx.insert_version(StoredVersion(ref=ref, content_hash=digest, docs=docs, proposal_id=proposal_id,
                                            created_by=who, created_at=now))
        elif stored.content_hash != digest:
            raise RegistryError(RegistryErrorCode.validation_failed, f"{ref} ya existe con otro contenido")
        return ref

    def export(self, release_id: str) -> dict[str, bytes]:
        with self._store.transaction() as tx:
            stored = tx.get_release(release_id)
            if stored is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            entities = [self._load(tx, ref) for ref in tx.release_refs(release_id)]
        return {**dump_entities(entities), **dump_release(stored.release, stored.agent_id)}
```
> Imports nuevos en `service.py`: `datetime`, `Path`, `VersionDocs`, `release_hash` (de `candidate`), `load_seed`, `dump_entities`, `dump_release` (de `yaml_io`).


- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry/yaml_io.py agent_core/registry/service.py tests/registry/test_yaml_io.py
git commit -m "feat(registry): importación inicial y exportación YAML determinista

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `ScenarioEvaluator` y `LocalSandbox` (T-REG-23, 24, 11)

**Files:**
- Create: `agent_core/registry/evaluation/evaluator.py`, `agent_core/registry/evaluation/local_sandbox.py`
- Modify: `agent_core/registry/evaluation/__init__.py`, `agent_core/registry/__init__.py` (interfaz pública completa)
- Test: `tests/registry/test_evaluator.py`, `tests/registry/test_local_sandbox.py`

**Interfaces:**
- Consumes: puertos (Task 8), `score_run`, `aggregate`, `decide` (Task 7), `SnapshotRegistry` (Task 6).
- Produces:
  - `ScenarioEvaluator(harness: ScenarioHarness, sandbox: SandboxPort, *, max_workers: int = 4, judge: Judge | None = None)` implementa `EvalPort`.
  - `LocalSandbox(ids: IdSource)` implementa `SandboxPort`. Sus tools: `LocalSandboxTools` con `is_sandbox = True`. `execute` repite la respuesta guardada cuando se llama con la misma `idempotency_key` (read-back) y, si no, consume la cola sembrada de esa tool (la última respuesta se repite). `definition` lee el `ToolDef` desde el `RegistryPort` del objetivo.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/registry/test_local_sandbox.py`:
```python
from agent_core.domain import EntityRef
from agent_core.ports import ToolCallContext
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalTarget
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import SandboxSeed
from testing.builders import principal
from testing.fakes.ids import FakeIds
from tests.registry.helpers import demo_pinned


def _target() -> EvalTarget:
    pinned = demo_pinned()
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))


CTX = ToolCallContext(run_id="r", release="demo", principal=principal())
TOOL = EntityRef(id="buscar_transacciones", version="1.0.0")


def test_tools_are_marked_as_sandbox_and_serve_seeded_replies() -> None:
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(SandboxSeed.model_validate(
        {"tools": {"buscar_transacciones": [{"result": [1]}, {"result": [2]}]}}), _target())
    tools = sandbox.tools(handle)
    assert getattr(tools, "is_sandbox", False) is True
    assert [tools.execute(TOOL, {}, {}, CTX).result_full for _ in range(3)] == [[1], [2], [2]]
    assert tools.definition(TOOL).id == "buscar_transacciones"


def test_same_idempotency_key_replays_the_write() -> None:
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(SandboxSeed.model_validate(
        {"tools": {"radicar_pqr": [{"result": {"id": "pqr-1"}}, {"result": {"id": "pqr-2"}}]}}), _target())
    tools = sandbox.tools(handle)
    radicar = EntityRef(id="radicar_pqr", version="1.0.0")
    first = tools.execute(radicar, {}, {}, CTX, idempotency_key="k1")
    again = tools.execute(radicar, {}, {}, CTX, idempotency_key="k1")
    assert first.result_full == again.result_full == {"id": "pqr-1"}


def test_unseeded_tool_is_an_error_not_a_crash() -> None:
    sandbox = LocalSandbox(FakeIds())
    tools = sandbox.tools(sandbox.provision(SandboxSeed(), _target()))
    assert tools.execute(TOOL, {}, {}, CTX).status.value == "error"


def test_each_provision_is_isolated() -> None:  # T-REG-23 (sandbox)
    sandbox = LocalSandbox(FakeIds())
    seed = SandboxSeed.model_validate({"tools": {"buscar_transacciones": [{"result": [1]}, {"result": [2]}]}})
    a, b = sandbox.tools(sandbox.provision(seed, _target())), sandbox.tools(sandbox.provision(seed, _target()))
    assert a.execute(TOOL, {}, {}, CTX).result_full == [1]
    assert b.execute(TOOL, {}, {}, CTX).result_full == [1]
```

`tests/registry/test_evaluator.py`:
```python
from dataclasses import dataclass, field

import pytest

from agent_core.domain import EngineEvent, Outcome, RunClosed, RunClosedPayload
from agent_core.ports import ToolExecutor
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalTarget, HarnessUnavailable, SandboxHandle
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario
from testing.builders import NOW
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, demo_pinned, suite_content


def _closed(outcome: str) -> list[EngineEvent]:
    return [RunClosed(event_id="e", run_id="r", release="x", ts=NOW,
                      payload=RunClosedPayload(outcome=Outcome(outcome), closed_by="flow"))]


@dataclass
class FakeHarness:
    outcomes: dict[str, str]  # label -> outcome
    fail: bool = False
    seen: list[tuple[str, str, int]] = field(default_factory=list)

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario, tools: ToolExecutor) -> list[EngineEvent]:
        assert getattr(tools, "is_sandbox", False)
        self.seen.append((target.label, scenario.id, id(tools)))
        if self.fail:
            raise HarnessUnavailable("gateway caído")
        return _closed(self.outcomes[target.label])


def _targets() -> tuple[EvalTarget, EvalTarget]:
    pinned = demo_pinned()
    reg = SnapshotRegistry(pinned.release, pinned.entities)
    return EvalTarget("candidate", pinned.release, reg), EvalTarget("base", pinned.release, reg)


SUITE = EvalSuite.model_validate(suite_content())  # 1 escenario, 2 repeticiones, espera resolved


def test_candidate_better_than_base_passes() -> None:
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "failed"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "pass"
    assert report.candidate is not None and str(report.candidate.primary) == "1.0000"
    assert len(report.results) == 4


def test_candidate_worse_than_base_fails() -> None:
    cand, base = _targets()
    report = ScenarioEvaluator(FakeHarness({"candidate": "failed", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "fail"


def test_each_run_gets_its_own_sandbox() -> None:  # T-REG-23
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(SUITE, cand, base)
    assert len({tools_id for _, _, tools_id in harness.seen}) == 4


def test_infra_failure_is_failed_infra() -> None:  # T-REG-11
    cand, base = _targets()
    report = ScenarioEvaluator(FakeHarness({}, fail=True), LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "failed_infra" and report.checks == []


class NotSandbox:
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        return SandboxHandle("h")

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        return object()  # type: ignore[return-value]

    def teardown(self, handle: SandboxHandle) -> None:
        pass


def test_refuses_non_sandbox_tools() -> None:  # T-REG-24
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    with pytest.raises(PermissionError):
        ScenarioEvaluator(harness, NotSandbox()).run(SUITE, cand, base)
    assert harness.seen == []


def test_without_base_compares_to_floor() -> None:
    cand, _ = _targets()
    report = ScenarioEvaluator(FakeHarness({"candidate": "resolved"}), LocalSandbox(FakeIds())).run(SUITE, cand, None)
    assert report.verdict == "pass" and report.base is None
    assert AGENT == SUITE.agent_id
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/registry/test_evaluator.py tests/registry/test_local_sandbox.py -v`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`agent_core/registry/evaluation/local_sandbox.py`:
```python
"""`LocalSandbox` (spec §6.3, respaldo de la entrega): respuestas sembradas por tool, aisladas por corrida."""

from collections import deque

from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus
from agent_core.registry.evaluation.ports import EvalTarget, SandboxHandle
from agent_core.registry.suite import SandboxSeed, ToolReply


class LocalSandboxTools:
    is_sandbox = True

    def __init__(self, seed: SandboxSeed, target: EvalTarget, ids: IdSource) -> None:
        self._queues = {tool: deque(replies) for tool, replies in seed.tools.items()}
        self._target, self._ids = target, ids
        self._writes: dict[tuple[str, str], ToolReply] = {}

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        call_id = self._ids.new_id(IdKind.call)
        if idempotency_key is not None and (tool.id, idempotency_key) in self._writes:
            reply = self._writes[(tool.id, idempotency_key)]
        else:
            queue = self._queues.get(tool.id)
            if not queue:
                return ToolResult(status=ToolStatus("error"), call_id=call_id, error="sin respuesta sembrada")
            reply = queue.popleft() if len(queue) > 1 else queue[0]
            if idempotency_key is not None and reply.status == ToolStatus("ok"):
                self._writes[(tool.id, idempotency_key)] = reply
        return ToolResult(status=reply.status, result_full=reply.result, error=reply.error, call_id=call_id,
                          source="sandbox")

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._target.registry.get(tool, ToolDef)


class LocalSandbox:
    def __init__(self, ids: IdSource) -> None:
        self._ids = ids
        self._tools: dict[str, LocalSandboxTools] = {}

    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        handle = SandboxHandle(self._ids.new_id(IdKind.run))
        self._tools[handle.handle_id] = LocalSandboxTools(seed, target, self._ids)
        return handle

    def tools(self, handle: SandboxHandle) -> LocalSandboxTools:
        return self._tools[handle.handle_id]

    def teardown(self, handle: SandboxHandle) -> None:
        self._tools.pop(handle.handle_id, None)
```
> Si M3 hace el read-back con **otra** tool (`readback_by: idempotency_key` sobre `obtener_pqr`), el escenario siembra la respuesta de esa tool. `testing/engine_world.py::_register_tools` muestra el par `radicar_pqr`/`obtener_pqr` de la demo.

`agent_core/registry/evaluation/evaluator.py`:
```python
"""`ScenarioEvaluator` (spec §6.2): k corridas por escenario sobre candidata y base, en sandbox."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from agent_core.domain import EngineEvent
from agent_core.registry.evaluation.gate import decide
from agent_core.registry.evaluation.ports import (
    EvalTarget,
    HarnessUnavailable,
    Judge,
    SandboxPort,
    ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import EvalReport, ScenarioResult, SuiteMetrics
from agent_core.registry.evaluation.scoring import aggregate, score_run
from agent_core.registry.suite import EvalSuite, Scenario


@dataclass(frozen=True)
class _Job:
    target: EvalTarget
    scenario: Scenario
    repetition: int


class ScenarioEvaluator:
    def __init__(self, harness: ScenarioHarness, sandbox: SandboxPort, *, max_workers: int = 4,
                 judge: Judge | None = None) -> None:
        self._harness, self._sandbox = harness, sandbox
        self._max_workers, self._judge = max_workers, judge

    def _one(self, suite: EvalSuite, job: _Job) -> tuple[ScenarioResult, list[EngineEvent]]:
        handle = self._sandbox.provision(job.scenario.seed, job.target)
        try:
            tools = self._sandbox.tools(handle)
            if getattr(tools, "is_sandbox", False) is not True:
                raise PermissionError("el evaluador solo corre contra un sandbox")
            events = self._harness.run(job.target, suite.agent_id, job.scenario, tools)
        finally:
            self._sandbox.teardown(handle)
        score = score_run(events, job.scenario.expect, job.scenario.sensitive_values)
        return ScenarioResult(scenario_id=job.scenario.id, label=job.target.label, repetition=job.repetition,
                              score=score), events

    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport:
        targets = [candidate] if base is None else [candidate, base]
        jobs = [_Job(t, s, r) for t in targets for s in suite.scenarios for r in range(suite.repetitions)]
        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                done = list(pool.map(lambda job: self._one(suite, job), jobs))
        except (HarnessUnavailable, TimeoutError) as exc:
            return EvalReport(verdict="failed_infra", detail=type(exc).__name__ + ": " + str(exc)[:200])
        results = [r for r, _ in done]

        def metrics(label: str) -> SuiteMetrics:
            return aggregate([r.score for r in results if r.label == label])

        cand_m = metrics("candidate")
        base_m = metrics("base") if base is not None else None
        verdict, checks = decide(suite, cand_m, base_m)
        notes = []
        if self._judge is not None:
            transcripts = [ScenarioTranscript(r.scenario_id, r.label, r.repetition, ev) for r, ev in done]
            notes = self._judge.score(suite, transcripts)
        return EvalReport(verdict=verdict, checks=checks, candidate=cand_m, base=base_m, results=results,
                          judge_notes=notes)
```
> `PermissionError` no se captura: es un error de configuración y debe hacer ruido (T-REG-24). Si el juez falla, su excepción tampoco se captura en la entrega, porque el juez es opcional: si molesta, no lo configures.

`agent_core/registry/evaluation/__init__.py`:
```python
"""Evaluación por agente sobre escenarios (spec §6)."""

from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import (
    EvalPort, EvalTarget, HarnessUnavailable, Judge, SandboxHandle, SandboxPort, ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import (
    EvalReport, JudgeNote, MetricCheck, RunScore, ScenarioResult, SuiteMetrics, Verdict,
)

__all__ = [
    "EvalPort", "EvalReport", "EvalTarget", "HarnessUnavailable", "Judge", "JudgeNote", "LocalSandbox",
    "MetricCheck", "RunScore", "SandboxHandle", "SandboxPort", "ScenarioEvaluator", "ScenarioHarness",
    "ScenarioResult", "ScenarioTranscript", "SuiteMetrics", "Verdict",
]
```

`agent_core/registry/__init__.py`:
```python
"""Registry (unidad 2): entidades, versionado y publicación.

Spec: docs/specs/2026-09-29-registry-design.md (rev. 2). ADR 0017 y 0018. Otros módulos importan solo de aquí."""

from agent_core.registry.blobs import BlobStore, InMemoryBlobStore
from agent_core.registry.errors import HTTP_STATUS, IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.evaluation import (
    EvalPort, EvalReport, EvalTarget, HarnessUnavailable, Judge, LocalSandbox, SandboxHandle, SandboxPort,
    ScenarioEvaluator, ScenarioHarness,
)
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import EntityDraft, Origin, ProposalState, VersionDocs, VersionRef
from agent_core.registry.service import RegistryService, RunReleaseReader
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.store import RegistryStore
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario, Step

__all__ = [
    "HTTP_STATUS", "BlobStore", "EntityDraft", "EvalPort", "EvalReport", "EvalSuite", "EvalTarget",
    "HarnessUnavailable", "InMemoryBlobStore", "InMemoryRegistryStore", "IntegrityError", "Judge",
    "LocalSandbox", "Origin", "ProposalState", "RegistryError", "RegistryErrorCode", "RegistryService",
    "RegistryStore", "RunReleaseReader", "SandboxHandle", "SandboxPort", "SandboxSeed", "Scenario",
    "ScenarioEvaluator", "ScenarioHarness", "SnapshotRegistry", "Step", "VersionDocs", "VersionRef",
]
```

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/registry tests/registry/test_evaluator.py tests/registry/test_local_sandbox.py
git commit -m "feat(registry): evaluador de escenarios con sandbox local e interfaz pública

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Postgres: esquema, `PgRegistryStore` y `PostgresRegistry` (T-REG-01, 02, 16–20)

**Files:**
- Create: `agent_core/registry/postgres/__init__.py`, `agent_core/registry/postgres/schema.sql`, `agent_core/registry/postgres/store.py`, `agent_core/registry/postgres/runtime.py`
- Modify: `agent_core/registry/__init__.py` (exporta `PgRegistryStore`, `PostgresRegistry`, `apply_registry_schema`), `tests/integration/conftest.py` (fixtures del registry)
- Test: `tests/integration/test_registry_postgres.py`

**Interfaces:**
- Consumes: `RegistryTx`/`RegistryStore` (Task 5), modelos, `decode_entity`, `verified`.
- Produces:
  - `apply_registry_schema(conn, app_role: str | None = None) -> None`.
  - `PgRegistryStore(connect: Callable[[], psycopg.Connection])`. Cada transacción abre una conexión con `autocommit=False` y hace commit o rollback.
  - `PostgresRegistry(store: PgRegistryStore, clock: Clock, status_ttl: timedelta = timedelta(seconds=5))`, que implementa `RegistryPort`.

- [ ] **Step 1: Escribir el esquema**

`agent_core/registry/postgres/schema.sql`:
```sql
-- Registry (spec §3.2, ADR 0017): inmutabilidad impuesta por permisos y por triggers.
CREATE TABLE IF NOT EXISTS reg_blobs (hash char(64) PRIMARY KEY, bytes bytea NOT NULL);
CREATE TABLE IF NOT EXISTS reg_entity_versions (
    kind text NOT NULL, id text NOT NULL, version text NOT NULL,
    content_hash char(64) NOT NULL REFERENCES reg_blobs(hash),
    docs text NOT NULL, proposal_id text, created_by text NOT NULL, created_at timestamptz NOT NULL,
    PRIMARY KEY (kind, id, version));
CREATE TABLE IF NOT EXISTS reg_releases (
    release_id text PRIMARY KEY, release_json text NOT NULL, release_hash char(64) NOT NULL,
    agent_id text NOT NULL, agent_version text NOT NULL, base_release_id text, proposal_id text,
    published_by text NOT NULL, published_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS reg_releases_agent_version ON reg_releases (agent_id, agent_version, published_at);
CREATE TABLE IF NOT EXISTS reg_release_entities (
    release_id text NOT NULL REFERENCES reg_releases(release_id),
    kind text NOT NULL, id text NOT NULL, version text NOT NULL,
    PRIMARY KEY (release_id, kind, id),
    FOREIGN KEY (kind, id, version) REFERENCES reg_entity_versions(kind, id, version));
CREATE TABLE IF NOT EXISTS reg_approvals (
    seq bigserial PRIMARY KEY, proposal_id text NOT NULL, candidate_hash text NOT NULL, actor text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')), reason text, at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS reg_eval_runs (
    eval_run_id text PRIMARY KEY, seq bigserial UNIQUE, proposal_id text NOT NULL, candidate_hash text NOT NULL,
    base_release_id text, suite text NOT NULL, verdict text NOT NULL, report text NOT NULL, at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS reg_events (seq bigserial PRIMARY KEY, event_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_alias_log (seq bigserial PRIMARY KEY, change_json text NOT NULL);
-- mutables controladas
CREATE TABLE IF NOT EXISTS reg_release_status (
    release_id text PRIMARY KEY REFERENCES reg_releases(release_id),
    status text NOT NULL CHECK (status IN ('active', 'revoked')));
CREATE TABLE IF NOT EXISTS reg_aliases (
    agent_id text NOT NULL, alias text NOT NULL, release_id text NOT NULL REFERENCES reg_releases(release_id),
    PRIMARY KEY (agent_id, alias));
CREATE TABLE IF NOT EXISTS reg_proposals (proposal_id text PRIMARY KEY, proposal_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_proposal_changes (proposal_id text PRIMARY KEY, drafts_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_publish_keys (
    key text PRIMARY KEY, proposal_id text NOT NULL, release_id text NOT NULL);

CREATE OR REPLACE FUNCTION reg_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% es de solo inserción', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['reg_blobs', 'reg_entity_versions', 'reg_releases', 'reg_release_entities',
                             'reg_approvals', 'reg_eval_runs', 'reg_events', 'reg_alias_log'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I_no_update ON %I', t, t);
        EXECUTE format('CREATE TRIGGER %I_no_update BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION reg_immutable()', t, t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I_no_truncate ON %I', t, t);
        EXECUTE format('CREATE TRIGGER %I_no_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION reg_immutable()', t, t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
    END LOOP;
END $$;
```

- [ ] **Step 2: Escribir las pruebas de integración que fallan**

En `tests/integration/conftest.py`, agrega:
```python
REG_ROLE, REG_PASSWORD = "agentcore_registry_app", "registry-dev-only"


@pytest.fixture
def registry_store(admin_conn: "psycopg.Connection[Any]"):  # type: ignore[no-untyped-def]
    """`PgRegistryStore` con el rol de aplicación del registry (sin UPDATE/DELETE en tablas inmutables)."""
    from agent_core.registry.postgres.store import PgRegistryStore, apply_registry_schema

    admin_conn.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{REG_ROLE}') THEN "
        f"CREATE ROLE {REG_ROLE} LOGIN PASSWORD '{REG_PASSWORD}'; END IF; END $$")
    admin_conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {REG_ROLE}")
    apply_registry_schema(admin_conn, app_role=REG_ROLE)
    dsn = ADMIN_DSN.replace("agentcore:agentcore-dev-only", f"{REG_ROLE}:{REG_PASSWORD}")

    def connect() -> "psycopg.Connection[Any]":
        return psycopg.connect(dsn, autocommit=False, options=f"-c search_path={SCHEMA}")

    return PgRegistryStore(connect)
```

`tests/integration/test_registry_postgres.py`:
```python
"""Registry sobre Postgres real (spec §13). Requiere docker compose up -d postgres."""

from datetime import timedelta
from typing import Any

import psycopg
import pytest

from agent_core.domain import AgentSelector, EntityRef, Flow, KnowledgeSnapshot, Release, Template
from agent_core.registry.errors import IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.postgres.runtime import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore
from agent_core.registry.service import RegistryService
from testing.builders import principal
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.contracts.test_registry_contract import (
    EXACT_FLOW,
    KNOWLEDGE,
    RANGED_FLOW,
    check_get_exact_entity,
    check_get_knowledge_snapshot,
    check_get_with_non_exact_content_raises,
    check_resolve_release_by_alias_and_version,
)
from tests.registry.helpers import AGENT, REGISTRY_DEMO, human, prompt_draft
from tests.registry.service_world import SUITE, FakeEvaluator

pytestmark = pytest.mark.integration
ANA = human()


def _service(store: PgRegistryStore) -> RegistryService:
    return RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds())


def _publish(service: RegistryService, key: str = "k", version: str = "1.1.0") -> str:
    p = service.create_proposal(ANA, AGENT, Origin.manual, "t")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(version=version, text=f"Texto {version}."), SUITE],
                      expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, p.proposal_id, h)
    return service.publish(ANA, p.proposal_id, key).release_id


def test_immutable_tables_reject_update_and_delete(registry_store: PgRegistryStore,
                                                   admin_conn: "psycopg.Connection[Any]") -> None:  # T-REG-01
    _service(registry_store).import_seed(ANA, REGISTRY_DEMO)
    with registry_store.connect() as conn:  # rol de aplicación: sin permiso
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE reg_entity_versions SET created_by = 'x'")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM reg_releases")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # administrador: lo frena el trigger (42501)
        admin_conn.execute("UPDATE reg_entity_versions SET created_by = 'x'")


def test_publish_failure_mid_way_leaves_nothing(registry_store: PgRegistryStore) -> None:  # T-REG-02
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    registry_store.fail_on = lambda name: name == "set_alias"
    with pytest.raises(RuntimeError):
        _publish(service)
    registry_store.fail_on = None
    with registry_store.transaction() as tx:
        assert [s.ref.version for s in tx.list_versions("prompt", "p/resumen_radicado")] == ["1.0.0"]


def test_second_publish_on_same_agent_is_stale(registry_store: PgRegistryStore) -> None:  # T-REG-16
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    late = service.create_proposal(ANA, AGENT, Origin.manual, "tardía")  # base: la release importada
    service.put_draft(ANA, late.proposal_id, [prompt_draft(version="1.2.0", text="Otra."), SUITE], expected_rev=0)
    service.freeze(ANA, late.proposal_id)
    service.evaluate(ANA, late.proposal_id, "disputas-suite")
    h = service.get_proposal(late.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, late.proposal_id, h)
    _publish(service, "ka", "1.1.0")  # otra propuesta mueve staging primero
    with pytest.raises(RegistryError) as info:
        service.publish(ANA, late.proposal_id, "kb")
    assert info.value.code is RegistryErrorCode.proposal_stale


@pytest.fixture
def pg_registry(registry_store: PgRegistryStore) -> PostgresRegistry:
    """Contrato de M0: carga las entidades del contrato como publicadas (rel-1 en prod, rel-2 en 2.0.0)."""
    from agent_core.registry.entities import content_hash, encode_entity, version_ref
    from agent_core.registry.models import AliasChange, StoredRelease, StoredVersion, VersionDocs
    from testing.builders import NOW

    entities = [Flow.model_validate(EXACT_FLOW), Flow.model_validate(RANGED_FLOW),
                Template(id="t/saludo", version="1.0.0", locales={"es": "Hola"}), KNOWLEDGE]
    docs = VersionDocs(description="contrato", rationale="", changelog="")
    with registry_store.transaction() as tx:
        for e in entities:
            tx.blobs.put(encode_entity(e))
            tx.insert_version(StoredVersion(ref=version_ref(e), content_hash=content_hash(e), docs=docs,
                                            proposal_id=None, created_by="t", created_at=NOW))
        for rid, version in (("rel-1", "1.0.0"), ("rel-2", "2.0.0")):
            release = Release.model_validate({"id": rid, "status": "active", "language_detection": "lang@1.0.0"})
            tx.insert_release(StoredRelease(release=release, release_hash="h" * 64, agent_id="atencion",
                                            agent_version=version, base_release_id=None, proposal_id=None,
                                            published_by="t", published_at=NOW), [])
        tx.set_alias(AliasChange(agent_id="atencion", alias="prod", before=None, after="rel-1", actor="t",
                                 reason="r", at=NOW))
    return PostgresRegistry(registry_store, FakeClock())


def test_postgres_registry_passes_contract(pg_registry: PostgresRegistry) -> None:  # T-REG-17
    check_get_exact_entity(pg_registry)
    check_get_with_non_exact_content_raises(pg_registry)
    check_resolve_release_by_alias_and_version(pg_registry)
    check_get_knowledge_snapshot(pg_registry)
    with pytest.raises(KeyError):
        pg_registry.get(EntityRef.parse("flujo@9.9.9"), Flow)


def test_candidates_and_drafts_are_invisible(registry_store: PgRegistryStore) -> None:  # T-REG-18
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    p = service.create_proposal(ANA, AGENT, Origin.manual, "t")
    service.put_draft(ANA, p.proposal_id, [prompt_draft()], expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    reg = PostgresRegistry(registry_store, FakeClock())
    with pytest.raises(KeyError):
        from agent_core.domain import Prompt
        reg.get(EntityRef.parse("p/resumen_radicado@1.1.0"), Prompt)
    assert reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).entities


def test_tampered_blob_raises_integrity_error(registry_store: PgRegistryStore,
                                              admin_conn: "psycopg.Connection[Any]") -> None:  # T-REG-19
    _service(registry_store).import_seed(ANA, REGISTRY_DEMO)
    admin_conn.execute("ALTER TABLE reg_blobs DISABLE TRIGGER USER")
    admin_conn.execute("UPDATE reg_blobs SET bytes = 'x'::bytea")
    admin_conn.execute("ALTER TABLE reg_blobs ENABLE TRIGGER USER")
    reg = PostgresRegistry(registry_store, FakeClock())
    with pytest.raises(IntegrityError):
        from agent_core.domain import Prompt
        reg.get(EntityRef.parse("p/resumen_radicado@1.0.0"), Prompt)


def test_revoked_release_not_resolved_for_new_runs(registry_store: PgRegistryStore) -> None:  # T-REG-20, RF 5
    service = _service(registry_store)
    service.import_seed(ANA, REGISTRY_DEMO)
    rel = _publish(service)
    clock = FakeClock()
    reg = PostgresRegistry(registry_store, clock, status_ttl=timedelta(seconds=5))
    assert reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal()).id == rel
    service.revoke(ANA, rel, "falla en producción")
    clock.advance(timedelta(seconds=6))
    assert reg.release_status(rel) == "revoked"
    with pytest.raises(KeyError):
        reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal())
```
> - Si `FakeClock` no tiene `advance(timedelta)`, usa el método equivalente que tenga (revisa `testing/fakes/clock.py`).
> - `test_second_publish_on_same_agent_is_stale` ejercita el chequeo de base con `FOR UPDATE` sobre el alias. Una variante con dos hilos (`threading.Barrier`) es opcional.

- [ ] **Step 3: Correr y confirmar que fallan**

Run: `docker compose up -d postgres && AGENTCORE_REQUIRE_POSTGRES=1 uv run pytest tests/integration/test_registry_postgres.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.registry.postgres'`.

- [ ] **Step 4: Implementar**

`agent_core/registry/postgres/__init__.py`:
```python
"""Adaptadores Postgres del registry (ADR 0017)."""
```

`agent_core/registry/postgres/store.py`:
```python
"""`RegistryStore` sobre Postgres: una conexión por transacción, FOR UPDATE en propuesta y alias."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from typing import Any

import psycopg
from psycopg import sql

from agent_core.domain import Release, dumps, loads
from agent_core.registry.blobs import verified
from agent_core.registry.errors import IntegrityError
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import (
    AliasChange, Approval, EntityDraft, EvalRun, Proposal, RegistryEvent, StoredRelease, StoredVersion,
    VersionDocs, VersionRef,
)
from agent_core.registry.store import RegistryTx, Status

_INSERT_ONLY = ("reg_blobs", "reg_entity_versions", "reg_releases", "reg_release_entities", "reg_approvals",
                "reg_eval_runs", "reg_events", "reg_alias_log")
_MUTABLE = ("reg_release_status", "reg_aliases", "reg_proposals", "reg_proposal_changes", "reg_publish_keys")


def apply_registry_schema(conn: "psycopg.Connection[Any]", app_role: str | None = None) -> None:
    conn.execute(resources.files("agent_core.registry.postgres").joinpath("schema.sql").read_text("utf-8"))
    if app_role is None:
        return
    role = sql.Identifier(app_role)
    for table in _INSERT_ONLY:
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(sql.Identifier(table), role))
    for table in _MUTABLE:
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(sql.Identifier(table), role))
    row = conn.execute("SELECT current_schema()").fetchone()
    assert row is not None
    conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(sql.Identifier(row[0]), role))


class _PgBlobs:
    def __init__(self, conn: "psycopg.Connection[Any]") -> None:
        self._c = conn

    def put(self, data: bytes) -> str:
        from agent_core.domain import sha256_hex
        digest = sha256_hex(data)
        self._c.execute("INSERT INTO reg_blobs (hash, bytes) VALUES (%s, %s) ON CONFLICT DO NOTHING", (digest, data))
        return digest

    def get(self, digest: str) -> bytes:
        row = self._c.execute("SELECT bytes FROM reg_blobs WHERE hash = %s", (digest,)).fetchone()
        if row is None:
            raise IntegrityError(f"no existe el contenido {digest[:12]}…")
        return verified(digest, bytes(row[0]))


def _ref(kind: str, ident: str, version: str) -> VersionRef:
    return VersionRef(kind=kind, id=ident, version=version)


class _PgTx:
    def __init__(self, conn: "psycopg.Connection[Any]", fail_on: Callable[[str], bool] | None) -> None:
        self._c = conn
        self._fail_on = fail_on
        self.blobs = _PgBlobs(conn)

    def __getattribute__(self, name: str) -> Any:
        fail_on = object.__getattribute__(self, "_fail_on")
        if fail_on is not None and not name.startswith("_") and fail_on(name):
            raise RuntimeError(f"falla inyectada en {name}")
        return object.__getattribute__(self, name)

    def _one(self, query: str, params: tuple[Any, ...]) -> Any:
        return self._c.execute(query, params).fetchone()  # type: ignore[arg-type]

    # propuestas
    def get_proposal(self, proposal_id: str, *, for_update: bool = False) -> Proposal | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self._one("SELECT proposal_json FROM reg_proposals WHERE proposal_id = %s" + lock, (proposal_id,))
        return Proposal.model_validate(loads(row[0])) if row else None

    def save_proposal(self, proposal: Proposal) -> None:
        self._c.execute("INSERT INTO reg_proposals (proposal_id, proposal_json) VALUES (%s, %s) "
                        "ON CONFLICT (proposal_id) DO UPDATE SET proposal_json = EXCLUDED.proposal_json",
                        (proposal.proposal_id, dumps(proposal)))

    def get_changes(self, proposal_id: str) -> list[EntityDraft]:
        row = self._one("SELECT drafts_json FROM reg_proposal_changes WHERE proposal_id = %s", (proposal_id,))
        return [EntityDraft.model_validate(d) for d in loads(row[0])] if row else []  # type: ignore[union-attr]

    def replace_changes(self, proposal_id: str, drafts: Sequence[EntityDraft]) -> None:
        self._c.execute("INSERT INTO reg_proposal_changes (proposal_id, drafts_json) VALUES (%s, %s) "
                        "ON CONFLICT (proposal_id) DO UPDATE SET drafts_json = EXCLUDED.drafts_json",
                        (proposal_id, dumps([d.model_dump(mode="json") for d in drafts])))

    # versiones
    def _version(self, row: Any) -> StoredVersion:
        return StoredVersion(ref=_ref(row[0], row[1], row[2]), content_hash=row[3],
                             docs=VersionDocs.model_validate(loads(row[4])), proposal_id=row[5],
                             created_by=row[6], created_at=row[7])

    _VCOLS = "kind, id, version, content_hash, docs, proposal_id, created_by, created_at"

    def get_version(self, ref: VersionRef) -> StoredVersion | None:
        row = self._one(f"SELECT {self._VCOLS} FROM reg_entity_versions WHERE kind = %s AND id = %s AND version = %s",
                        (ref.kind, ref.id, ref.version))
        return self._version(row) if row else None

    def list_versions(self, kind: str, entity_id: str) -> list[StoredVersion]:
        rows = self._c.execute(f"SELECT {self._VCOLS} FROM reg_entity_versions WHERE kind = %s AND id = %s",
                               (kind, entity_id)).fetchall()
        return [self._version(r) for r in rows]

    def insert_version(self, v: StoredVersion) -> None:
        self._c.execute(f"INSERT INTO reg_entity_versions ({self._VCOLS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (v.ref.kind, v.ref.id, v.ref.version, v.content_hash, dumps(v.docs), v.proposal_id,
                         v.created_by, v.created_at))

    # releases
    def get_release(self, release_id: str) -> StoredRelease | None:
        row = self._one("SELECT release_json, release_hash, agent_id, agent_version, base_release_id, proposal_id, "
                        "published_by, published_at FROM reg_releases WHERE release_id = %s", (release_id,))
        if row is None:
            return None
        return StoredRelease(release=Release.model_validate(loads(row[0])), release_hash=row[1], agent_id=row[2],
                             agent_version=row[3], base_release_id=row[4], proposal_id=row[5],
                             published_by=row[6], published_at=row[7])

    def release_refs(self, release_id: str) -> list[VersionRef]:
        rows = self._c.execute("SELECT kind, id, version FROM reg_release_entities WHERE release_id = %s "
                               "ORDER BY kind, id", (release_id,)).fetchall()
        return [_ref(*r) for r in rows]

    def insert_release(self, s: StoredRelease, refs: Sequence[VersionRef]) -> None:
        self._c.execute("INSERT INTO reg_releases (release_id, release_json, release_hash, agent_id, agent_version, "
                        "base_release_id, proposal_id, published_by, published_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (s.release.id, dumps(s.release), s.release_hash, s.agent_id, s.agent_version,
                         s.base_release_id, s.proposal_id, s.published_by, s.published_at))
        with self._c.cursor() as cur:
            cur.executemany("INSERT INTO reg_release_entities (release_id, kind, id, version) VALUES (%s, %s, %s, %s)",
                            [(s.release.id, r.kind, r.id, r.version) for r in refs])
        self._c.execute("INSERT INTO reg_release_status (release_id, status) VALUES (%s, 'active')", (s.release.id,))

    def release_status(self, release_id: str) -> Status | None:
        row = self._one("SELECT status FROM reg_release_status WHERE release_id = %s", (release_id,))
        return row[0] if row else None

    def set_release_status(self, release_id: str, status: Status) -> None:
        self._c.execute("UPDATE reg_release_status SET status = %s WHERE release_id = %s", (status, release_id))

    def latest_release_for_agent_version(self, agent_id: str, version: str) -> str | None:
        row = self._one("SELECT release_id FROM reg_releases WHERE agent_id = %s AND agent_version = %s "
                        "ORDER BY published_at DESC LIMIT 1", (agent_id, version))
        return row[0] if row else None

    # alias
    def get_alias(self, agent_id: str, alias: str, *, for_update: bool = False) -> str | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self._one("SELECT release_id FROM reg_aliases WHERE agent_id = %s AND alias = %s" + lock,
                        (agent_id, alias))
        return row[0] if row else None

    def set_alias(self, change: AliasChange) -> None:
        self._c.execute("INSERT INTO reg_aliases (agent_id, alias, release_id) VALUES (%s, %s, %s) "
                        "ON CONFLICT (agent_id, alias) DO UPDATE SET release_id = EXCLUDED.release_id",
                        (change.agent_id, change.alias, change.after))
        self._c.execute("INSERT INTO reg_alias_log (change_json) VALUES (%s)", (dumps(change),))

    def aliases_to(self, release_id: str) -> list[tuple[str, str]]:
        rows = self._c.execute("SELECT agent_id, alias FROM reg_aliases WHERE release_id = %s ORDER BY 1, 2",
                               (release_id,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    # evaluación y aprobaciones
    def insert_eval_run(self, run: EvalRun) -> None:
        self._c.execute("INSERT INTO reg_eval_runs (eval_run_id, proposal_id, candidate_hash, base_release_id, "
                        "suite, verdict, report, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (run.eval_run_id, run.proposal_id, run.candidate_hash, run.base_release_id,
                         dumps(run.suite), run.verdict, dumps(run.report), run.at))

    def latest_eval_run(self, proposal_id: str, candidate_hash: str) -> EvalRun | None:
        row = self._one("SELECT eval_run_id, base_release_id, suite, verdict, report, at FROM reg_eval_runs "
                        "WHERE proposal_id = %s AND candidate_hash = %s ORDER BY seq DESC LIMIT 1",
                        (proposal_id, candidate_hash))
        if row is None:
            return None
        return EvalRun(eval_run_id=row[0], proposal_id=proposal_id, candidate_hash=candidate_hash,
                       base_release_id=row[1], suite=VersionRef.model_validate(loads(row[2])), verdict=row[3],
                       report=EvalReport.model_validate(loads(row[4])), at=row[5])

    def insert_approval(self, a: Approval) -> None:
        self._c.execute("INSERT INTO reg_approvals (proposal_id, candidate_hash, actor, decision, reason, at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (a.proposal_id, a.candidate_hash, a.actor, a.decision, a.reason, a.at))

    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None:
        row = self._one("SELECT actor, decision, reason, at FROM reg_approvals WHERE proposal_id = %s "
                        "AND candidate_hash = %s ORDER BY seq DESC LIMIT 1", (proposal_id, candidate_hash))
        return (Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=row[0], decision=row[1],
                         reason=row[2], at=row[3]) if row else None)

    def append_event(self, event: RegistryEvent) -> None:
        self._c.execute("INSERT INTO reg_events (event_json) VALUES (%s)", (dumps(event),))

    def events(self) -> list[RegistryEvent]:
        rows = self._c.execute("SELECT event_json FROM reg_events ORDER BY seq").fetchall()
        return [RegistryEvent.model_validate(loads(r[0])) for r in rows]

    def get_publish_key(self, key: str) -> tuple[str, str] | None:
        row = self._one("SELECT proposal_id, release_id FROM reg_publish_keys WHERE key = %s", (key,))
        return (row[0], row[1]) if row else None

    def put_publish_key(self, key: str, proposal_id: str, release_id: str) -> None:
        self._c.execute("INSERT INTO reg_publish_keys (key, proposal_id, release_id) VALUES (%s, %s, %s)",
                        (key, proposal_id, release_id))


class PgRegistryStore:
    def __init__(self, connect: Callable[[], "psycopg.Connection[Any]"]) -> None:
        self.connect = connect
        self.fail_on: Callable[[str], bool] | None = None

    @contextmanager
    def transaction(self) -> Iterator[RegistryTx]:
        with self.connect() as conn:  # psycopg: commit al salir sin error, rollback con excepción
            yield _PgTx(conn, self.fail_on)
```
> `dumps` de M0 serializa modelos Pydantic (lo usa `PgAuditEvents`). `loads` devuelve `Decimal` para números con decimales, y así el `EvalReport` conserva sus cifras.

`agent_core/registry/postgres/runtime.py`:
```python
"""`RegistryPort` de producción (spec §7.1): solo lo publicado; release cacheada por id; estado con TTL."""

from datetime import datetime, timedelta
from typing import Literal

from agent_core.domain import AgentSelector, EntityRef, Principal, RegistryEntity, Release, require_exact_refs
from agent_core.ports import Clock
from agent_core.domain import ENTITY_KIND
from agent_core.registry.entities import decode_entity
from agent_core.registry.models import VersionRef
from agent_core.registry.postgres.store import PgRegistryStore


def _kind_name(model: type) -> str:
    return ENTITY_KIND[model].value


class PostgresRegistry:
    def __init__(self, store: PgRegistryStore, clock: Clock, status_ttl: timedelta = timedelta(seconds=5)) -> None:
        self._store, self._clock, self._ttl = store, clock, status_ttl
        self._releases: dict[str, Release] = {}
        self._status: dict[str, tuple[Literal["active", "revoked"], datetime]] = {}

    def _release(self, release_id: str) -> Release:
        cached = self._releases.get(release_id)
        if cached is None:
            with self._store.transaction() as tx:
                stored = tx.get_release(release_id)
            if stored is None:
                raise KeyError(release_id)
            cached = stored.release
            require_exact_refs(cached)
            self._releases[release_id] = cached
        return cached

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        now = self._clock.now()
        hit = self._status.get(release_id)
        if hit is not None and now - hit[1] < self._ttl:
            return hit[0]
        with self._store.transaction() as tx:
            status = tx.release_status(release_id)
        if status is None:
            raise KeyError(release_id)
        self._status[release_id] = (status, now)
        return status

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        with self._store.transaction() as tx:
            if selector.version is not None:
                release_id = tx.latest_release_for_agent_version(selector.id, selector.version)
            else:
                assert selector.alias is not None
                release_id = tx.get_alias(selector.id, selector.alias)
        if release_id is None:
            raise KeyError(str(selector.id))
        if self.release_status(release_id) != "active":
            raise KeyError(f"{release_id} revocada")
        release = self._release(release_id).model_copy(deep=True, update={"status": "active"})
        return release.model_copy(update={"id": release_id})

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        vref = VersionRef(kind=_kind_name(kind), id=ref.id, version=ref.version)
        with self._store.transaction() as tx:
            stored = tx.get_version(vref)
            if stored is None:
                raise KeyError(str(vref))
            data = tx.blobs.get(stored.content_hash)  # verifica el hash: IntegrityError
        entity = decode_entity(vref.kind, data)
        require_exact_refs(entity)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref.id}@{ref.version} no es {kind.__name__}")
        return entity
```
> - `get` con un `kind` que no está en `ENTITY_KIND` lanza `KeyError`, y eso es lo correcto para el contrato.
> - La release guardada tiene su `id` real porque `publish` e `import_seed` la insertan con `release_id`, así que el `update={"id": ...}` es solo defensivo.
> - Una release revocada no se resuelve para runs nuevos. Los runs abiertos la ven revocada por `release_status` y escalan (M4, T-REG-20).

Agrega a `agent_core/registry/__init__.py` los exports `PgRegistryStore`, `PostgresRegistry` y `apply_registry_schema` (desde `agent_core.registry.postgres.store` y `.runtime`).

- [ ] **Step 5: Correr las pruebas**

Run: `AGENTCORE_REQUIRE_POSTGRES=1 uv run pytest tests/integration/test_registry_postgres.py tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS. En Windows, si la consola falla por codificación, usa `PYTHONIOENCODING=utf-8`: es del entorno, no del código.

- [ ] **Step 6: Commit**
```bash
git add agent_core/registry/postgres agent_core/registry/__init__.py tests/integration/conftest.py tests/integration/test_registry_postgres.py
git commit -m "feat(registry): almacén Postgres con inmutabilidad y PostgresRegistry de runtime

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Composición: harness del motor, cableado del servicio y CLI

**Files:**
- Create: `agent_core/composition/evaluation.py`, `agent_core/composition/registry.py`, `testing/registry_demo.py`
- Modify: `agent_core/composition/__init__.py`, `agent_core/cli.py`
- Test: `tests/composition/test_registry_harness.py`, `tests/composition/test_registry_cli.py`

**Interfaces:**
- Consumes: `EngineDeps`, `EngineConfig`, `build_turn_engine` (composition); `ScenarioHarness`, `EvalTarget`, `HarnessUnavailable`, `Scenario` (registry); `GatewayError` (M0); `DecisionProvider` (decision).
- Produces:
  - `@dataclass(frozen=True) EvalStorage(uow_factory: UnitOfWorkFactory, audit: AuditSink, transcript: TranscriptStore)`.
  - `EngineScenarioHarness(*, clock, ids, keys, gateway, providers: Callable[[str], Mapping[str, DecisionProvider]], calibrations, authz, storage: Callable[[], EvalStorage], classifier=None, config=EngineConfig())`, que implementa `ScenarioHarness`. `providers` recibe el `scenario.id` para que la demo pueda guionar por escenario; en producción es una constante.
  - `UowRunReleases(uow_factory)`, que implementa `RunReleaseReader`.
  - `build_registry_service(dsn: str, *, evaluator: EvalPort, clock: Clock, ids: IdSource, runs: RunReleaseReader | None = None) -> RegistryService`.
  - `run_registry_cli(args: argparse.Namespace) -> int`.
  - `testing/registry_demo.py`: `demo_verifier() -> IdentityVerifier`, `issue(actor: Literal["human","bot"]) -> str` (JWS con roles del registry), `build_harness() -> EngineScenarioHarness` sobre el mundo de demo (gateway `CitingGateway`, proveedores guionados por escenario y almacenamiento en memoria), y `DEMO_SCRIPTS: dict[str, Callable[[ScriptedProvider, ScriptedProvider], None]]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/composition/test_registry_harness.py`:
```python
import pytest

from agent_core.composition import EngineScenarioHarness
from agent_core.domain import GatewayError, GatewayErrorKind, Outcome, RunClosed
from agent_core.registry import EvalSuite, EvalTarget, HarnessUnavailable, LocalSandbox, SnapshotRegistry
from agent_core.registry.evaluation.scoring import score_run
from testing.fakes.ids import FakeIds
from testing.registry_demo import build_harness, demo_suite
from tests.registry.helpers import AGENT, demo_pinned


def _target() -> EvalTarget:
    pinned = demo_pinned()
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))


def test_demo_scenario_resolves_with_real_engine() -> None:
    harness = build_harness()
    suite: EvalSuite = demo_suite()
    scenario = next(s for s in suite.scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(scenario.seed, _target())
    events = harness.run(_target(), AGENT, scenario, sandbox.tools(handle))
    closed = [e for e in events if isinstance(e, RunClosed)]
    assert closed and closed[-1].payload.outcome is Outcome.resolved
    assert score_run(events, scenario.expect, scenario.sensitive_values).passed


def test_gateway_failure_becomes_harness_unavailable() -> None:
    harness: EngineScenarioHarness = build_harness(gateway_error=GatewayError(GatewayErrorKind("unavailable"), "caído"))
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    with pytest.raises(HarnessUnavailable):
        harness.run(_target(), AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, _target())))
```
> Si `GatewayError` o `GatewayErrorKind` tienen otra firma, construye el error como lo hace `testing/fakes/gateway.py`.

`tests/composition/test_registry_cli.py`:
```python
import pytest

from agent_core.cli import main


def test_registry_help_lists_subcommands(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as info:
        main(["registry", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("import", "propose", "draft", "freeze", "evaluate", "approve", "publish", "promote", "revoke",
                "diff", "lineage", "export"):
        assert cmd in out
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/composition/test_registry_harness.py tests/composition/test_registry_cli.py -v`
Expected: FAIL con `ImportError: cannot import name 'EngineScenarioHarness'`.

- [ ] **Step 3: Implementar**

`agent_core/composition/evaluation.py`:
```python
"""`ScenarioHarness` real (registry spec §6.2): el motor compuesto sobre la release a evaluar."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from agent_core.composition.engine import EngineConfig, EngineDeps, build_turn_engine
from agent_core.decision import DecisionProvider
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import (
    AgentSelector, ConfirmAnswer, EngineEvent, GatewayError, JsonValue, Locale, Principal, RunInput, TurnInput,
)
from agent_core.ports import (
    AuditSink, AuthzPort, Clock, GenerationResult, IdSource, KeyProvider, LLMGateway, ToolExecutor, TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.registry import EvalTarget, HarnessUnavailable, Scenario
from agent_core.views import FieldClassifier


@dataclass(frozen=True)
class EvalStorage:
    uow_factory: UnitOfWorkFactory
    audit: AuditSink
    transcript: TranscriptStore


class _ProbingGateway:
    """El motor absorbe los `GatewayError`; la sonda los recuerda para marcar `failed_infra`."""

    def __init__(self, inner: LLMGateway) -> None:
        self._inner = inner
        self.failed = False

    def generate(self, prompt: Any, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        try:
            return self._inner.generate(prompt, inputs_model_view, locale, schema)
        except GatewayError:
            self.failed = True
            raise


class EngineScenarioHarness:
    def __init__(self, *, clock: Clock, ids: IdSource, keys: KeyProvider, gateway: LLMGateway,
                 providers: Callable[[str], Mapping[str, DecisionProvider]], calibrations: CalibrationSource,
                 authz: AuthzPort, storage: Callable[[], EvalStorage], classifier: FieldClassifier | None = None,
                 config: EngineConfig | None = None) -> None:
        self._clock, self._ids, self._keys = clock, ids, keys
        self._gateway, self._providers, self._calibrations = gateway, providers, calibrations
        self._authz, self._storage, self._classifier = authz, storage, classifier
        self._config = config or EngineConfig()

    def _principal(self, scenario: Scenario, level: str) -> Principal:
        now = self._clock.now()
        return Principal.model_validate({
            "type": "customer", "id": scenario.principal.id, "attrs": dict(scenario.principal.attrs),
            "auth": {"level": level, "at": now}, "exp": now + timedelta(hours=1)})

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario, tools: ToolExecutor) -> list[EngineEvent]:
        storage = self._storage()
        probe = _ProbingGateway(self._gateway)
        engine = build_turn_engine(EngineDeps(
            clock=self._clock, ids=self._ids, keys=self._keys, uow_factory=storage.uow_factory,
            audit=storage.audit, registry=target.registry, releases=lambda _rid: target.release, tools=tools,
            gateway=probe, providers=self._providers(scenario.id), calibrations=self._calibrations,
            transcript=storage.transcript, authz=self._authz, classifier=self._classifier, config=self._config))
        run_id: str | None = None
        session_id: str | None = None
        token: str | None = None
        for n, step in enumerate(scenario.steps):
            principal = self._principal(scenario, step.auth)
            if step.op == "start":
                data: dict[str, Any] = {"agent": AgentSelector(id=agent_id, alias="prod"),
                                        "idempotency_key": f"eval-{scenario.id}"}
                if step.lang is not None:
                    data["lang"] = step.lang
                result = engine.start_run(principal, None, RunInput.model_validate(data))
                run_id, session_id, turn = result.run_id, result.session_id, result.first_turn
            else:
                if session_id is None:
                    break  # modo task: el run ya terminó en start
                confirm = (ConfirmAnswer(token=token or "", answer=step.answer)  # type: ignore[arg-type]
                           if step.op == "confirm" else None)
                turn = engine.handle_turn(principal, None, TurnInput(
                    session_id=session_id, text=step.text or "", channel="web", client_turn_id=f"c-{n}",
                    confirm=confirm))
            token = turn.confirmation.token if turn is not None and turn.confirmation is not None else None
        if probe.failed:
            raise HarnessUnavailable("el gateway falló durante el escenario")
        assert run_id is not None
        return storage.audit.read(run_id)
```
> `build_turn_engine` recibe `EngineDeps` tipado con `gateway: LLMGateway`. `_ProbingGateway` cumple el protocolo; si mypy pide la firma exacta, copia la de `agent_core/ports/llm.py` (`prompt: EntityRef`).

`agent_core/composition/registry.py`:
```python
"""Cableado del registry: servicio sobre Postgres, lector de runs y subcomando `agentcore registry`."""

import argparse
import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from agent_core.domain import Principal, dumps, loads
from agent_core.ports import Clock, IdentityVerifier, IdSource, UnitOfWorkFactory
from agent_core.registry import (
    EntityDraft, EvalPort, LocalSandbox, Origin, PgRegistryStore, RegistryError, RegistryService,
    RunReleaseReader, ScenarioEvaluator, VersionDocs,
)
from agent_core.flows import load_yaml


class UowRunReleases:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def release_of(self, run_id: str) -> str | None:
        with self._uow_factory() as uow:
            run = uow.load_run(run_id)
        return run.release if run is not None else None


def build_registry_service(dsn: str, *, evaluator: EvalPort, clock: Clock, ids: IdSource,
                           runs: RunReleaseReader | None = None) -> RegistryService:
    store = PgRegistryStore(lambda: psycopg.connect(dsn, autocommit=False))
    return RegistryService(store, evaluator, clock, ids, runs=runs)


def _load(path: str) -> Any:
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def _drafts(paths: list[Path]) -> list[EntityDraft]:
    """Cada archivo YAML: `kind`, `docs` y `content` (la entidad en el formato de M1)."""
    out = []
    for path in paths:
        files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
        for f in files:
            data = load_yaml(f.read_bytes())
            assert isinstance(data, dict)
            out.append(EntityDraft.model_validate(data))
    return out


def add_registry_parser(sub: Any) -> None:
    reg = sub.add_parser("registry", help="registry de entidades (unidad 2)")
    reg.add_argument("--dsn", default=None, help="Postgres del registry (o AGENTCORE_REGISTRY_DSN)")
    reg.add_argument("--credential", default=None, help="JWS del principal (o AGENTCORE_CREDENTIAL)")
    reg.add_argument("--verifier", default="testing.registry_demo:demo_verifier")
    reg.add_argument("--harness", default="testing.registry_demo:build_harness")
    cmds = reg.add_subparsers(dest="registry_cmd", required=True)
    cmds.add_parser("import").add_argument("root", type=Path)
    exp = cmds.add_parser("export")
    exp.add_argument("release_id")
    exp.add_argument("out", type=Path)
    prop = cmds.add_parser("propose")
    prop.add_argument("agent_id")
    prop.add_argument("title")
    prop.add_argument("--origin", default="manual", choices=[o.value for o in Origin])
    draft = cmds.add_parser("draft")
    draft.add_argument("proposal_id")
    draft.add_argument("paths", type=Path, nargs="+")
    draft.add_argument("--rev", type=int, required=True)
    for name in ("validate", "freeze", "reopen", "show"):
        cmds.add_parser(name).add_argument("proposal_id")
    ev = cmds.add_parser("evaluate")
    ev.add_argument("proposal_id")
    ev.add_argument("suite_id")
    ap = cmds.add_parser("approve")
    ap.add_argument("proposal_id")
    ap.add_argument("candidate_hash")
    rj = cmds.add_parser("reject")
    rj.add_argument("proposal_id")
    rj.add_argument("reason")
    pb = cmds.add_parser("publish")
    pb.add_argument("proposal_id")
    pb.add_argument("idempotency_key")
    pr = cmds.add_parser("promote")
    pr.add_argument("agent_id")
    pr.add_argument("alias")
    pr.add_argument("release_id")
    rv = cmds.add_parser("revoke")
    rv.add_argument("release_id")
    rv.add_argument("reason")
    df = cmds.add_parser("diff")
    df.add_argument("a")
    df.add_argument("b")
    cmds.add_parser("lineage").add_argument("run_id")


def run_registry_cli(args: argparse.Namespace, *, clock: Clock, ids: IdSource,
                     env: Callable[[str], str | None]) -> int:
    dsn = args.dsn or env("AGENTCORE_REGISTRY_DSN")
    credential = args.credential or env("AGENTCORE_CREDENTIAL")
    if not dsn or not credential:
        print("agentcore registry necesita --dsn y --credential (o sus variables de entorno)", file=sys.stderr)
        return 2
    verifier: IdentityVerifier = _load(args.verifier)()
    actor: Principal = verifier.verify(credential)
    evaluator = ScenarioEvaluator(_load(args.harness)(), LocalSandbox(ids))
    service = build_registry_service(dsn, evaluator=evaluator, clock=clock, ids=ids)
    try:
        result = _dispatch(service, actor, args)
    except RegistryError as exc:
        print(json.dumps({"code": exc.code.value, "detail": exc.detail, "payload": loads(dumps(exc.payload))},
                         ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return 1
    print(dumps(result) if result is not None else "ok")
    return 0


def _dispatch(s: RegistryService, actor: Principal, a: argparse.Namespace) -> Any:
    match a.registry_cmd:
        case "import":
            return s.import_seed(actor, a.root)
        case "export":
            for rel, data in s.export(a.release_id).items():
                (a.out / rel).parent.mkdir(parents=True, exist_ok=True)
                (a.out / rel).write_bytes(data)
            return None
        case "propose":
            return s.create_proposal(actor, a.agent_id, Origin(a.origin), a.title)
        case "draft":
            return s.put_draft(actor, a.proposal_id, _drafts(a.paths), a.rev)
        case "validate":
            return s.validate(actor, a.proposal_id)
        case "freeze":
            return s.freeze(actor, a.proposal_id)
        case "reopen":
            return s.reopen(actor, a.proposal_id)
        case "show":
            return s.get_proposal(a.proposal_id)
        case "evaluate":
            return s.evaluate(actor, a.proposal_id, a.suite_id)
        case "approve":
            return s.approve(actor, a.proposal_id, a.candidate_hash)
        case "reject":
            return s.reject(actor, a.proposal_id, a.reason)
        case "publish":
            return s.publish(actor, a.proposal_id, a.idempotency_key)
        case "promote":
            return s.promote(actor, a.agent_id, a.alias, a.release_id)
        case "revoke":
            return s.revoke(actor, a.release_id, a.reason)
        case "diff":
            return s.diff_releases(a.a, a.b)
        case "lineage":
            return s.lineage_for_run(actor, a.run_id)
    raise AssertionError(a.registry_cmd)
```
> - El formato de archivo de `draft` es `{kind, docs: {description, rationale, changelog}, content: {...}}`. Documéntalo en la spec §7.5 en este mismo commit.
> - `VersionDocs` se importa para el `mypy` del formato; si queda sin uso, bórralo.
> - `lineage` por CLI no tiene `RunReleaseReader` (el servicio se construye sin `runs`), así que responde `not_found`. Con `--runs-dsn` se cablearía `UowRunReleases(PostgresStore(dsn).uow)`; agrégalo si la demo lo necesita.

`agent_core/cli.py`: en `main`, después de los parsers existentes:
```python
    from agent_core.composition.registry import add_registry_parser, run_registry_cli
    add_registry_parser(sub)
```
y en el despacho:
```python
    if args.command == "registry":
        return run_registry_cli(args, clock=SystemClock(), ids=SystemIds(), env=os.environ.get)
```
> Usa el nombre real del atributo del subcomando en `main` (`args.command` u otro) y el patrón de `SystemIds()` que ya usa `build_sweeper`.

`agent_core/composition/__init__.py`: exporta `EngineScenarioHarness`, `EvalStorage`, `UowRunReleases` y `build_registry_service`.

`testing/registry_demo.py`:
```python
"""Demo del registry con claves y dobles de PRUEBA (nunca producción).

    uv run python -m testing.registry_demo credentials   # imprime JWS de una persona y del bot constructor
"""

import json
import sys
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Literal

from agent_core.composition import EngineScenarioHarness, EvalStorage
from agent_core.decision import DecisionProvider, RawPrediction
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import GatewayError, Principal
from agent_core.ports import IdentityVerifier
from agent_core.registry import EvalSuite
from agent_core.views import FieldClassifier
from testing.engine_world import CATALOG, CitingGateway, SyntheticAuthz, demo_calibration
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.provider import ScriptedProvider
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript

_ISSUER = TestIdentityIssuer(FakeClock(), ttl=timedelta(days=3650))


def demo_verifier() -> IdentityVerifier:
    return _ISSUER.verifier()


def issue(actor: Literal["human", "bot"]) -> str:
    now = FakeClock().now()
    who = Principal.model_validate({
        "type": "builder", "id": "ana" if actor == "human" else "constructor-bot",
        "roles": ["constructor", "aprobador"] if actor == "human" else ["constructor"],
        "attrs": {"actor": "human"} if actor == "human" else {},
        "auth": {"level": "session", "at": now}, "exp": now + timedelta(days=3650)})
    return _ISSUER.issue(who)


def _script_resuelto(jev: ScriptedProvider, classifier: ScriptedProvider) -> None:
    """Mismo guion que `testing/replay/scenarios.py::resuelto`: continuar, casar el cargo y confirmar."""
    jev.push(RawPrediction(value={"command": "continue"}, p_raw={"command": 0.95}, tokens=20))
    classifier.push(RawPrediction(value={"match": "unica", "transaction": "tx-1"}, p_raw={"match": 0.9}, tokens=10))


DEMO_SCRIPTS: dict[str, Callable[[ScriptedProvider, ScriptedProvider], None]] = {"resuelto": _script_resuelto}


def demo_suite() -> EvalSuite:
    return EvalSuite.model_validate({
        "id": "disputas-suite", "version": "1.0.0", "agent_id": "atencion", "repetitions": 1,
        "noise_margin": "0", "floor": "1",
        "scenarios": [{
            "id": "resuelto", "principal": {"id": "cust-001", "attrs": {"country": "CO"}},
            "steps": [{"op": "start"}, {"op": "turn", "text": "no reconozco un cargo de ciento veinte dólares"},
                      {"op": "confirm", "answer": "yes"}],
            "seed": {"tools": {
                "buscar_transacciones": [{"result": [
                    {"transaction_id": "tx-1", "amount": "120.50", "currency": "USD"},
                    {"transaction_id": "tx-2", "amount": "30.00", "currency": "USD"}]}],
                "seleccionar": [{"result": {"transaction_id": "tx-1", "amount": "120.50", "currency": "USD"}}],
                "convertir_moneda": [{"result": "120.50"}],
                "radicar_pqr": [{"result": {"status": "Open", "id": "pqr-demo-1"}}],
                "obtener_pqr": [{"result": {"status": "Open", "id": "pqr-demo-1"}}]}},
            "expect": {"outcome": "resolved", "actions_verified": ["radicar_pqr"], "escalated": False}}]})


def build_harness(gateway_error: GatewayError | None = None) -> EngineScenarioHarness:
    clock, ids = FakeClock(), FakeIds()

    class _Gateway(CitingGateway):
        def generate(self, *a, **k):  # type: ignore[no-untyped-def]
            if gateway_error is not None:
                raise gateway_error
            return super().generate(*a, **k)

    def providers(scenario_id: str) -> Mapping[str, DecisionProvider]:
        jev, classifier = ScriptedProvider("jev", clock=clock), ScriptedProvider("classifier", clock=clock)
        DEMO_SCRIPTS.get(scenario_id, lambda j, c: None)(jev, classifier)
        return {"jev": jev, "classifier": classifier}

    def storage() -> EvalStorage:
        store = InMemoryStore()
        return EvalStorage(uow_factory=store.uow, audit=InMemoryAuditSink(store), transcript=InMemoryTranscript())

    return EngineScenarioHarness(
        clock=clock, ids=ids, keys=FakeKeyProvider.default(), gateway=_Gateway(), providers=providers,
        calibrations=InMemoryCalibrationSource({"cal-demo": demo_calibration()}), authz=SyntheticAuthz(),
        storage=storage, classifier=FieldClassifier(CATALOG))


if __name__ == "__main__":
    if sys.argv[1:] == ["credentials"]:
        print(json.dumps({"_note": "PRUEBA", "human": issue("human"), "bot": issue("bot")}, indent=2))
```
> **Ajuste esperado al implementar:** el guion exacto de proveedores y tools debe reproducir `testing/replay/scenarios.py::resuelto` (M11 T-M11-01). Si el escenario no cierra `resolved`, compara con el `EngineWorld` de ese camino (cuántas veces predice JEV, qué read-back usa `verify`) y ajusta `DEMO_SCRIPTS` y el `seed`. No cambies el motor.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/composition tests/registry -v && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add agent_core/composition agent_core/cli.py testing/registry_demo.py tests/composition/test_registry_harness.py tests/composition/test_registry_cli.py docs/specs/2026-09-29-registry-design.md
git commit -m "feat(composition): harness de escenarios con el motor, servicio del registry y CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: API REST montada en M9 (T-REG-13 por HTTP)

**Files:**
- Create: `agent_core/registry/http.py`
- Modify: `agent_core/api/app.py` (`ApiExtension`, `ApiDeps.extensions`, `authenticate`)
- Test: `tests/m09/test_extensions.py`, `tests/registry/test_http.py`

**Interfaces:**
- Consumes: `RegistryService` y sus modelos; `HTTP_STATUS`.
- Produces:
  - En M9: `Authenticate = Callable[[Request, str | None], Principal]`, `ApiExtension = Callable[[FastAPI, Authenticate], None]`, `ApiDeps.extensions: tuple[ApiExtension, ...] = ()`.
  - En el registry: `registry_extension(service: RegistryService) -> Callable[[FastAPI, Callable[[Request, str | None], Principal]], None]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m09/test_extensions.py`:
```python
from dataclasses import replace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from tests.m09.conftest import api_deps  # usa el constructor de ApiDeps que ya tengan las pruebas de M9


def test_extension_receives_app_and_authenticator() -> None:
    seen: list[str] = []

    def ext(app: FastAPI, authenticate) -> None:  # type: ignore[no-untyped-def]
        @app.get("/v1/ext/whoami")
        def whoami(request: Request, authorization: str | None = None) -> dict[str, str | None]:
            principal = authenticate(request, request.headers.get("authorization"))
            seen.append(principal.id or "")
            return {"id": principal.id}

    deps, credential = api_deps()
    app = create_app(replace(deps, extensions=(ext,)))
    client = TestClient(app)
    assert client.get("/v1/ext/whoami", headers={"authorization": credential}).json()["id"] == seen[0]
    assert client.get("/v1/ext/whoami").status_code == 401
```
> Adapta `api_deps()` al helper real de `tests/m09` (el que arma `ApiDeps` con `TestIdentityIssuer`). Si no existe un helper reutilizable, créalo en `tests/m09/conftest.py` a partir de `tests/m09/test_api.py` y úsalo en ambos archivos.

`tests/registry/test_http.py`:
```python
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.domain import CredentialsInvalid, Principal
from agent_core.registry.http import registry_extension
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World

PRINCIPALS: dict[str, Principal] = {"ana": human(), "bot": bot("constructor", "aprobador")}


def _client() -> tuple[TestClient, World]:
    w = World()
    app = FastAPI()

    def authenticate(request: Request, authorization: str | None) -> Principal:
        if authorization not in PRINCIPALS:
            raise CredentialsInvalid()
        return PRINCIPALS[authorization]

    registry_extension(w.service)(app, authenticate)
    return TestClient(app, raise_server_exceptions=False), w


def _h(who: str, key: str | None = None) -> dict[str, str]:
    return {"authorization": who, **({"idempotency-key": key} if key else {})}


def test_full_cycle_over_http() -> None:
    c, _ = _client()
    p = c.post("/v1/registry/proposals", json={"agent_id": AGENT, "origin": "manual", "title": "t"}, headers=_h("ana"))
    assert p.status_code == 201
    pid = p.json()["proposal_id"]
    body = {"expected_rev": 0, "changes": [prompt_draft().model_dump(mode="json"), SUITE.model_dump(mode="json")]}
    assert c.put(f"/v1/registry/proposals/{pid}/draft", json=body, headers=_h("ana")).status_code == 200
    h = c.post(f"/v1/registry/proposals/{pid}/freeze", headers=_h("ana")).json()["candidate_hash"]
    assert c.post(f"/v1/registry/proposals/{pid}/evaluate", json={"suite_id": "disputas-suite"},
                  headers=_h("ana")).json()["verdict"] == "pass"
    assert c.post(f"/v1/registry/proposals/{pid}/approve", json={"candidate_hash": h}, headers=_h("ana")).status_code == 200
    pub = c.post(f"/v1/registry/proposals/{pid}/publish", headers=_h("ana", "k1"))
    assert pub.status_code == 200
    rel = pub.json()["release_id"]
    assert c.get(f"/v1/registry/releases/rel-demo/diff/{rel}", headers=_h("ana")).status_code == 200


def test_bot_gets_forbidden_role_problem() -> None:  # T-REG-13 por HTTP
    c, w = _client()
    r = c.post("/v1/registry/releases/rel-demo/revoke", json={"reason": "x"}, headers=_h("bot"))
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "forbidden_role"


def test_validation_failed_carries_violations() -> None:
    c, _ = _client()
    pid = c.post("/v1/registry/proposals", json={"agent_id": AGENT, "origin": "manual", "title": "t"},
                 headers=_h("ana")).json()["proposal_id"]
    body = {"expected_rev": 0, "changes": [prompt_draft(version="0.1.0").model_dump(mode="json")]}
    c.put(f"/v1/registry/proposals/{pid}/draft", json=body, headers=_h("ana"))
    r = c.post(f"/v1/registry/proposals/{pid}/freeze", headers=_h("ana"))
    assert r.status_code == 422 and r.json()["violations"][0]["rule"] == "REG-VERSION"


def test_publish_requires_idempotency_key_and_auth() -> None:
    c, _ = _client()
    assert c.post("/v1/registry/proposals/x/publish", headers=_h("ana")).status_code == 422
    assert c.get("/v1/registry/releases/rel-demo").status_code == 401
```

- [ ] **Step 2: Correr y confirmar que fallan**

Run: `uv run pytest tests/m09/test_extensions.py tests/registry/test_http.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.registry.http` y `TypeError: unexpected keyword 'extensions'`.

- [ ] **Step 3: Implementar**

En `agent_core/api/app.py`:
```python
from collections.abc import Callable

Authenticate = Callable[[Request, str | None], Principal]
ApiExtension = Callable[[FastAPI, Authenticate], None]
```
En `ApiDeps`, agrega el campo `extensions: tuple[ApiExtension, ...] = ()`. En `create_app`, antes de `return app`:
```python
    def authenticate(request: Request, authorization: str | None) -> Principal:
        return admit(request, authorization, None).principal

    for extension in deps.extensions:
        extension(app, authenticate)
```

`agent_core/registry/http.py`:
```python
"""API REST del registry (spec §7.4). La monta M9 como extensión; no importa M9 ni M9 la importa."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent_core.domain import CredentialsInvalid, Principal, loads, dumps
from agent_core.registry.errors import HTTP_STATUS, RegistryError
from agent_core.registry.models import EntityDraft, Origin
from agent_core.registry.service import RegistryService

Authenticate = Callable[[Request, str | None], Principal]
Auth = Annotated[str | None, Header(alias="authorization")]


class _Create(BaseModel):
    agent_id: str
    origin: Origin = Origin.manual
    title: str


class _Draft(BaseModel):
    expected_rev: int
    changes: list[EntityDraft]


class _Evaluate(BaseModel):
    suite_id: str
    suite_version: str | None = None


class _Approve(BaseModel):
    candidate_hash: str


class _Reason(BaseModel):
    reason: str


class _Promote(BaseModel):
    release_id: str
    reason: str = ""


def _json(value: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(loads(dumps(value)), status_code=status)


def _problem(request: Request, exc: RegistryError) -> JSONResponse:
    body: dict[str, Any] = {"type": f"urn:agentcore:registry:{exc.code.value}", "title": exc.code.value,
                            "status": HTTP_STATUS[exc.code], "code": exc.code.value, "detail": exc.detail,
                            "trace_id": getattr(request.state, "trace_id", None)}
    if exc.code.value == "validation_failed":
        body["violations"] = exc.payload
    elif exc.payload is not None:
        body["payload"] = exc.payload
    return JSONResponse(loads(dumps(body)), status_code=HTTP_STATUS[exc.code],
                        media_type="application/problem+json")


def registry_extension(service: RegistryService) -> Callable[[FastAPI, Authenticate], None]:
    def install(app: FastAPI, authenticate: Authenticate) -> None:
        router = APIRouter(prefix="/v1/registry")

        @app.exception_handler(RegistryError)
        async def _registry_error(request: Request, exc: RegistryError) -> JSONResponse:
            return _problem(request, exc)

        if CredentialsInvalid not in app.exception_handlers:
            @app.exception_handler(CredentialsInvalid)
            async def _creds(request: Request, exc: CredentialsInvalid) -> JSONResponse:
                return JSONResponse({"code": "credentials_invalid", "status": 401}, status_code=401,
                                    media_type="application/problem+json")

        def who(request: Request, authorization: str | None) -> Principal:
            return authenticate(request, authorization)

        @router.post("/proposals", status_code=201)
        def create(request: Request, body: _Create, authorization: Auth = None) -> JSONResponse:
            return _json(service.create_proposal(who(request, authorization), body.agent_id, body.origin, body.title), 201)

        @router.get("/proposals/{pid}")
        def show(request: Request, pid: str, authorization: Auth = None) -> JSONResponse:
            who(request, authorization)
            return _json(service.get_proposal(pid))

        @router.put("/proposals/{pid}/draft")
        def draft(request: Request, pid: str, body: _Draft, authorization: Auth = None) -> JSONResponse:
            return _json(service.put_draft(who(request, authorization), pid, body.changes, body.expected_rev))

        @router.post("/proposals/{pid}/validate")
        def validate(request: Request, pid: str, authorization: Auth = None) -> JSONResponse:
            return _json(service.validate(who(request, authorization), pid))

        @router.post("/proposals/{pid}/freeze")
        def freeze(request: Request, pid: str, authorization: Auth = None) -> JSONResponse:
            return _json(service.freeze(who(request, authorization), pid))

        @router.post("/proposals/{pid}/reopen")
        def reopen(request: Request, pid: str, authorization: Auth = None) -> JSONResponse:
            return _json(service.reopen(who(request, authorization), pid))

        @router.post("/proposals/{pid}/evaluate")
        def evaluate(request: Request, pid: str, body: _Evaluate, authorization: Auth = None) -> JSONResponse:
            return _json(service.evaluate(who(request, authorization), pid, body.suite_id, body.suite_version))

        @router.post("/proposals/{pid}/approve")
        def approve(request: Request, pid: str, body: _Approve, authorization: Auth = None) -> JSONResponse:
            return _json(service.approve(who(request, authorization), pid, body.candidate_hash))

        @router.post("/proposals/{pid}/reject")
        def reject(request: Request, pid: str, body: _Reason, authorization: Auth = None) -> JSONResponse:
            return _json(service.reject(who(request, authorization), pid, body.reason))

        @router.post("/proposals/{pid}/publish")
        def publish(request: Request, pid: str, authorization: Auth = None,
                    idempotency_key: Annotated[str, Header()] = ...) -> JSONResponse:  # type: ignore[assignment]
            return _json(service.publish(who(request, authorization), pid, idempotency_key))

        @router.post("/aliases/{agent_id}/{alias}")
        def promote(request: Request, agent_id: str, alias: str, body: _Promote,
                    authorization: Auth = None) -> JSONResponse:
            return _json(service.promote(who(request, authorization), agent_id, alias, body.release_id, body.reason))

        @router.post("/releases/{rid}/revoke")
        def revoke(request: Request, rid: str, body: _Reason, authorization: Auth = None) -> JSONResponse:
            return _json(service.revoke(who(request, authorization), rid, body.reason))

        @router.get("/releases/{rid}")
        def release(request: Request, rid: str, authorization: Auth = None) -> JSONResponse:
            who(request, authorization)
            return _json(service.get_release(rid))

        @router.get("/releases/{a}/diff/{b}")
        def diff(request: Request, a: str, b: str, authorization: Auth = None) -> JSONResponse:
            who(request, authorization)
            return _json(service.diff_releases(a, b))

        @router.get("/entities/{kind}/{eid:path}")
        def entity(request: Request, kind: str, eid: str, authorization: Auth = None) -> JSONResponse:
            """`eid` admite `/` (p. ej. `t/saludo`); una versión se pide con `?version=`."""
            who(request, authorization)
            return _json(service.get_entity(kind, eid, request.query_params.get("version")))

        @router.get("/runs/{run_id}/lineage")
        def lineage(request: Request, run_id: str, authorization: Auth = None) -> JSONResponse:
            return _json(service.lineage_for_run(who(request, authorization), run_id))

        app.include_router(router)

    return install
```
> Notas:
> - `Body` y `BaseModel` se importan para los cuerpos; borra los imports que ruff marque como no usados.
> - La ruta de entidades usa `?version=` porque los ids de template llevan `/`. Ajusta la tabla de la spec §7.4 en el mismo commit.
> - En producción, M9 ya instala el manejador de `CredentialsInvalid`, así que el condicional evita duplicarlo.
> - `trace_id`: si M9 guarda el trace en `request.state` con otro nombre, usa su helper público; si solo existe en `agent_core.api.tracing`, deja `None`. El registry no puede importar M9.

- [ ] **Step 4: Correr las pruebas**

Run: `uv run pytest tests/m09 tests/registry -v && uv run agentcore contracts --check; uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS. Si `contracts --check` falla porque el OpenAPI de M9 cambió, regenera con `uv run agentcore contracts`: el campo nuevo no cambia rutas de M9, pero regenerar es lo correcto.

- [ ] **Step 5: Commit**
```bash
git add agent_core/api/app.py agent_core/registry/http.py tests/m09/test_extensions.py tests/m09/conftest.py tests/registry/test_http.py contracts docs/specs/2026-09-29-registry-design.md
git commit -m "feat(registry,m9): API REST del registry como extensión de M9

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: E2E (T-REG-27) y definición de terminado

**Files:**
- Modify: `tests/integration/test_registry_postgres.py` (E2E)
- Modify: `docs/specs/2026-09-29-registry-design.md` (estado y §14 marcada), `docs/specs/motor/00-indice.md`, `CLAUDE.md` (una línea en Estructura: `registry/` unidad 2)
- Test: `tests/integration/test_registry_postgres.py::test_end_to_end_prompt_change`

**Interfaces:**
- Consumes: todo lo anterior.

- [ ] **Step 1: Escribir la prueba E2E**

Agrega a `tests/integration/test_registry_postgres.py`:
```python
def test_end_to_end_prompt_change(registry_store: PgRegistryStore) -> None:  # T-REG-27
    from agent_core.registry import LocalSandbox, ScenarioEvaluator
    from agent_core.registry.models import EntityDraft, VersionDocs
    from testing.registry_demo import build_harness, demo_suite

    clock, ids = FakeClock(), FakeIds()
    evaluator = ScenarioEvaluator(build_harness(), LocalSandbox(ids), max_workers=1)
    runs: dict[str, str] = {}

    class Runs:
        def release_of(self, run_id: str) -> str | None:
            return runs.get(run_id)

    service = RegistryService(registry_store, evaluator, clock, ids, runs=Runs())
    service.import_seed(ANA, REGISTRY_DEMO)

    suite = EntityDraft(kind="eval_suite", content=demo_suite().model_dump(mode="json"),
                        docs=VersionDocs(description="suite de disputas", rationale="gate", changelog="inicial"))
    p = service.create_proposal(ANA, AGENT, Origin.manual, "Confirmación más clara del radicado")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(), suite], expected_rev=0)
    view = service.freeze(ANA, p.proposal_id)
    report = service.evaluate(ANA, p.proposal_id, "disputas-suite")
    assert report.verdict == "pass", report.model_dump()
    service.approve(ANA, p.proposal_id, view.candidate_hash)
    detail = service.publish(ANA, p.proposal_id, "e2e-1")

    reg = PostgresRegistry(registry_store, clock)
    release = reg.resolve_release(AgentSelector.parse(f"{AGENT}@staging"), principal())
    assert release.id == detail.release_id  # un run nuevo en staging usa la release nueva
    runs["run-e2e"] = release.id
    lineage = service.lineage_for_run(ANA, "run-e2e")
    changed = {e.ref.id: e.docs for e in lineage.entities if e.changed_vs_base}
    assert changed["p/resumen_radicado"].description == "cambio de prueba"
    assert lineage.approved_by == "ana" and lineage.eval_verdict == "pass"
```

- [ ] **Step 2: Correr la E2E**

Run: `AGENTCORE_REQUIRE_POSTGRES=1 uv run pytest tests/integration/test_registry_postgres.py::test_end_to_end_prompt_change -v`
Expected: PASS. Si `evaluate` da `fail`, el escenario de demo no cierra `resolved` con la base o la candidata: revisa el guion (Task 14, nota de ajuste). No bajes el piso de la suite para que pase.

- [ ] **Step 3: Correr todo**

Run: `docker compose up -d postgres && AGENTCORE_REQUIRE_POSTGRES=1 uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo en verde. Anota cualquier prueba omitida.

- [ ] **Step 4: Definición de terminado (spec §14).** Marca punto por punto en la spec:
  - [ ] `RegistryService`, `BlobStore`, `EvalPort`, `SandboxPort`, `Judge`, `PostgresRegistry` y `SnapshotRegistry` exportados y tipados (`agent_core/registry/__init__.py`).
  - [ ] T-REG-01 a T-REG-27 en verde. Tabla de trazabilidad en la spec: `test_*` → T-REG-NN.
  - [ ] `lint-imports`, `mypy` y `ruff` en verde.
  - [ ] API montada en M9 (`registry_extension`) y CLI (`agentcore registry …`).
  - [ ] **La composición del motor usa `PostgresRegistry`:** hoy no hay servidor de producción que componga `create_app` con adaptadores reales. Déjalo anotado como pendiente de la raíz de composición del servidor (junto con el LLM gateway, que va en otra sesión) en lugar de inventar ese cableado aquí.
  - [ ] `contracts/` regenerado (Task 1 y Task 15).
  - [ ] Sin TODO sin issue.

  Cambia el estado de la spec a "rev. 2 implementada (entrega)". Agrega en `docs/specs/motor/00-indice.md` la línea del paquete `registry/` y en `CLAUDE.md` (Estructura) `registry/  unidad 2 (spec 2026-09-29-registry-design.md)`.

- [ ] **Step 5: Commit**
```bash
git add tests/integration/test_registry_postgres.py docs CLAUDE.md
git commit -m "test(registry): E2E de propuesta a linaje y definición de terminado

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
