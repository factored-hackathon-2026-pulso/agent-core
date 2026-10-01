# `write_draft` y el agente constructor, fases 1 a 5 — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un flow con `invocable_by ⊆ {builder}` pueda crear una propuesta y escribir un borrador en el registry con `act → verify`, sin `confirm`, de forma idempotente y auditada (hito 1 de la spec).

**Architecture:**
- **Registry:** escrituras con clave de idempotencia (`reg_draft_writes`), lectura `get_write`, topes del constructor autónomo y pruebas de que ninguna release publicada sirve un borrador.
- **Motor:** `RiskClass.write_draft`; el nodo de escritura gana una forma `draft: true` con `tool` y `args` propios (sin `confirm`); M1 la valida (G0-05, G0-23, G0-22, G0-25, AG-02); M3 congela la acción en `confirmed` sin token; M2 y M4 la ejecutan y recuperan.
- **Composición:** `BuilderToolExecutor` envuelve `RegistryService` con la credencial `constructor`; el principal del run solo viaja como `AuditContext`.

**Tech Stack:** Python 3.12, Pydantic v2, psycopg 3, pytest, mypy strict, ruff, import-linter, `uv`. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-09-30-write-draft-design.md` (aprobada el 2026-09-30; la Task 0 de este plan la corrige donde el código la desmiente). Lee también `CLAUDE.md`, ADR 0007, 0019, 0006 y `docs/specs/2026-09-29-registry-design.md` §2, §7 y §18.

**Este plan cubre las fases 1 a 5.** Las fases 6 (replay de M11), 7 (agentes) y 8 (`await_approval`) tendrán su propio plan cuando se lleguen: la 7 depende de lo que se aprenda en 1 a 6, y la 8 tiene cuatro detalles que el usuario debe cerrar antes (spec §11).

## Global Constraints

- Python 3.12, Pydantic v2, Postgres 16, `uv`; sin colas, Redis ni vector DB.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`: el tiempo sale del `Clock` y los ids del `IdSource` inyectados.
- Dinero y cifras con `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads` y toda canonización usa `canonical_bytes`.
- Fixtures y pruebas solo con datos sintéticos; nunca credenciales ni datos reales.
- Un módulo solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública (`__init__.py`) de los módulos que `.importlinter` le permite. Si hace falta algo que no está expuesto, **detente y pregunta**.
- Si cambias un tipo de M0: `uv run agentcore contracts` y `uv run agentcore contracts --check`, y avisa al usuario (cambio de interfaz para todos los módulos).
- G0-16 no se relaja. `write_draft` solo para agentes con `invocable_by ⊆ {builder}`.
- Topes del constructor autónomo: 10 propuestas por día (ventana móvil de 24 h con el `Clock`) y 20 evaluaciones por propuesta; el tope de costo está **diferido** (no se implementa).
- Cada tarea corre `uv run pytest`, `uv run lint-imports`, `uv run mypy` y `uv run ruff check .` antes de su commit. Las pruebas con Postgres usan `docker compose up -d postgres`.
- Commits: uno por unidad lógica, formato `tipo(ámbito): resumen`, y al final del mensaje la línea `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Nunca `--no-verify`, nunca force-push. Sin push ni PR sin autorización del usuario. Rama de trabajo: `feat/write-draft`.
- Si una spec es ambigua, contradice un ADR o toca un "Abierto": detente y pregunta.

## Review Focus

Entradas y fallos que la spec implica pero ninguna tarea del camino feliz ejercita; cada uno tiene su prueba en la tarea indicada.

1. **Reintento de `put_draft` después de que la propuesta avanzó** (ya `candidate`): con la misma clave devuelve lo guardado, no `proposal_stale` ni `illegal_transition` (Task 3).
2. **Misma clave con otro contenido** da `idempotency_conflict`, nunca sobrescribe (Task 3).
3. **Un nodo `draft` al que se vuelve desde `V.failed`** es violación de G0-23; desde `V.verified` no (Task 8).
4. **Borde de la ventana de 24 h**: la propuesta número 11 `auto_detect` falla; una creada exactamente 24 h antes ya no cuenta; el origen `manual` no tiene tope (Task 5).
5. **Una acción `draft` nunca recibe token ni se puede proponer o confirmar**, y `freeze_draft_write` rechaza una tool que no sea `write_draft` (Tasks 7 y 11).
6. **Errores del registry hacia el motor**: `forbidden_role`, `step_up_required` y `quota_exceeded` son `denied` (sin efecto); los demás son `uncertain` en escrituras y el `verify` decide; argumentos mal formados no lanzan, dan `denied` (Task 13).
7. **El rol del run no presta permisos**: un run cuyo principal es un supervisor con `aprobador` sigue recibiendo `forbidden_role` si la credencial del executor no tiene `constructor` (Task 13).

---

## Task 0: Corregir la spec donde el código la desmiente

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-write-draft-design.md`

El código leído al preparar este plan contradice cinco puntos de la spec. Se corrigen antes de implementar para que spec y código no diverjan (CLAUDE.md: "si cambia el comportamiento acordado, actualiza el spec en el mismo cambio").

- [ ] **Step 1: §5 y §6 — el esquema de nodos sí cambia.** `ToolNode` solo admite las ramas `ok/error/timeout/denied` (`RESULTS["tool"]` en `domain/nodes.py`), no `uncertain`, y G0-05, `derive_claims`, G0-06 y la recuperación de M4 están atados al nodo `tool_write`. Reemplaza en §5 la línea «**No cambia el esquema de nodos** (se reutiliza `ToolConfig`) y **no hay evento nuevo**…» por:

```markdown
- **Nodo:** `WriteToolConfig` gana la forma `draft: true` con `tool` y `args` propios (`action_from` pasa a opcional; se declara uno u otro). El discriminador `node_kind` trata `tool` con `draft: true` como `tool_write`: así reutiliza las ramas `ok/uncertain/denied`, G0-06 y la recuperación. Un nodo `tool` normal (`ToolConfig`) con una tool de escritura sigue siendo violación de G0-05.1.
- **`Action`** gana `write_node_id` (el nodo `draft` que la creó). `confirm_node_id`, `confirmation_token_hash` y `token_exp` pasan a opcionales con un validador: una acción con `write_node_id` no lleva ninguno de los tres; una sin él, los tres.
- **No hay evento nuevo:** `action_dispatched`, `tool_called` y `action_verified` son la auditoría de cada escritura.
```

  En §6 reemplaza «**G0-05.1:** un nodo `tool` sin `action_from` admite `read`, `compute` y `write_draft`.» por «**G0-05.1:** un nodo `tool` (`ToolConfig`) admite `read` y `compute`; una escritura va en un nodo con `action_from` (con `confirm`) o con `draft: true` (solo `write_draft`, G0-23).» y la línea de **G0-23** por: «**G0-23:** un nodo con `draft: true` usa una tool `write_draft` con `readback_by: idempotency_key`; `next.ok == next.uncertain == V`, con V un `verify` con `by: idempotency_key` y un `readback` de clase `read`; ningún otro nodo de escritura tiene a V como destino de `ok` o `uncertain`; el flow solo vuelve a W desde la rama `verified` de V.»

- [ ] **Step 2: §7 — M4 también cambia.** Añade al final de §7: «**M4:** `turn/recovery.py::position_at_verify` encuentra el nodo de escritura de una acción `draft` por `write_node_id` (hoy solo busca por `confirm_node_id`).»

- [ ] **Step 3: §4.3 y §8 — el readback.** El predicado de un `verify` solo ve `{"readback": ...}`, no puede comparar con un hash esperado. Reemplaza en §4.3 «El `verify` del constructor compara `request_hash` con el esperado.» (si aparece en tu copia; si no, añade esta oración al final de §4.3): «La clave de idempotencia ya ata el contenido (una clave con otro contenido da `idempotency_conflict`), así que el `verify` del constructor solo comprueba que el registro exista con la operación esperada (`readback.op == "<op>"`; para `evaluate`, `readback.verdict == "pass"`). `get_write` devuelve además `verdict`, `run_id` y `on_behalf_of`.» En §8 cambia los ids de las tools a `registry/create_proposal`, `registry/put_draft`, `registry/freeze`, `registry/reopen`, `registry/evaluate`, `registry/validate`, `registry/get_proposal`, `registry/get_entity`, `registry/list_versions` y `registry/get_write` (un `EntityId` no admite `.`).

- [ ] **Step 4: §4.4 punto 2 y 3.** `SnapshotRegistry` sí lo usa `registry/service.py` (para evaluar), así que la prueba de composición no puede decir «solo en `composition/evaluation.py`». Reemplaza el punto 2 por: «**Composición:** ningún módulo fuera de `agent_core/registry/` menciona `SnapshotRegistry`, y dentro del paquete solo lo hacen `service.py`, `snapshot.py` y `__init__.py`.» y el punto 3 por: «**Estática:** `registry/postgres/runtime.py` solo llama a un conjunto cerrado de métodos del `tx` (`get_release`, `release_status`, `get_alias`, `latest_release_for_agent_version`, `get_version` y `blobs`); en particular nunca a `get_proposal`, `get_changes` ni a `get_draft_write`.»

- [ ] **Step 5: §15 — nuevos detalles resueltos.** Añade a §15 «Detalles a cerrar en el plan»: la lista `D-A…D-D` siguiente, marcada como resuelta aquí: `D-A` el esquema de nodos cambia (Step 1); `D-B` `Action.write_node_id`; `D-C` `get_write` y `verify` por existencia; `D-D` los ids de tools llevan `/`.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-09-30-write-draft-design.md
git commit -F - <<'EOF'
docs(write-draft): corrige la spec con lo que el codigo exige

El nodo draft reutiliza el nodo de escritura (ToolNode no tiene rama uncertain),
Action gana write_node_id, M4 cambia, el readback verifica por existencia y la
prueba de SnapshotRegistry se reformula.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

# Fase 1 — Registry

## Task 1: Errores nuevos y topes

**Files:**
- Modify: `agent_core/registry/errors.py`
- Create: `agent_core/registry/quotas.py`
- Test: `tests/registry/test_errors.py`, `tests/registry/test_quotas.py`

**Interfaces:**
- Produces: `RegistryErrorCode.idempotency_conflict` (409), `RegistryErrorCode.quota_exceeded` (429); `Quotas(proposals_per_day: int = 10, evals_per_proposal: int = 20, window: timedelta = timedelta(hours=24))` y `DEFAULT_QUOTAS`, en `agent_core.registry.quotas`.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/registry/test_errors.py`:

```python
def test_new_codes_have_http_status() -> None:
    assert HTTP_STATUS[RegistryErrorCode.idempotency_conflict] == 409
    assert HTTP_STATUS[RegistryErrorCode.quota_exceeded] == 429
```

Crea `tests/registry/test_quotas.py`:

```python
from datetime import timedelta

from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas


def test_default_quotas_are_the_ones_decided_for_the_autonomous_builder() -> None:  # TEMAS #16
    assert DEFAULT_QUOTAS == Quotas(proposals_per_day=10, evals_per_proposal=20, window=timedelta(hours=24))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/registry/test_errors.py tests/registry/test_quotas.py -v`
Expected: FAIL (`AttributeError: idempotency_conflict` y `ModuleNotFoundError: agent_core.registry.quotas`). Además `test_every_code_has_http_status` seguirá en verde.

- [ ] **Step 3: Implement**

En `agent_core/registry/errors.py` añade al enum, después de `not_found = "not_found"`:

```python
    idempotency_conflict = "idempotency_conflict"
    quota_exceeded = "quota_exceeded"
```

y al mapa `HTTP_STATUS`, después de `RegistryErrorCode.not_found: 404,`:

```python
    RegistryErrorCode.idempotency_conflict: 409,
    RegistryErrorCode.quota_exceeded: 429,
```

Crea `agent_core/registry/quotas.py`:

```python
"""Topes del constructor autónomo (TEMAS #16, spec write-draft §4.5).

Valen solo para propuestas con `origin = auto_detect`. El tope de costo por propuesta está diferido: no existe
aquí hasta que se fije su monto."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Quotas:
    proposals_per_day: int = 10  # ventana móvil medida con el Clock, no un día calendario
    evals_per_proposal: int = 20
    window: timedelta = timedelta(hours=24)


DEFAULT_QUOTAS = Quotas()
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/registry/test_errors.py tests/registry/test_quotas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_core/registry/errors.py agent_core/registry/quotas.py tests/registry/test_errors.py tests/registry/test_quotas.py
git commit -F - <<'EOF'
feat(registry): codigos idempotency_conflict y quota_exceeded y topes del constructor

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 2: Modelos y almacén de escrituras idempotentes

**Files:**
- Modify: `agent_core/registry/models.py`, `agent_core/registry/store.py`, `agent_core/registry/memory.py`, `agent_core/registry/postgres/store.py`, `agent_core/registry/postgres/schema.sql`, `agent_core/registry/__init__.py`
- Test: `tests/registry/test_draft_write_store.py` (memoria), `tests/integration/test_registry_postgres.py` (Postgres)

**Interfaces:**
- Produces (en `agent_core.registry.models`): `DraftOp`, `AuditContext(run_id: str, on_behalf_of: str)`, `DraftWrite(idempotency_key, op, proposal_id, rev_after, request_hash, result_ref, audit, created_at)`, `WriteRecord(op, proposal_id, rev_after, request_hash, verdict, run_id, on_behalf_of)`.
- Produces (en `RegistryTx`): `get_draft_write(key) -> DraftWrite | None`, `put_draft_write(write) -> None` (clave repetida: error), `get_eval_run(eval_run_id) -> EvalRun | None`, `count_created_after(origin: str, after: datetime) -> int` (propuestas creadas con ese origen en `at > after`), `count_eval_runs(proposal_id) -> int`.

- [ ] **Step 1: Write the failing tests (memoria)**

Crea `tests/registry/test_draft_write_store.py`:

```python
from datetime import timedelta

import pytest

from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AuditContext, DraftWrite, RegistryEvent
from testing.builders import NOW


def _write(key: str = "k1", **over: object) -> DraftWrite:
    base: dict[str, object] = {
        "idempotency_key": key, "op": "put_draft", "proposal_id": "prop-1", "rev_after": 1,
        "request_hash": "a" * 64, "created_at": NOW}
    return DraftWrite.model_validate(base | over)


def test_put_and_get_draft_write_round_trip() -> None:
    store = InMemoryRegistryStore()
    write = _write(audit=AuditContext(run_id="run-1", on_behalf_of="builder:ana"))
    with store.transaction() as tx:
        assert tx.get_draft_write("k1") is None
        tx.put_draft_write(write)
    with store.transaction() as tx:
        assert tx.get_draft_write("k1") == write


def test_a_repeated_key_is_rejected_and_rolled_back() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.put_draft_write(_write())
    with pytest.raises(ValueError):
        with store.transaction() as tx:
            tx.put_draft_write(_write(rev_after=9))
    with store.transaction() as tx:
        stored = tx.get_draft_write("k1")
        assert stored is not None and stored.rev_after == 1


def _created(origin: str, at: object) -> RegistryEvent:
    return RegistryEvent.model_validate({"type": "proposal_created", "actor": "bot", "principal_type": "builder",
                                         "origin": origin, "proposal_id": "p", "at": at})


def test_count_created_after_is_strict_and_filters_by_origin() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.append_event(_created("auto_detect", NOW - timedelta(hours=24)))  # justo en el borde: no cuenta
        tx.append_event(_created("auto_detect", NOW - timedelta(hours=23)))
        tx.append_event(_created("manual", NOW - timedelta(hours=1)))
    with store.transaction() as tx:
        assert tx.count_created_after("auto_detect", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("manual", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("import", NOW - timedelta(hours=24)) == 0


def test_count_eval_runs_of_an_unknown_proposal_is_zero() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        assert tx.count_eval_runs("nope") == 0
        assert tx.get_eval_run("nope") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/registry/test_draft_write_store.py -v`
Expected: FAIL (`ImportError: cannot import name 'AuditContext'`).

- [ ] **Step 3: Implement modelos**

En `agent_core/registry/models.py` añade, después de la clase `RegistryEvent`:

```python
DraftOp = Literal["create_proposal", "put_draft", "freeze", "reopen", "evaluate"]


class AuditContext(RegModel):
    """Quién pidió la escritura (ADR 0019 §4): el run y su principal, solo para auditoría, nunca permisos."""

    run_id: str = Field(min_length=1, max_length=200)
    on_behalf_of: str = Field(min_length=1, max_length=200)  # `tipo:id` del principal del run


class DraftWrite(RegModel):
    """Una escritura del constructor con clave de idempotencia (ADR 0007 §5). Una sola vez por clave."""

    idempotency_key: str = Field(min_length=1, max_length=255)
    op: DraftOp
    proposal_id: str
    rev_after: int
    request_hash: str
    result_ref: str | None = None  # `evaluate`: el id de la corrida de evaluación
    audit: AuditContext | None = None
    created_at: datetime


class WriteRecord(RegModel):
    """Lo que devuelve `get_write` (el readback de las tools `write_draft` del constructor)."""

    op: DraftOp
    proposal_id: str
    rev_after: int
    request_hash: str
    verdict: Verdict | None = None  # solo `evaluate`
    run_id: str | None = None
    on_behalf_of: str | None = None
```

- [ ] **Step 4: Implement store (Protocol, memoria)**

En `agent_core/registry/store.py` importa `DraftWrite` (y `datetime`):

```python
from datetime import datetime
```

añade `DraftWrite` a la lista del `from agent_core.registry.models import (...)` y, dentro de `class RegistryTx(Protocol)`, después de `put_publish_key`:

```python
    def get_draft_write(self, key: str) -> DraftWrite | None: ...
    def put_draft_write(self, write: DraftWrite) -> None: ...
    def get_eval_run(self, eval_run_id: str) -> EvalRun | None: ...
    def count_created_after(self, origin: str, after: datetime) -> int:
        """Propuestas creadas con ese origen en `at > after` (ventana móvil de los topes)."""
        ...
    def count_eval_runs(self, proposal_id: str) -> int: ...
```

En `agent_core/registry/memory.py` importa `DraftWrite` y `datetime`:

```python
from datetime import datetime
```

añade `DraftWrite` a los imports de modelos; en `_State` añade `draft_writes: dict[str, DraftWrite] = field(default_factory=dict)` y, en `_Tx`, después de `put_publish_key`:

```python
    def get_draft_write(self, key: str) -> DraftWrite | None:
        return self._s.draft_writes.get(key)

    def put_draft_write(self, write: DraftWrite) -> None:
        if write.idempotency_key in self._s.draft_writes:
            raise ValueError(f"la clave {write.idempotency_key} ya existe")
        self._s.draft_writes[write.idempotency_key] = write

    def get_eval_run(self, eval_run_id: str) -> EvalRun | None:
        return next((r for r in self._s.eval_runs if r.eval_run_id == eval_run_id), None)

    def count_created_after(self, origin: str, after: datetime) -> int:
        return sum(1 for e in self._s.events
                   if e.type == "proposal_created" and e.origin == origin and e.at > after)

    def count_eval_runs(self, proposal_id: str) -> int:
        return sum(1 for r in self._s.eval_runs if r.proposal_id == proposal_id)
```

- [ ] **Step 5: Run to verify memoria pasa**

Run: `uv run pytest tests/registry/test_draft_write_store.py -v`
Expected: PASS

- [ ] **Step 6: Write the failing tests (Postgres)**

Añade al final de `tests/integration/test_registry_postgres.py` (reutiliza `pytestmark`, `registry_store`, `psycopg` y `Any` ya importados):

```python
def test_draft_writes_round_trip_and_are_insert_only(registry_store: PgRegistryStore,
                                                     admin_conn: "psycopg.Connection[Any]") -> None:
    from agent_core.registry.models import AuditContext, DraftWrite
    from testing.builders import NOW

    write = DraftWrite(idempotency_key="k1", op="put_draft", proposal_id="prop-1", rev_after=1,
                       request_hash="a" * 64, audit=AuditContext(run_id="run-1", on_behalf_of="builder:ana"),
                       created_at=NOW)
    with registry_store.transaction() as tx:
        tx.put_draft_write(write)
    with registry_store.transaction() as tx:
        assert tx.get_draft_write("k1") == write
        assert tx.get_draft_write("otra") is None
    with pytest.raises(psycopg.errors.UniqueViolation):
        with registry_store.transaction() as tx:
            tx.put_draft_write(write)
    with registry_store.connect() as conn:  # rol de aplicación: sin UPDATE ni DELETE
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE reg_draft_writes SET rev_after = 9")
        conn.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM reg_draft_writes")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # administrador: lo frena el trigger
        admin_conn.execute("UPDATE reg_draft_writes SET rev_after = 9")


def test_counts_for_the_autonomous_builder_quotas(registry_store: PgRegistryStore) -> None:
    from datetime import timedelta

    from agent_core.registry.models import RegistryEvent
    from testing.builders import NOW

    def created(origin: str, at: object) -> RegistryEvent:
        return RegistryEvent.model_validate({"type": "proposal_created", "actor": "bot",
                                             "principal_type": "builder", "origin": origin,
                                             "proposal_id": "p", "at": at})

    with registry_store.transaction() as tx:
        tx.append_event(created("auto_detect", NOW - timedelta(hours=24)))  # en el borde: no cuenta
        tx.append_event(created("auto_detect", NOW - timedelta(hours=1)))
        tx.append_event(created("manual", NOW - timedelta(hours=1)))
    with registry_store.transaction() as tx:
        assert tx.count_created_after("auto_detect", NOW - timedelta(hours=24)) == 1
        assert tx.count_created_after("manual", NOW - timedelta(hours=24)) == 1
        assert tx.count_eval_runs("p") == 0
        assert tx.get_eval_run("nada") is None
```

- [ ] **Step 7: Run to verify Postgres falla**

Run: `docker compose up -d postgres` y luego `uv run pytest tests/integration/test_registry_postgres.py -k "draft_writes or quotas" -v`
Expected: FAIL (`AttributeError: '_PgTx' object has no attribute 'put_draft_write'`).

- [ ] **Step 8: Implement Postgres**

En `agent_core/registry/postgres/schema.sql`, después de la tabla `reg_publish_keys`, añade:

```sql
CREATE TABLE IF NOT EXISTS reg_draft_writes (
    idempotency_key text PRIMARY KEY, op text NOT NULL, proposal_id text NOT NULL, rev_after integer NOT NULL,
    request_hash char(64) NOT NULL, result_ref text, run_id text, on_behalf_of text,
    created_at timestamptz NOT NULL);
```

y agrega `'reg_draft_writes'` a la lista del bloque `DO $$ ... FOREACH t IN ARRAY ARRAY[...]`:

```sql
    FOREACH t IN ARRAY ARRAY['reg_blobs', 'reg_entity_versions', 'reg_releases', 'reg_release_entities',
                             'reg_approvals', 'reg_eval_runs', 'reg_events', 'reg_alias_log',
                             'reg_draft_writes'] LOOP
```

En `agent_core/registry/postgres/store.py`: agrega `"reg_draft_writes"` al final de la tupla `_INSERT_ONLY`; importa `AuditContext`, `DraftWrite` (junto a los otros modelos) y `datetime`:

```python
from datetime import datetime
```

y añade a `_PgTx`, después de `put_publish_key`:

```python
    def get_draft_write(self, key: str) -> DraftWrite | None:
        row = self._one("SELECT op, proposal_id, rev_after, request_hash, result_ref, run_id, on_behalf_of, "
                        "created_at FROM reg_draft_writes WHERE idempotency_key = %s", (key,))
        if row is None:
            return None
        audit = AuditContext(run_id=row[5], on_behalf_of=row[6]) if row[5] is not None and row[6] is not None \
            else None
        return DraftWrite(idempotency_key=key, op=row[0], proposal_id=row[1], rev_after=row[2],
                          request_hash=row[3].strip(), result_ref=row[4], audit=audit, created_at=row[7])

    def put_draft_write(self, write: DraftWrite) -> None:
        audit = write.audit
        self._c.execute(
            "INSERT INTO reg_draft_writes (idempotency_key, op, proposal_id, rev_after, request_hash, "
            "result_ref, run_id, on_behalf_of, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (write.idempotency_key, write.op, write.proposal_id, write.rev_after, write.request_hash,
             write.result_ref, audit.run_id if audit else None, audit.on_behalf_of if audit else None,
             write.created_at))

    def get_eval_run(self, eval_run_id: str) -> EvalRun | None:
        row = self._one("SELECT proposal_id, candidate_hash, base_release_id, suite, verdict, report, at "
                        "FROM reg_eval_runs WHERE eval_run_id = %s", (eval_run_id,))
        if row is None:
            return None
        return EvalRun(eval_run_id=eval_run_id, proposal_id=row[0], candidate_hash=row[1],
                       base_release_id=row[2], suite=VersionRef.model_validate(loads(row[3])), verdict=row[4],
                       report=EvalReport.model_validate(loads(row[5])), at=row[6])

    def count_created_after(self, origin: str, after: datetime) -> int:
        row = self._one("SELECT count(*) FROM reg_events WHERE event_json::jsonb->>'type' = 'proposal_created' "
                        "AND event_json::jsonb->>'origin' = %s "
                        "AND (event_json::jsonb->>'at')::timestamptz > %s", (origin, after))
        return int(row[0])

    def count_eval_runs(self, proposal_id: str) -> int:
        row = self._one("SELECT count(*) FROM reg_eval_runs WHERE proposal_id = %s", (proposal_id,))
        return int(row[0])
```

En `agent_core/registry/__init__.py` agrega a la importación de modelos `AuditContext`, `DraftWrite`, `WriteRecord`:

```python
from agent_core.registry.models import (
    AuditContext,
    DraftWrite,
    EntityDraft,
    Origin,
    ProposalState,
    VersionDocs,
    VersionRef,
    WriteRecord,
)
from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas
```

y a `__all__`: `"AuditContext"`, `"DEFAULT_QUOTAS"`, `"DraftWrite"`, `"Quotas"`, `"WriteRecord"` (si `ruff` pide orden, `uv run ruff check . --fix`).

- [ ] **Step 9: Run to verify everything passes**

Run: `uv run pytest tests/registry tests/integration/test_registry_postgres.py -v`
Expected: PASS (incluida `test_immutable_tables_reject_update_and_delete`).

- [ ] **Step 10: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: sin errores.

```bash
git add agent_core/registry tests/registry/test_draft_write_store.py tests/integration/test_registry_postgres.py
git commit -F - <<'EOF'
feat(registry): tabla reg_draft_writes y modelos de escrituras idempotentes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 3: `create_proposal` y `put_draft` con clave de idempotencia, y `get_write`

**Files:**
- Modify: `agent_core/registry/service.py`
- Test: `tests/registry/test_draft_writes.py`

**Interfaces:**
- Consumes: `DraftWrite`, `AuditContext`, `WriteRecord`, `Quotas` (Tasks 1 y 2).
- Produces: `RegistryService(..., quotas: Quotas = DEFAULT_QUOTAS)`; `create_proposal(actor, agent_id, origin, title, *, idempotency_key=None, audit=None)`; `put_draft(actor, proposal_id, changes, expected_rev, *, idempotency_key=None, audit=None)`; `get_write(idempotency_key) -> WriteRecord | None`.

- [ ] **Step 1: Write the failing tests**

Crea `tests/registry/test_draft_writes.py`:

```python
import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import AuditContext, Origin, ProposalState
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _events(w: World, type_: str) -> int:
    with w.store.transaction() as tx:
        return sum(1 for e in tx.events() if e.type == type_)


def test_create_proposal_with_the_same_key_returns_the_same_proposal() -> None:
    w = World()
    first = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    second = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    assert second.proposal_id == first.proposal_id
    assert _events(w, "proposal_created") == 1


def test_the_same_key_with_other_content_is_a_conflict() -> None:  # Review Focus 2
    w = World()
    w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    with pytest.raises(RegistryError) as info:
        w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "otro titulo", idempotency_key="k1")
    assert info.value.code is RegistryErrorCode.idempotency_conflict


def test_put_draft_replay_does_not_bump_rev_nor_go_stale() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    first = w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k2")
    again = w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k2")
    assert (first.rev, again.rev) == (1, 1)
    assert w.service.get_proposal(p.proposal_id).proposal.rev == 1


def test_put_draft_replay_after_the_proposal_moved_on_returns_the_stored_write() -> None:  # Review Focus 1
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0, idempotency_key="k3")
    w.service.freeze(bot(), p.proposal_id)
    again = w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0,
                                idempotency_key="k3")
    assert again.state is ProposalState.candidate  # no `illegal_transition` ni `proposal_stale`


def test_put_draft_same_key_other_changes_is_a_conflict() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k4")
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(bot(), p.proposal_id, [prompt_draft(text="Otro texto.")], expected_rev=0,
                            idempotency_key="k4")
    assert info.value.code is RegistryErrorCode.idempotency_conflict


def test_put_draft_without_key_still_goes_stale() -> None:  # no cambia el comportamiento previo
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0)
    assert info.value.code is RegistryErrorCode.proposal_stale


def test_get_write_reads_back_the_write_with_its_audit_context() -> None:
    w = World()
    audit = AuditContext(run_id="run-1", on_behalf_of="builder:ana")
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    assert w.service.get_write("k5") is None
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k5",
                        audit=audit)
    record = w.service.get_write("k5")
    assert record is not None
    assert (record.op, record.proposal_id, record.rev_after, record.verdict) == ("put_draft", p.proposal_id,
                                                                                 1, None)
    assert (record.run_id, record.on_behalf_of) == ("run-1", "builder:ana")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/registry/test_draft_writes.py -v`
Expected: FAIL (`TypeError: create_proposal() got an unexpected keyword argument 'idempotency_key'`).

- [ ] **Step 3: Implement**

En `agent_core/registry/service.py`:

1. Imports: cambia `from agent_core.domain import EntityKind, Principal, RegistryEntity, Release, loads` por

```python
from agent_core.domain import (
    EntityKind,
    JsonValue,
    Principal,
    RegistryEntity,
    Release,
    canonical_bytes,
    loads,
    sha256_hex,
)
```

   agrega a los modelos importados `AuditContext`, `DraftOp`, `DraftWrite`, `WriteRecord`, y las líneas:

```python
from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas
```

2. Después de `_violations_payload` añade:

```python
def _request_hash(op: str, payload: JsonValue) -> str:
    """Huella del contenido de una escritura: una clave solo se reutiliza con el mismo contenido."""
    return sha256_hex(canonical_bytes({"op": op, "payload": payload}))
```

3. En `__init__`, añade el parámetro `quotas: Quotas = DEFAULT_QUOTAS` al final de la firma y la línea `self._quotas = quotas` junto a `self._runs, self._limits = runs, limits`.

4. Después de `_rebuild`, añade los ayudantes:

```python
    @staticmethod
    def _replayed(tx: RegistryTx, key: str | None, op: DraftOp, request_hash: str) -> DraftWrite | None:
        """Una clave ya usada con el mismo contenido devuelve su escritura; con otro, conflicto (ADR 0007)."""
        if key is None:
            return None
        prior = tx.get_draft_write(key)
        if prior is None:
            return None
        if prior.op != op or prior.request_hash != request_hash:
            raise RegistryError(RegistryErrorCode.idempotency_conflict,
                                "la clave de idempotencia ya se usó con otro contenido")
        return prior

    def _remember(self, tx: RegistryTx, key: str | None, op: DraftOp, p: Proposal, request_hash: str,
                  audit: AuditContext | None, result_ref: str | None = None) -> None:
        if key is None:
            return
        tx.put_draft_write(DraftWrite(idempotency_key=key, op=op, proposal_id=p.proposal_id, rev_after=p.rev,
                                      request_hash=request_hash, result_ref=result_ref, audit=audit,
                                      created_at=self._clock.now()))
```

5. Reemplaza `create_proposal` completo por:

```python
    def create_proposal(self, actor: Principal, agent_id: str, origin: Origin, title: str, *,
                        idempotency_key: str | None = None, audit: AuditContext | None = None) -> Proposal:
        require_constructor(actor)
        request = _request_hash("create_proposal",
                                {"agent_id": agent_id, "origin": origin.value, "title": title})
        with self._store.transaction() as tx:
            prior = self._replayed(tx, idempotency_key, "create_proposal", request)
            if prior is not None:
                return self._proposal(tx, prior.proposal_id, for_update=False)
            base = tx.get_alias(agent_id, "staging")
            try:
                p = Proposal(proposal_id=self._ids.new_id(IdKind.proposal), agent_id=agent_id, origin=origin,
                             state=ProposalState.draft, base_release_id=base, title=title,
                             created_by=actor_id(actor), updated_at=self._clock.now())
            except ValidationError as exc:  # sin `input`: el título o el id pueden traer texto libre
                errors = exc.errors(include_input=False)
                fields = sorted({str(e["loc"][0]) for e in errors if e["loc"]})
                raise RegistryError(RegistryErrorCode.validation_failed, "la propuesta no es válida",
                                    payload=[{"rule": "REG-PROPOSAL", "path": f, "flow": None,
                                              "node_id": None, "message": f"`{f}` no es válido"}
                                             for f in fields]) from None
            tx.save_proposal(p)
            self._event(tx, "proposal_created", actor, p)
            self._remember(tx, idempotency_key, "create_proposal", p, request, audit)
            return p
```

6. Reemplaza `put_draft` completo por:

```python
    def put_draft(self, actor: Principal, proposal_id: str, changes: Sequence[EntityDraft],
                  expected_rev: int, *, idempotency_key: str | None = None,
                  audit: AuditContext | None = None) -> Proposal:
        require_constructor(actor)
        problems = check_draft_limits(changes, self._limits)
        if problems:
            raise RegistryError(RegistryErrorCode.validation_failed, "el borrador excede los límites",
                                payload=_violations_payload(problems))  # type: ignore[arg-type]
        request = _request_hash("put_draft", {"proposal_id": proposal_id, "expected_rev": expected_rev,
                                              "changes": [c.model_dump(mode="json") for c in changes]})
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            if self._replayed(tx, idempotency_key, "put_draft", request) is not None:
                return p  # el estado actual: la escritura ya ocurrió y la propuesta pudo avanzar
            self._expect(p, ProposalState.draft)
            if p.rev != expected_rev:
                raise RegistryError(RegistryErrorCode.proposal_stale,
                                    f"la propuesta va en la revisión {p.rev}, no en {expected_rev}")
            tx.replace_changes(proposal_id, changes)
            p = self._save(tx, p, rev=p.rev + 1)
            self._event(tx, "draft_updated", actor, p)
            self._remember(tx, idempotency_key, "put_draft", p, request, audit)
            return p
```

7. Después de `get_proposal` añade:

```python
    def get_write(self, idempotency_key: str) -> WriteRecord | None:
        """Readback de las escrituras del constructor (`readback_by: idempotency_key`, ADR 0007 §5)."""
        with self._store.transaction() as tx:
            write = tx.get_draft_write(idempotency_key)
            if write is None:
                return None
            verdict = None
            if write.op == "evaluate" and write.result_ref is not None:
                run = tx.get_eval_run(write.result_ref)
                verdict = run.verdict if run is not None else None
            audit = write.audit
            return WriteRecord(op=write.op, proposal_id=write.proposal_id, rev_after=write.rev_after,
                               request_hash=write.request_hash, verdict=verdict,
                               run_id=audit.run_id if audit else None,
                               on_behalf_of=audit.on_behalf_of if audit else None)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/registry -v`
Expected: PASS (todas, incluidas las previas de `test_service_build.py`).

- [ ] **Step 5: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/registry/service.py tests/registry/test_draft_writes.py
git commit -F - <<'EOF'
feat(registry): create_proposal y put_draft idempotentes y readback get_write

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 4: `freeze`, `reopen` y `evaluate` con clave de idempotencia

**Files:**
- Modify: `agent_core/registry/service.py`
- Test: `tests/registry/test_draft_writes.py` (se amplía)

**Interfaces:**
- Produces: `freeze(actor, proposal_id, *, idempotency_key=None, audit=None)`, `reopen(...)` igual, `evaluate(actor, proposal_id, suite_id, suite_version=None, *, idempotency_key=None, audit=None)`.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/registry/test_draft_writes.py` (y al `from tests.registry.service_world import ...` suma `ZERO`; importa `SuiteMetrics` y `EvalReport`):

```python
from agent_core.registry.evaluation.report import EvalReport, SuiteMetrics
from tests.registry.service_world import ZERO


def _candidate(w: World) -> str:
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(bot(), p.proposal_id)
    return p.proposal_id


def test_freeze_replay_returns_the_same_candidate() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    first = w.service.freeze(bot(), p.proposal_id, idempotency_key="kf")
    again = w.service.freeze(bot(), p.proposal_id, idempotency_key="kf")
    assert again.candidate_hash == first.candidate_hash
    assert _events(w, "frozen") == 1


def test_reopen_replay_bumps_rev_once() -> None:
    w = World()
    pid = _candidate(w)
    first = w.service.reopen(bot(), pid, idempotency_key="kr")
    again = w.service.reopen(bot(), pid, idempotency_key="kr")
    assert (first.rev, again.rev) == (2, 2) and again.state is ProposalState.draft
    assert _events(w, "reopened") == 1


def test_evaluate_replay_does_not_run_the_evaluator_twice() -> None:
    w = World()
    pid = _candidate(w)
    first = w.service.evaluate(bot(), pid, "disputas-suite", idempotency_key="ke")
    again = w.service.evaluate(bot(), pid, "disputas-suite", idempotency_key="ke")
    assert again.verdict == first.verdict == "pass"
    assert len(w.evaluator.calls) == 1
    record = w.service.get_write("ke")
    assert record is not None and record.op == "evaluate" and record.verdict == "pass"


def test_evaluate_replay_of_a_failed_gate_raises_gate_failed_without_a_second_run() -> None:
    w = World()
    m = SuiteMetrics(primary=0, guardrails=ZERO, runs=1)  # type: ignore[arg-type]
    w.evaluator.reports.append(EvalReport(verdict="fail", candidate=m, base=m))
    pid = _candidate(w)
    for _ in range(2):
        with pytest.raises(RegistryError) as info:
            w.service.evaluate(bot(), pid, "disputas-suite", idempotency_key="kg")
        assert info.value.code is RegistryErrorCode.gate_failed
    assert len(w.evaluator.calls) == 1
    record = w.service.get_write("kg")
    assert record is not None and record.verdict == "fail"


def test_a_failed_freeze_leaves_no_record() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft(version="0.1.0")], expected_rev=0)  # versión inválida
    with pytest.raises(RegistryError) as info:
        w.service.freeze(bot(), p.proposal_id, idempotency_key="kx")
    assert info.value.code is RegistryErrorCode.validation_failed
    assert w.service.get_write("kx") is None  # el verify del constructor lo verá como "no hubo efecto"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/registry/test_draft_writes.py -v`
Expected: FAIL (`TypeError: freeze() got an unexpected keyword argument 'idempotency_key'`).

- [ ] **Step 3: Implement**

En `agent_core/registry/service.py` reemplaza `freeze`, `reopen` y `evaluate` por las versiones siguientes y añade `_view` y `_stored_eval`.

```python
    @staticmethod
    def _view(proposal_id: str, cand: Candidate) -> CandidateView:
        return CandidateView(proposal_id=proposal_id, candidate_hash=cand.candidate_hash,
                             release_id_preview=release_id_for(cand.candidate_hash),
                             new_versions=list(cand.new_versions), auto_bumped=list(cand.auto_bumped))

    def freeze(self, actor: Principal, proposal_id: str, *, idempotency_key: str | None = None,
               audit: AuditContext | None = None) -> CandidateView:
        require_constructor(actor)
        request = _request_hash("freeze", {"proposal_id": proposal_id})
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            if self._replayed(tx, idempotency_key, "freeze", request) is not None:
                if p.candidate_hash is None:
                    raise RegistryError(RegistryErrorCode.illegal_transition, "la propuesta ya no está congelada")
                return self._view(proposal_id, self._rebuild(tx, p, "la candidata cambió desde freeze"))
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
                self._remember(tx, idempotency_key, "freeze", p, request, audit)
                return self._view(proposal_id, cand)
        raise failure

    def reopen(self, actor: Principal, proposal_id: str, *, idempotency_key: str | None = None,
               audit: AuditContext | None = None) -> Proposal:
        require_constructor(actor)
        request = _request_hash("reopen", {"proposal_id": proposal_id})
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            if self._replayed(tx, idempotency_key, "reopen", request) is not None:
                return p
            self._expect(p, ProposalState.candidate, ProposalState.evaluated, ProposalState.approved)
            p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "reopened", actor, p)
            self._remember(tx, idempotency_key, "reopen", p, request, audit)
            return p
```

Para `evaluate`, añade antes del método:

```python
    @staticmethod
    def _stored_eval(tx: RegistryTx, prior: DraftWrite) -> EvalReport:
        """El resultado de una evaluación ya hecha con esta clave: el reporte, o `gate_failed` si no pasó."""
        run = tx.get_eval_run(prior.result_ref) if prior.result_ref is not None else None
        if run is None:
            raise RegistryError(RegistryErrorCode.integrity_error, "la evaluación guardada no existe")
        if run.verdict == "fail":
            raise RegistryError(RegistryErrorCode.gate_failed, "la candidata no pasa el gate",
                                payload=run.report.model_dump(mode="json"))
        return run.report
```

y reemplaza el método `evaluate` por (el tope de evaluaciones se agrega en la Task 5):

```python
    def evaluate(self, actor: Principal, proposal_id: str, suite_id: str,
                 suite_version: str | None = None, *, idempotency_key: str | None = None,
                 audit: AuditContext | None = None) -> EvalReport:
        require_constructor(actor)
        request = _request_hash("evaluate", {"proposal_id": proposal_id, "suite_id": suite_id,
                                             "suite_version": suite_version})
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            prior = self._replayed(tx, idempotency_key, "evaluate", request)
            if prior is not None:
                return self._stored_eval(tx, prior)
            self._expect(p, ProposalState.candidate)
            cand = self._rebuild(tx, p, "la candidata cambió desde freeze")
            if cand.candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed, "la candidata cambió desde freeze")
            suite = self._suite(tx, cand, suite_id, suite_version)
            base_release, base_entities = self._base(tx, p.base_release_id)
        candidate_target = EvalTarget("candidate", cand.release,
                                      SnapshotRegistry(cand.release, cand.entities))
        base_target = (EvalTarget("base", base_release, SnapshotRegistry(base_release, base_entities))
                       if base_release is not None else None)
        report = self._evaluator.run(suite, candidate_target, base_target)  # fuera de la transacción

        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            again = self._replayed(tx, idempotency_key, "evaluate", request)  # otra llamada con la clave ganó
            if again is not None:
                return self._stored_eval(tx, again)
            if p.state is not ProposalState.candidate or p.candidate_hash != cand.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed,
                                    "la propuesta cambió durante la evaluación")
            run = EvalRun(
                eval_run_id=self._ids.new_id(IdKind.eval_run), proposal_id=proposal_id,
                candidate_hash=cand.candidate_hash, base_release_id=p.base_release_id,
                suite=version_ref(suite), verdict=report.verdict, report=report, at=self._clock.now())
            tx.insert_eval_run(run)
            if report.verdict == "pass":
                p = self._save(tx, p, state=ProposalState.evaluated)
            elif report.verdict == "fail":
                p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "evaluated", actor, p)
            self._remember(tx, idempotency_key, "evaluate", p, request, audit, result_ref=run.eval_run_id)
        if report.verdict == "fail":
            raise RegistryError(RegistryErrorCode.gate_failed, "la candidata no pasa el gate",
                                payload=report.model_dump(mode="json"))
        return report
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/registry -v`
Expected: PASS (todas, incluidas `test_service_decide.py` que ejercita `evaluate` y `freeze`).

- [ ] **Step 5: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/registry/service.py tests/registry/test_draft_writes.py
git commit -F - <<'EOF'
feat(registry): freeze, reopen y evaluate idempotentes por clave

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 5: Topes del constructor autónomo

**Files:**
- Modify: `agent_core/registry/service.py`
- Test: `tests/registry/test_quota_enforcement.py`

**Interfaces:**
- Consumes: `Quotas`, `count_created_after`, `count_eval_runs`.
- Produces: `RegistryError(quota_exceeded)` desde `create_proposal` (origen `auto_detect`) y `evaluate` (propuesta `auto_detect`).

- [ ] **Step 1: Write the failing tests**

Crea `tests/registry/test_quota_enforcement.py`:

```python
from datetime import timedelta

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.quotas import Quotas
from agent_core.registry.service import RegistryService
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _service(w: World, quotas: Quotas) -> RegistryService:
    return RegistryService(w.store, w.evaluator, w.clock, w.ids, quotas=quotas)


def _code(info: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return info.value.code


def test_the_autonomous_builder_cannot_create_more_than_its_daily_proposals() -> None:  # Review Focus 4
    w = World()
    service = _service(w, Quotas(proposals_per_day=2))
    for _ in range(2):
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "señal")
    with pytest.raises(RegistryError) as info:
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "señal")
    assert _code(info) is RegistryErrorCode.quota_exceeded


def test_the_window_is_a_rolling_24_hours() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "a")
    w.clock.advance(timedelta(hours=23, minutes=59))
    with pytest.raises(RegistryError):
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "b")
    w.clock.advance(timedelta(minutes=1))  # exactamente 24 h: la primera ya no cuenta
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "c")


def test_other_origins_have_no_quota() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    for origin in (Origin.manual, Origin.builder_chat, Origin.manual):
        service.create_proposal(bot(), AGENT, origin, "t")
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "una")  # las anteriores no cuentan


def test_a_replayed_creation_does_not_count_twice() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    first = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t", idempotency_key="k")
    again = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t", idempotency_key="k")
    assert again.proposal_id == first.proposal_id


def test_the_autonomous_builder_cannot_exceed_its_evaluations_per_proposal() -> None:
    w = World()
    service = _service(w, Quotas(evals_per_proposal=1))
    p = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t")
    service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")  # la 1.ª
    service.reopen(bot(), p.proposal_id)
    service.freeze(bot(), p.proposal_id)
    calls = len(w.evaluator.calls)
    with pytest.raises(RegistryError) as info:
        service.evaluate(bot(), p.proposal_id, "disputas-suite")  # la 2.ª
    assert _code(info) is RegistryErrorCode.quota_exceeded
    assert len(w.evaluator.calls) == calls  # no se gastó una evaluación


def test_manual_proposals_have_no_evaluation_quota() -> None:
    w = World()
    service = _service(w, Quotas(evals_per_proposal=1))
    p = service.create_proposal(bot(), AGENT, Origin.manual, "t")
    service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")
    service.reopen(bot(), p.proposal_id)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")  # no lanza
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/registry/test_quota_enforcement.py -v`
Expected: FAIL (`test_the_autonomous_builder_cannot_create_more_than_its_daily_proposals` no lanza `quota_exceeded`).

- [ ] **Step 3: Implement**

En `create_proposal` (service.py), después del bloque `prior`, antes de `base = tx.get_alias(...)`, añade:

```python
            if origin is Origin.auto_detect:
                since = self._clock.now() - self._quotas.window
                if tx.count_created_after(origin.value, since) >= self._quotas.proposals_per_day:
                    raise RegistryError(RegistryErrorCode.quota_exceeded,
                                        f"el constructor autónomo ya creó {self._quotas.proposals_per_day} "
                                        "propuestas en las últimas 24 horas")
```

En `evaluate`, después de `self._expect(p, ProposalState.candidate)` de la primera transacción, añade:

```python
            if p.origin is Origin.auto_detect and \
                    tx.count_eval_runs(p.proposal_id) >= self._quotas.evals_per_proposal:
                raise RegistryError(RegistryErrorCode.quota_exceeded,
                                    f"la propuesta ya tiene {self._quotas.evals_per_proposal} evaluaciones")
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/registry -v`
Expected: PASS

- [ ] **Step 5: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/registry/service.py tests/registry/test_quota_enforcement.py
git commit -F - <<'EOF'
feat(registry): topes del constructor autonomo (10 propuestas por dia, 20 evaluaciones)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 6: Regla 8 — ninguna release publicada sirve un borrador, y documentos de la fase 1

**Files:**
- Test: `tests/registry/test_rule8_invariant.py` (estática y de composición), `tests/integration/test_registry_postgres.py` (contrato en Postgres)
- Modify: `docs/specs/2026-09-29-registry-design.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`

- [ ] **Step 1: Write the tests**

Crea `tests/registry/test_rule8_invariant.py` (son pruebas guarda: pasan al crearse; describen la invariante de registry §2 regla 8):

```python
"""Regla 8 (registry §2): el motor solo lee lo publicado. Es la condición que hace admisible `write_draft`
(ADR 0019 §5, spec write-draft D1 y D2)."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "agent_core"
RUNTIME = ROOT / "registry" / "postgres" / "runtime.py"
# Lo único que el RegistryPort de producción le pide a la transacción: todo es contenido publicado.
ALLOWED_TX_CALLS = {"get_release", "release_status", "get_alias", "latest_release_for_agent_version",
                    "get_version", "blobs"}


def test_the_runtime_registry_only_touches_published_content() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "tx"}
    assert used <= ALLOWED_TX_CALLS, sorted(used - ALLOWED_TX_CALLS)
    for forbidden in ("get_proposal", "get_changes", "get_draft_write"):
        assert forbidden not in used


def test_snapshot_registry_is_only_built_by_the_registry_service() -> None:
    users = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.py")
             if "SnapshotRegistry" in path.read_text("utf-8")}
    allowed = {"registry/__init__.py", "registry/service.py", "registry/snapshot.py"}
    assert users == allowed, sorted(users ^ allowed)
```

Añade al final de `tests/integration/test_registry_postgres.py`:

```python
def test_a_draft_is_never_served_as_executable_content(registry_store: PgRegistryStore) -> None:  # regla 8
    from agent_core.domain import Prompt

    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    runtime = PostgresRegistry(registry_store, FakeClock())
    before = runtime.resolve_release(AgentSelector.parse(AGENT), principal())
    p = service.create_proposal(ANA, AGENT, Origin.manual, "borrador")
    service.put_draft(ANA, p.proposal_id,
                      [prompt_draft(version="9.9.9"), prompt_draft(version="1.0.0", id="p/solo_borrador")],
                      expected_rev=0)
    for ref in ("p/resumen_radicado@9.9.9", "p/solo_borrador@1.0.0"):  # versión nueva y entidad nueva
        with pytest.raises(KeyError):
            runtime.get(EntityRef.parse(ref), Prompt)
    after = runtime.resolve_release(AgentSelector.parse(AGENT), principal())
    assert after.id == before.id  # un borrador no mueve el alias ni crea una release
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/registry/test_rule8_invariant.py tests/integration/test_registry_postgres.py -v`
Expected: PASS. Para comprobar que la prueba estática detecta una violación, añade temporalmente `tx.get_changes("x")` en `PostgresRegistry.get`, comprueba que `test_the_runtime_registry_only_touches_published_content` falla y **revierte el cambio** (`git checkout agent_core/registry/postgres/runtime.py`).

- [ ] **Step 3: Documentos de la fase 1**

En `docs/specs/2026-09-29-registry-design.md`:

1. §18, fila 1 de la tabla: reemplaza `(§2 regla 7)` por `(§2 regla 8)` y añade al final de la nota: `**Construido (2026-09-30, spec write-draft fase 1):** `reg_draft_writes`, `idempotency_key` en `create_proposal`, `put_draft`, `freeze`, `reopen` y `evaluate`, y `get_write`. La regla 8 la guardan `tests/registry/test_rule8_invariant.py` y la prueba de contrato de `tests/integration/test_registry_postgres.py`.`
2. §7.2: en el bloque de código, cambia las líneas de construcción por:

```python
    def create_proposal(self, actor, agent_id, origin, title, *, idempotency_key=None, audit=None) -> Proposal
    def put_draft(self, actor, proposal_id, changes: list[EntityDraft], expected_rev: int, *, idempotency_key=None, audit=None) -> Proposal
    def validate(self, actor, proposal_id) -> ValidationReport
    def freeze(self, actor, proposal_id, *, idempotency_key=None, audit=None) -> Candidate
    def evaluate(self, actor, proposal_id, suite: EntityRef, *, idempotency_key=None, audit=None) -> EvalReport
    def reopen(self, actor, proposal_id, *, idempotency_key=None, audit=None) -> Proposal
```

   y añade a «Lecturas»: `def get_write(self, idempotency_key) -> WriteRecord | None   # readback de las escrituras con clave`.
3. §8: añade al final de la sección el párrafo: «**Topes del constructor autónomo (tema #16):** `create_proposal` con `origin = auto_detect` falla con `quota_exceeded` (429) si ya hay 10 creadas en las últimas 24 h (ventana móvil con el `Clock`), y `evaluate` sobre una propuesta `auto_detect` falla igual si ya tiene 20 evaluaciones (incluidas `failed_infra`). Se aplican en el servicio. El tope de costo por propuesta está diferido.» y en la tabla de errores de §7.4 añade las filas `idempotency_conflict | 409 | una clave de idempotencia reutilizada con otro contenido` y `quota_exceeded | 429 | tope del constructor autónomo`.
4. §17: en el punto 4 añade al final «Los topes del constructor autónomo (10 y 20) están implementados.».

En `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`, en #16, reemplaza `Aún no hay código que aplique los topes.` por `Los topes de 10 propuestas por día y 20 evaluaciones por propuesta ya se aplican en `RegistryService` (2026-09-30); el tope de costo sigue diferido.`

- [ ] **Step 4: Verification gates and commit**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo en verde (corre la suite completa al cerrar la fase 1).

```bash
git add tests/registry/test_rule8_invariant.py tests/integration/test_registry_postgres.py docs/specs/2026-09-29-registry-design.md docs/specs/TEMAS-ABIERTOS-PENDIENTES.md
git commit -F - <<'EOF'
test(registry): invariante de la regla 8 y documentos de la fase 1 de write_draft

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```


---

# Fase 2 — M0 (cambio de interfaz para todos los módulos)

## Task 7: `RiskClass.write_draft`, nodo de escritura `draft` y `Action` sin confirmación

**Files:**
- Modify: `agent_core/domain/entities.py`, `agent_core/domain/nodes.py`, `agent_core/domain/state.py`, `agent_core/domain/version.py`
- Modify (para que mypy siga en verde con los campos opcionales): `agent_core/actions/confirmation.py`, `agent_core/flows/graph.py`, `agent_core/turn/recovery.py`, `agent_core/interpreter/handlers/write.py`
- Regenerate: `contracts/` (`uv run agentcore contracts`)
- Modify (docs): `docs/specs/motor/m00-dominio-y-contratos.md`
- Test: `tests/m00/test_nodes.py`, `tests/m00/test_state.py`, `tests/m00/test_entities.py`

**Interfaces:**
- Produces:
  - `RiskClass.write_draft`.
  - `WriteToolConfig(action_from: NodeId | None, draft: bool, tool: RefSpec | None, args: dict[str, JsonValue], save_as, step_up_max_attempts)`: o bien `action_from` (forma con `confirm`, sin `tool` ni `args`), o bien `draft=True` con `tool` (forma draft).
  - `node_kind(...)` devuelve `"tool_write"` para `tool` con `action_from` **o** con `draft: true`.
  - `Action.write_node_id: NodeId | None`; `Action.confirm_node_id`, `confirmation_token_hash` y `token_exp` pasan a opcionales con el validador «los tres, o ninguno y `write_node_id`».
  - `SCHEMA_VERSION = "1.1.0"`.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/m00/test_nodes.py`:

```python
def test_draft_write_node_has_its_own_tool_and_args() -> None:
    node = NODE.validate_python(
        _tool({"draft": True, "tool": "guardar@1.0.0", "args": {"q": "slots.x"}, "save_as": "borrador"}))
    assert isinstance(node, WriteToolNode)
    assert node_kind(node) == "tool_write"
    assert node.config.draft and node.config.action_from is None


def test_draft_write_rejects_action_from_and_needs_a_tool() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"draft": True, "tool": "g@1.0.0", "action_from": "c", "save_as": "b"}))
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"draft": True, "action_from": None, "save_as": "b"}))


def test_confirm_write_rejects_a_tool() -> None:
    with pytest.raises(ValidationError):
        NODE.validate_python(_tool({"action_from": "confirmar", "tool": "g@1.0.0", "save_as": "b"}))
```

Añade al final de `tests/m00/test_state.py` (importa `from datetime import timedelta` y `from testing.builders import NOW` si el archivo no los tiene ya):

```python
DRAFT = {"confirm_node_id": None, "write_node_id": "guardar", "confirmation_token_hash": None,
         "token_exp": None, "state": "confirmed"}


def test_draft_action_has_no_confirmation_fields() -> None:
    draft = action(**DRAFT)
    assert (draft.write_node_id, draft.confirm_node_id, draft.confirmation_token_hash, draft.token_exp) == (
        "guardar", None, None, None)


def test_draft_action_rejects_any_confirmation_field() -> None:  # Review Focus 5
    from datetime import timedelta

    from testing.builders import NOW

    for name, value in (("confirm_node_id", "confirmar"), ("confirmation_token_hash", "f" * 64),
                        ("token_exp", NOW + timedelta(minutes=5))):
        with pytest.raises(ValidationError):
            action(**(DRAFT | {name: value}))


def test_confirm_action_needs_every_confirmation_field() -> None:
    for name in ("confirm_node_id", "confirmation_token_hash", "token_exp"):
        with pytest.raises(ValidationError):
            action(**{name: None})
```

Añade al final de `tests/m00/test_entities.py` (añade `import pytest` arriba si no está):

```python
def test_write_draft_is_a_write_and_needs_readback() -> None:
    from pydantic import ValidationError

    from agent_core.domain import RiskClass, ToolDef

    base = {"id": "guardar", "version": "1.0.0", "risk_class": "write_draft", "min_auth_level": "session",
            "idempotent": True}
    with pytest.raises(ValidationError):
        ToolDef.model_validate(base)
    tool = ToolDef.model_validate(base | {"readback_by": "idempotency_key"})
    assert tool.risk_class is RiskClass.write_draft and tool.is_write
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m00/test_nodes.py tests/m00/test_state.py tests/m00/test_entities.py -v`
Expected: FAIL (`write_draft` no es un `RiskClass`; el nodo `draft` no valida; `action(**DRAFT)` falla porque `write_node_id` no existe).

- [ ] **Step 3: Implement M0**

`agent_core/domain/entities.py`: en `RiskClass`, después de `compute = "compute"`, añade `write_draft = "write_draft"` y cambia la docstring a `"""Clase de riesgo de una tool; determina si escribe y qué confirmación exige (M0 §2.4). `write_draft` es la escritura confinada a un borrador del registry: va sin `confirm` (ADR 0019)."""`.

`agent_core/domain/nodes.py`: reemplaza la clase `WriteToolConfig` por:

```python
class WriteToolConfig(Model):
    """Escritura de un nodo `tool`.

    Con `action_from` usa la acción congelada de un `confirm` (ADR 0007) y no admite `tool` ni `args`.
    Con `draft: true` (ADR 0019) invoca una tool `write_draft` con sus propios `args`: no hay `confirm`; la
    acción se congela al entrar al nodo."""

    action_from: NodeId | None = None
    draft: bool = False
    tool: RefSpec | None = None
    args: dict[str, JsonValue] = Field(default_factory=dict)
    save_as: SaveAs
    step_up_max_attempts: PositiveInt = 2

    @model_validator(mode="after")
    def _one_form(self) -> "WriteToolConfig":
        if self.draft:
            if self.action_from is not None or self.tool is None:
                raise ValueError("una escritura draft declara `tool` y no `action_from`")
        elif self.action_from is None or self.tool is not None or self.args:
            raise ValueError("una escritura con confirm declara solo `action_from` (sin `tool` ni `args`)")
        return self
```

y en `node_kind`, reemplaza la condición del `tool_write` por:

```python
        if kind == "tool" and isinstance(config, dict) and (
                "action_from" in config or config.get("draft") is True):
            return "tool_write"
```

con la docstring `"""Discriminador: `type`, salvo `tool` con `action_from` o con `draft: true` → `tool_write`."""`.

`agent_core/domain/state.py`: reemplaza los campos de `Action` por

```python
    action_id: str
    confirm_node_id: NodeId | None = None
    write_node_id: NodeId | None = None  # el nodo `draft` que la creó (ADR 0019); sin confirm
    flow: EntityRef
    tool: EntityRef
    args: dict[str, JsonValue]
    args_hash: Sha256Hex
    state: ActionState
    confirmation_token_hash: Sha256Hex | None = None
    token_exp: UtcDatetime | None = None
    idempotency_key: str
    created_at: UtcDatetime
    cancel_reason: InvalidationReason | None = None

    @model_validator(mode="after")
    def _confirmed_or_draft(self) -> "Action":
        confirm = (self.confirm_node_id, self.confirmation_token_hash, self.token_exp)
        if self.write_node_id is None:
            if any(value is None for value in confirm):
                raise ValueError("una acción con confirm lleva confirm_node_id, token y vencimiento")
        elif any(value is not None for value in confirm):
            raise ValueError("una acción de escritura draft no lleva confirm_node_id, token ni vencimiento")
        return self
```

(conserva la docstring de la clase; si `state.py` no importa `model_validator` ya, añádelo a `from pydantic import ...`).

`agent_core/domain/version.py`: `SCHEMA_VERSION = "1.1.0"`.

- [ ] **Step 4: Mantén mypy en verde (campos opcionales)**

Run: `uv run mypy`
Expected: errores en `agent_core/actions/confirmation.py` (uso de `token_exp` y `confirmation_token_hash`) y quizá en `flows/graph.py`. Arréglalos así.

`agent_core/actions/confirmation.py`: añade `from datetime import datetime` y, después de `token_hash`, los ayudantes:

```python
def _expiry(action: Action) -> datetime:
    """El vencimiento de una acción con confirm; una escritura draft no se confirma (ADR 0019)."""
    if action.token_exp is None:
        raise IllegalTransition(f"la acción {action.action_id} no se confirma: es una escritura draft")
    return action.token_exp


def _digest(action: Action) -> str:
    if action.confirmation_token_hash is None:
        raise IllegalTransition(f"la acción {action.action_id} no se confirma: es una escritura draft")
    return action.confirmation_token_hash
```

y aplica estos reemplazos exactos:

| Antes | Después |
|---|---|
| `expires_at=action.token_exp,` | `expires_at=_expiry(action),` |
| `if current is not None and now < current.token_exp and (current.args_hash != args_hash` | `if current is not None and now < _expiry(current) and (current.args_hash != args_hash` |
| `if current is not None and now < current.token_exp:` | `if current is not None and now < _expiry(current):` |
| `if self._clock.now() >= current.token_exp:  # un token vencido nunca confirma` | `if self._clock.now() >= _expiry(current):  # un token vencido nunca confirma` |
| `hmac.compare_digest(token_hash(token), current.confirmation_token_hash)` | `hmac.compare_digest(token_hash(token), _digest(current))` |
| `now >= a.token_exp]` | `now >= _expiry(a)]` |

`agent_core/flows/graph.py`: en `writes_by_confirm` cambia la condición a `if isinstance(node, WriteToolNode) and node.config.action_from is not None:` (una escritura draft no es de ningún `confirm`; las trata la Task 8).

`agent_core/turn/recovery.py` y `agent_core/interpreter/handlers/write.py`: aún no hay flows `draft`, pero ambos comparan `action_from == confirm_node_id`, y dos `None` serían iguales. Añade el guard `node.config.action_from is not None and` delante de cada comparación (`if node.config.action_from is not None and node.config.action_from == action.confirm_node_id:` en recovery; `if node.config.action_from is not None and action.confirm_node_id == node.config.action_from:` en `_action_tool`). La Task 12 los reescribe para las acciones draft.

- [ ] **Step 5: Run to verify tests pass**

Run: `uv run pytest tests/m00 tests/m03 tests/m01 tests/m02 tests/m04 -v`
Expected: PASS salvo `tests/m00/test_contracts.py` (los contratos aún no se regeneraron).

- [ ] **Step 6: Regenerate `contracts/`**

Run: `uv run agentcore contracts` y luego `uv run agentcore contracts --check`
Expected: la segunda termina con código 0. Revisa `git diff contracts/`: cambian `VERSION`, `RiskClass.json`, `Action.json`, `WriteToolConfig.json` (y las que los referencian: `Node.json`, `RunState.json`). Todo es aditivo o de relajación.

- [ ] **Step 7: Documentar en m00**

En `docs/specs/motor/m00-dominio-y-contratos.md` añade: (a) bajo `RiskClass` (§2.4) «`write_draft`: escritura confinada a un borrador del registry, sin `confirm` (ADR 0019); `is_write` la cuenta y exige `readback_by`»; (b) bajo la descripción de los nodos (§2.5) «`WriteToolConfig` tiene dos formas: `action_from` (con `confirm`; sin `tool` ni `args`) y `draft: true` con `tool` y `args` propios; `node_kind` trata ambas como `tool_write`»; (c) bajo `Action` (§2.6) «`write_node_id` identifica el nodo `draft` que creó la acción; `confirm_node_id`, `confirmation_token_hash` y `token_exp` son opcionales y solo se omiten (los tres) en una acción con `write_node_id`»; (d) en el historial de versión «1.1.0: `RiskClass.write_draft`, `WriteToolConfig.draft` y `Action.write_node_id`».

- [ ] **Step 8: Verification gates and commit**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo en verde.

```bash
git add agent_core contracts docs/specs/motor/m00-dominio-y-contratos.md tests/m00
git commit -F - <<'EOF'
feat(domain): RiskClass.write_draft, escritura draft y Action sin confirmacion (SCHEMA_VERSION 1.1.0)

Cambio de interfaz de M0: contracts/ regenerado.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 9: AVISA al usuario del cambio de interfaz**

Escribe al usuario, en español y sin esperar su respuesta para seguir: «Cambio de interfaz de M0 (`SCHEMA_VERSION` 1.1.0, `contracts/` regenerado): `RiskClass.write_draft`, `WriteToolConfig.draft`/`tool`/`args` y `Action.write_node_id` con los campos de confirmación opcionales. Es aditivo, pero todos los módulos ven los tipos nuevos.»

---

# Fase 3 — M1

## Task 8: Escritura `draft` en M1 — grafo, reclamos, G0-05, referencias y G0-23

**Files:**
- Modify: `agent_core/flows/graph.py`, `agent_core/flows/claims.py`, `agent_core/flows/rules/writes.py`, `agent_core/flows/refs.py`, `agent_core/flows/schema.py`, `agent_core/flows/rules/phase5.py`, `agent_core/flows/validate.py`
- Create: `agent_core/flows/rules/drafts.py`
- Test: `tests/m01/cases.py`, `tests/m01/test_draft_writes.py`

**Interfaces:**
- Consumes: `WriteToolConfig.draft` (Task 7).
- Produces: `draft_writes(flow) -> list[WriteToolNode]` y `writes_by_action(flow) -> dict[str, list[WriteToolNode]]` (en `flows/graph.py`); `verify_entries(ctx, linked)` (renombre público de `_verify_entries`); regla `G0-23`.

- [ ] **Step 1: Write the failing tests**

En `tests/m01/cases.py`:
1. Añade a `ENTITIES`, después de `_tool("escribir", ...)`:

```python
    _tool("guardar", "write_draft", readback_by="idempotency_key",
          description="tool guardar", args_schema={"type": "object"}),
```

2. En el `ModelProfile` `perfil` añade la clave `"structured": "prompted",` (la usa el nodo `agent` desde G0-25; ninguna prueba de M1 depende del modo `native`).

3. Añade después de `task_base()`:

```python
def draft_base() -> dict[str, Any]:
    """Flow conversacional válido con una escritura `draft` (sin confirm): collect → draft → verify → respond."""
    return deepcopy(
        {
            "id": "borrador",
            "version": "1.0.0",
            "priority": 10,
            "nodes": [
                {"id": "pedir", "type": "collect", "config": {"slot": "desc", "prompt_ref": "t/pedir"},
                 "next": {"ok": "guardar", "max_attempts": "esc"}},
                {"id": "guardar", "type": "tool",
                 "config": {"draft": True, "tool": "guardar@1", "args": {"q": "slots.desc"},
                            "save_as": "res"},
                 "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}},
                {"id": "verificar", "type": "verify",
                 "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                            "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": "verif"},
                 "next": {"verified": "ok_msg", "failed": "esc"}},
                {"id": "ok_msg", "type": "respond",
                 "config": {"template_ref": "t/hecho", "claims": ["guardar"]},
                 "next": {"next": "fin"}},
                {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
                {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
            ],
        }
    )
```

Crea `tests/m01/test_draft_writes.py`:

```python
"""Escritura `draft` (ADR 0019, m01 §3.13): G0-05, G0-23, reclamos y referencias."""

from agent_core.domain import EntityKind
from agent_core.flows import derive_claims, entity_ref_sites
from tests.m01.cases import check, draft_base, flow, node, registry, rules


def test_draft_flow_is_valid() -> None:
    assert check(draft_base()) == []


def test_a_plain_tool_node_with_a_write_tool_is_still_g0_05() -> None:
    d = draft_base()
    node(d, "guardar")["config"] = {"tool": "escribir@1", "args": {"q": "slots.desc"}, "save_as": "res"}
    node(d, "guardar")["next"] = {"ok": "verificar", "error": "esc", "timeout": "esc", "denied": "esc"}
    assert "G0-05" in rules(check(d))


def test_draft_with_a_non_draft_write_tool_is_g0_23() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "escribir@1"  # write_reversible: pediría confirm
    assert "G0-23" in rules(check(d))


def test_draft_with_a_read_tool_is_g0_23() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "leer@1"
    assert "G0-23" in rules(check(d))


def test_draft_must_link_ok_and_uncertain_to_the_same_verify() -> None:
    d = draft_base()
    node(d, "guardar")["next"]["uncertain"] = "esc"
    assert "G0-23" in rules(check(d))


def test_draft_verify_must_be_by_idempotency_key() -> None:
    d = draft_base()
    node(d, "verificar")["config"]["by"] = "fact:facts.res.value.id"
    assert "G0-23" in rules(check(d))


def test_going_back_to_the_draft_from_verify_failed_is_g0_23() -> None:  # Review Focus 3
    d = draft_base()
    node(d, "verificar")["next"]["failed"] = "guardar"
    assert "G0-23" in rules(check(d))


def test_going_back_to_the_draft_from_verified_is_allowed() -> None:  # Review Focus 3
    d = draft_base()
    node(d, "ok_msg")["next"]["next"] = "otra"
    d["nodes"].append({"id": "otra", "type": "collect", "config": {"slot": "mas", "prompt_ref": "t/pedir"},
                       "next": {"ok": "guardar", "max_attempts": "fin"}})
    assert check(d) == []  # si otra regla rechaza este fixture por un motivo ajeno, simplifica el fixture


def test_a_respond_claiming_a_draft_write_must_pass_through_its_verified_branch() -> None:
    d = draft_base()
    node(d, "verificar")["next"]["failed"] = "ok_msg"
    assert "G0-05" in rules(check(d))


def test_derive_claims_sees_a_draft_write() -> None:
    d = draft_base()
    node(d, "ok_msg")["config"]["claims"] = []  # sin reclamo declarado: el derivado basta
    assert derive_claims(flow(d), registry())["ok_msg"] == frozenset({"guardar"})


def test_the_draft_tool_is_a_reference_site() -> None:
    sites = entity_ref_sites(flow(draft_base()))
    assert (EntityKind.tool, "guardar@1") in {(s.kind, str(s.ref)) for s in sites if s.node_id == "guardar"}


def test_an_unresolved_draft_tool_is_g0_02() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "nada@1"
    assert "G0-02" in rules(check(d))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m01/test_draft_writes.py -v`
Expected: FAIL (el flow `draft` da violaciones por reglas que aún no lo entienden, y no existe G0-23).

- [ ] **Step 3: Implement — grafo y reclamos**

`agent_core/flows/graph.py`: después de `writes_by_confirm` añade

```python
def draft_writes(flow: Flow) -> list[WriteToolNode]:
    """Las escrituras `draft` (sin confirm) del flow, en orden."""
    return [n for n in flow.nodes if isinstance(n, WriteToolNode) and n.config.draft]


def writes_by_action(flow: Flow) -> dict[str, list[WriteToolNode]]:
    """Escrituras por acción: la clave es el `confirm` de la acción o, en una escritura draft, el propio nodo."""
    grouped = writes_by_confirm(flow)
    for node in draft_writes(flow):
        grouped.setdefault(node.id, []).append(node)
    return grouped
```

`agent_core/flows/claims.py`: importa `writes_by_action` junto a `verify_of`; en `_inputs` añade, antes de `return set()`:

```python
    if isinstance(node, WriteToolNode) and node.config.draft:
        return _names(value_paths(dict(node.config.args), strict=False))
```

y en `derive_claims` reemplaza `writes = writes_by_confirm(flow)` por `writes = writes_by_action(flow)` y el bucle de `origins` por:

```python
    for node in flow.nodes:
        is_action = isinstance(node, ConfirmNode) or (isinstance(node, WriteToolNode) and node.config.draft)
        if is_action and node.id not in origins:
            origins[node.id] = _origin(_seeds(graph, writes.get(node.id, [])), propagation)
```

(quita de los imports lo que `ruff` marque como sin usar, p. ej. `writes_by_confirm`).

- [ ] **Step 4: Implement — G0-05, referencias, esquema y lectura de rutas**

`agent_core/flows/rules/writes.py`: (a) importa `draft_writes, writes_by_action` de `agent_core.flows.graph`; (b) renombra `_verify_entries` a `verify_entries` (la definición y su llamada en `_write_conditions`); (c) en `_node_conditions` cambia el texto a `"la tool de escritura {…} solo se invoca con action_from o con draft: true"`; (d) en `_claim_conditions` reemplaza `confirm_ids = {n.id for n in ctx.flow.nodes if isinstance(n, ConfirmNode)}` por

```python
    action_ids = {n.id for n in ctx.flow.nodes if isinstance(n, ConfirmNode)} | {
        w.id for w in draft_writes(ctx.flow)}
```

y `for action in sorted(claimed & confirm_ids):` por `for action in sorted(claimed & action_ids):`; (e) en `g0_05` reemplaza la última línea por `yield from _claim_conditions(ctx, writes_by_action(ctx.flow))` (las líneas `writes = writes_by_confirm(ctx.flow)`, `_node_conditions` y `_write_conditions(ctx, writes)` no cambian).

`agent_core/flows/refs.py`: importa `WriteToolNode` y añade en `node_ref_sites`, después del caso `ToolNode()`:

```python
        case WriteToolNode():
            if node.config.tool is not None:
                add(EntityKind.tool, node.config.tool, "tool")
```

`agent_core/flows/schema.py`: importa `WriteToolNode` y en `_args_fields` añade, después del `if isinstance(node, ToolNode)`:

```python
    if isinstance(node, WriteToolNode) and node.config.draft:
        yield ("/config/args", dict(node.config.args))
```

`agent_core/flows/rules/phase5.py`: importa `WriteToolNode` y, en `_read_sites`, añade después de la rama `ToolNode`:

```python
    elif isinstance(node, WriteToolNode) and node.config.draft:
        yield _Site("/config/args", value_paths(dict(node.config.args), strict=False), SLOTS_FACTS)
```

- [ ] **Step 5: Implement — G0-23**

Crea `agent_core/flows/rules/drafts.py`:

```python
"""G0-23: escrituras `draft` (sin confirm) de una tool `write_draft` (M1 §3.13, ADR 0019).

Total: nunca lanza; una tool que no resuelve no añade ruido (G0-02 ya lo reporta)."""

from collections.abc import Iterator

from agent_core.domain import RiskClass
from agent_core.flows.context import Ctx
from agent_core.flows.graph import Edge, draft_writes, verify_of
from agent_core.flows.rules.writes import verify_entries
from agent_core.flows.violations import Violation, clip

RULE = "G0-23"


def g0_23(ctx: Ctx) -> Iterator[Violation]:
    graph = ctx.graph
    linked: dict[str, list[str]] = {}  # verify enlazado → escrituras draft que lo tienen como ok/uncertain
    for write in sorted(draft_writes(ctx.flow), key=lambda w: w.id):
        ref = write.config.tool
        tool = ctx.tool(ref) if ref is not None else None
        if tool is not None and (tool.risk_class is not RiskClass.write_draft
                                 or tool.readback_by != "idempotency_key"):
            yield ctx.v(RULE, write.id,
                        "la tool de una escritura draft debe ser write_draft con readback_by: "
                        "idempotency_key", "/config/tool")
        verify = verify_of(graph, write)
        if verify is None or verify.config.by != "idempotency_key":
            yield ctx.v(RULE, write.id,
                        "ok y uncertain deben ir directo al mismo verify con by: idempotency_key", "/next")
            continue
        linked.setdefault(verify.id, []).append(write.id)
        cut: frozenset[Edge] = frozenset({(verify.id, "verified")})
        if write.id in graph.reachable([write.id], cut):  # solo se vuelve a W desde `verified`
            yield ctx.v(RULE, write.id,
                        f"{clip(write.id)} se puede repetir sin pasar por verified de {clip(verify.id)}")
    for verify_id, writers in sorted(linked.items()):
        if len(writers) > 1:
            names = ", ".join(clip(w) for w in sorted(writers))
            yield ctx.v(RULE, verify_id, f"el verify lo comparten varias escrituras: {names}")
    yield from verify_entries(ctx, linked)
```

`agent_core/flows/validate.py`: importa `from agent_core.flows.rules.drafts import g0_23` y añade `g0_23` al final de `FLOW_RULES`.

- [ ] **Step 6: Run to verify they pass**

Run: `uv run pytest tests/m01 -v`
Expected: PASS (todas, incluidas las previas de G0-05 y `test_agent_node.py`).

- [ ] **Step 7: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/flows tests/m01
git commit -F - <<'EOF'
feat(flows): escritura draft en M1 (G0-05, reclamos, referencias y G0-23)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 9: Excepción acotada de G0-22 y esquema del borrador

**Files:**
- Create: `agent_core/flows/draft_schema.py`
- Modify: `agent_core/flows/__init__.py`, `agent_core/flows/rules/phase5.py`
- Test: `tests/m01/test_draft_writes.py` (se amplía), `tests/m01/test_public_api.py`

**Interfaces:**
- Produces: `DRAFT_OUTPUT_SCHEMA: dict[str, JsonValue]` exportado por `agent_core.flows`. La salida de un nodo `agent` con ese `output_schema` puede leerse en `config.args` de una escritura `draft`.

- [ ] **Step 1: Write the failing tests**

En `tests/m01/test_public_api.py` añade `"DRAFT_OUTPUT_SCHEMA"` a la lista `PUBLIC` y `"draft_schema"` al conjunto `submodules` de `test_no_unlisted_public_name_leaks`.

Añade al final de `tests/m01/test_draft_writes.py`:

```python
from typing import Any

from agent_core.domain.schema import check_output, unsupported_keyword
from agent_core.flows import DRAFT_OUTPUT_SCHEMA
from tests.m01.cases import agent_node


def test_the_draft_schema_is_inside_the_supported_subset_and_accepts_a_draft() -> None:
    assert unsupported_keyword(DRAFT_OUTPUT_SCHEMA) is None
    good = {"changes": [{"kind": "prompt", "content": {"id": "p/x", "version": "1.0.0"},
                         "docs": {"description": "d", "rationale": "r", "changelog": "c"}}]}
    assert check_output(DRAFT_OUTPUT_SCHEMA, good) is None
    assert check_output(DRAFT_OUTPUT_SCHEMA, {"changes": [{"kind": "prompt"}]}) is not None
    assert check_output(DRAFT_OUTPUT_SCHEMA, {}) is not None


def _agent_into_draft(schema: dict[str, Any]) -> dict[str, Any]:
    d = draft_base()
    node(d, "pedir")["next"]["ok"] = "investigar"
    investigar = agent_node(output_schema=schema, save_as="hallazgo")
    investigar["next"]["answered"] = "guardar"
    d["nodes"].append(investigar)
    node(d, "guardar")["config"]["args"] = {"changes": "facts.hallazgo.value.changes"}
    return d


def test_agent_output_may_feed_a_draft_write_when_its_schema_is_the_draft_schema() -> None:
    assert check(_agent_into_draft(dict(DRAFT_OUTPUT_SCHEMA))) == []


def test_agent_output_with_another_schema_cannot_feed_a_draft_write() -> None:
    assert rules(check(_agent_into_draft({"type": "object"}))) == {"G0-22"}


def test_agent_output_still_cannot_feed_a_verify_even_with_the_draft_schema() -> None:
    d = _agent_into_draft(dict(DRAFT_OUTPUT_SCHEMA))
    node(d, "verificar")["config"]["predicate"] = {"==": [{"var": "facts.hallazgo.value.changes"}, []]}
    assert "G0-22" in rules(check(d))
```

(`check_output` y `unsupported_keyword` están en `agent_core/domain/schema.py`; si `agent_core.domain` también los reexporta puedes importarlos de allí.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m01/test_draft_writes.py tests/m01/test_public_api.py -v`
Expected: FAIL (`ImportError: cannot import name 'DRAFT_OUTPUT_SCHEMA'`).

- [ ] **Step 3: Implement**

Crea `agent_core/flows/draft_schema.py`:

```python
"""Esquema de la salida de un nodo `agent` cuando alimenta una escritura `draft` (ADR 0019 §1, enmienda D9).

Es la única salida de un `agent` que puede leerse en `tool.args`: el registry la valida otra vez con su esquema
estricto y sus límites, y la aprobación humana de la propuesta es el gate. Dentro del subconjunto cerrado de
`agent_core.domain.schema`."""

from agent_core.domain import JsonValue

_TEXT: dict[str, JsonValue] = {"type": "string"}

DRAFT_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["changes"],
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "content", "docs"],
                "properties": {
                    "kind": {"type": "string"},
                    "content": {"type": "object"},
                    "docs": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["description", "rationale", "changelog"],
                        "properties": {"description": _TEXT, "rationale": _TEXT, "changelog": _TEXT},
                    },
                },
            },
        },
    },
}
```

En `agent_core/flows/__init__.py` añade `from agent_core.flows.draft_schema import DRAFT_OUTPUT_SCHEMA` y `"DRAFT_OUTPUT_SCHEMA"` al inicio de `__all__`.

En `agent_core/flows/rules/phase5.py`: importa `from agent_core.flows.draft_schema import DRAFT_OUTPUT_SCHEMA` y reemplaza `g0_22` por:

```python
def g0_22(ctx: Ctx) -> Iterator[Violation]:
    """Lo que el modelo genera no alimenta decisiones ni escrituras (m01 §3.13).

    Excepción acotada (ADR 0019 §1, enmienda del 2026-09-30): el `args` de una escritura `draft` puede leer la
    salida de un nodo `agent` cuyo `output_schema` es `DRAFT_OUTPUT_SCHEMA`."""
    produced = {n.config.save_as: n.config.output_schema == DRAFT_OUTPUT_SCHEMA
                for n in ctx.flow.nodes if isinstance(n, AgentNode)}
    if not produced:
        return
    for node in ctx.flow.nodes:
        draft_args = isinstance(node, WriteToolNode) and node.config.draft
        for site in _read_sites(ctx, node):
            if site.sub in _AGENT_OUTPUT_SITES:
                continue
            for path in site.paths:
                if path.ns == "facts" and path.name in produced:
                    if draft_args and site.sub == "/config/args" and produced[path.name]:
                        continue
                    yield ctx.v(
                        "G0-22",
                        node.id,
                        f"{clip(path.raw)} es salida de un nodo agent: solo puede leerla un respond o el "
                        "input_view de un decide",
                        site.sub,
                    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/m01 -v`
Expected: PASS

- [ ] **Step 5: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/flows tests/m01
git commit -F - <<'EOF'
feat(flows): excepcion acotada de G0-22 para los args de una escritura draft

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 10: G0-25, AG-02 y documentos de la fase 3

**Files:**
- Modify: `agent_core/flows/rules/phase5.py`, `agent_core/flows/validate.py`, `agent_core/flows/agent.py`
- Test: `tests/m01/test_agent_node.py`, `tests/m01/test_draft_writes.py`
- Modify (docs): `docs/specs/motor/m01-validacion-estatica.md`, `docs/specs/2026-09-28-llm-gateway-design.md`, `docs/adr/0019-agentes-internos.md`, `docs/adr/0007-escrituras-con-outbox-e-idempotencia.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`

**Interfaces:**
- Produces: regla `G0-25` (en `FLOW_RULES`) y chequeo `AG-02` en `validate_flow_for_agent`.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/m01/test_agent_node.py`:

```python
def test_agent_prompt_with_a_native_profile_is_g0_25() -> None:
    from agent_core.domain import ModelProfile, Prompt

    native = ModelProfile.model_validate(
        {"id": "perfil_nativo", "version": "1.0.0", "endpoint_alias": "demo", "model": "modelo-sintetico",
         "temperature": "0", "max_tokens": 400, "structured": "native",
         "price": {"input_per_mtok": "1", "output_per_mtok": "2", "source": "sintético",
                   "as_of": "2026-09-28"}})
    prompt = Prompt.model_validate({"id": "p/nativo", "version": "1.0.0",
                                    "locales": {"es": "Responde.", "pt": "Responda."},
                                    "model_profile": "perfil_nativo@1"})
    assert rules(check(with_agent(prompt_ref="p/nativo"), registry(native, prompt))) == {"G0-25"}
```

Añade al final de `tests/m01/test_draft_writes.py`:

```python
from agent_core.flows import validate_flow_for_agent
from tests.m01.cases import agent, base


def _ag02(d: dict[str, Any], **over: Any) -> list[str]:
    return [v.rule for v in validate_flow_for_agent(flow(d), agent(**over), registry())]


def test_a_builder_agent_without_subjects_may_use_write_draft() -> None:
    assert _ag02(draft_base(), invocable_by=["builder"], subject_kinds=[]) == []


def test_write_draft_is_closed_to_agents_invocable_by_others() -> None:
    assert _ag02(draft_base(), invocable_by=["builder", "customer"], subject_kinds=[]) == ["AG-02"]
    assert _ag02(draft_base(), invocable_by=["advisor"], subject_kinds=[]) == ["AG-02"]


def test_write_draft_is_closed_to_agents_that_declare_subjects() -> None:
    assert _ag02(draft_base(), invocable_by=["builder"], subject_kinds=["customer"]) == ["AG-02"]


def test_a_flow_without_write_draft_has_no_ag_02() -> None:
    assert "AG-02" not in _ag02(base())  # el agente de prueba es invocable por customer
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m01/test_agent_node.py tests/m01/test_draft_writes.py -v`
Expected: FAIL (`G0-25` no se emite; `AG-02` no existe).

- [ ] **Step 3: Implement G0-25**

`agent_core/flows/rules/phase5.py`: asegúrate de importar `ModelProfile` y `StructuredMode` de `agent_core.domain`, y añade después de `g0_15`:

```python
def g0_25(ctx: Ctx) -> Iterator[Violation]:
    """El prompt de un nodo `agent` va en modo `prompted`: el paso del agente tiene propiedades opcionales y el
    modo `native` estricto de OpenAI lo rechaza (gateway §3.8)."""
    for node in ctx.flow.nodes:
        if not isinstance(node, AgentNode):
            continue
        prompt = ctx.prompt(node.config.prompt_ref)
        if prompt is None:
            continue
        profile = ctx.reg.resolve(EntityKind.model_profile, prompt.model_profile)
        if isinstance(profile, ModelProfile) and profile.structured is not StructuredMode.prompted:
            yield ctx.v(
                "G0-25",
                node.id,
                f"el model_profile {clip(prompt.model_profile.id)} del prompt del agente debe ser "
                "structured: prompted",
                "/config/prompt_ref",
            )
```

y en `agent_core/flows/validate.py` importa `g0_25` junto a las otras reglas de `phase5` y agrégalo al final de `FLOW_RULES`.

- [ ] **Step 4: Implement AG-02**

`agent_core/flows/agent.py`: amplía el import de dominio a `from agent_core.domain import Agent, EntityKind, Flow, PrincipalType, Prompt, RiskClass, StartFlowAction, Template, ToolDef`, añade antes de `validate_flow_for_agent`:

```python
def _uses_write_draft(flow: Flow, reg: RegistryView) -> bool:
    """True si algún nodo del flow referencia una tool `write_draft` (escritura draft o confirm)."""
    for site in flow_ref_sites(flow):
        if site.kind is not EntityKind.tool:
            continue
        tool = reg.resolve(site.kind, site.ref)
        if isinstance(tool, ToolDef) and tool.risk_class is RiskClass.write_draft:
            return True
    return False
```

y en `validate_flow_for_agent`, antes de `for site in flow_ref_sites(flow):` del bucle de G0-12, añade:

```python
    if _uses_write_draft(flow, reg):
        outside = sorted({p.value for p in agent.invocable_by} - {PrincipalType.builder.value})
        if outside:
            text = f"el flow usa una tool write_draft y {_agent_label(agent)} es invocable por {', '.join(outside)}"
            found.append(Violation(rule="AG-02", flow=label, message=clip(text, 240)))
        if agent.subject_kinds:
            text = f"el flow usa una tool write_draft y {_agent_label(agent)} declara subject_kinds"
            found.append(Violation(rule="AG-02", flow=label, message=clip(text, 240)))
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/m01 -v`
Expected: PASS

- [ ] **Step 6: Documentos de la fase 3**

- `docs/specs/motor/m01-validacion-estatica.md` §3.13: reemplaza el bloque «**Diseñado, no implementado (clase `write_draft`, constructor por señal)**…» hasta el final de la sección por:

```markdown
**Implementado (clase `write_draft`, fase 3 de la spec write-draft, 2026-09-30):**
- **Forma `draft` del nodo de escritura:** `tool` con `draft: true` declara su propia `tool` y `args` (sin `action_from`). Es un nodo `tool_write` más: ramas `ok`, `uncertain` y `denied`.
- **G0-05.1:** un nodo `tool` normal admite `read` y `compute`; una escritura va en un nodo con `action_from` (con `confirm`) o con `draft: true` (G0-23).
- **G0-23:** la tool de una escritura draft es `write_draft` con `readback_by: idempotency_key`; `next.ok == next.uncertain == V`, con V un `verify` con `by: idempotency_key`; ningún otro nodo de escritura tiene a V como destino de `ok` o `uncertain`; el flow solo vuelve al nodo desde la rama `verified` de V. No exige `confirm`.
- **Reclamos:** `respond.claims` y `derive_claims` usan el id del nodo draft como identificador de la acción (en vez del `confirm`); el invariante de G0-05.8 es el mismo.
- **G0-22, excepción acotada (ADR 0019 §1):** el `config.args` de una escritura draft puede leer `facts.<save_as>` de un nodo `agent` cuyo `output_schema` es `agent_core.flows.DRAFT_OUTPUT_SCHEMA`. Todos los demás destinos siguen vetados.
- **G0-25:** el prompt del `prompt_ref` de un nodo `agent` tiene un `model_profile` con `structured: prompted`.
- **AG-02** (`validate_flow_for_agent`): un agente cuyos flows referencian una tool `write_draft` tiene `invocable_by ⊆ {builder}` y `subject_kinds` vacío (los `subject_kinds` son cadenas libres: una lista de «datos de clientes» no es comprobable; con la lista vacía el agente nunca recibe subject, M9 §85).
- Pruebas: `tests/m01/test_draft_writes.py` y `tests/m01/test_agent_node.py`.
```

  y en la tabla de reglas de §3.4 añade las filas `G0-23` y `G0-25` con su descripción y fase `5`.
- `docs/specs/2026-09-28-llm-gateway-design.md`: en la línea que termina «Una regla G0-25 de M1 que lo detecte al validar el flow es una mejora posterior.» reemplaza esa frase por «La regla G0-25 de M1 lo detecta al validar el flow (implementada el 2026-09-30).» y en la línea «Abierto: regla G0-25 de M1 que lo detecte en la validación estática.» reemplaza «Abierto: regla G0-25…» por «Resuelto: regla G0-25 de M1.».
- `docs/adr/0019-agentes-internos.md`: añade al final «## Enmienda 2026-09-30 (spec write-draft)» con las viñetas: «§1: excepción acotada a G0-22: el `args` de una escritura `draft` puede leer la salida de un nodo `agent` cuyo `output_schema` es el del borrador; el registry la valida con esquema estricto y límites y la aprobación humana es el gate. §2: los flows de los dos agentes del constructor se duplican (AG-01 y `subflow` rechazado por G0-01 impiden compartirlos); comparten tools, prompts y plantillas. `LLMAgentPort` (`adapters/llm/agent_port.py`) ya existe. El nodo `draft` es un nodo `tool_write` con `draft: true`.»
- `docs/adr/0007-escrituras-con-outbox-e-idempotencia.md`: en «Enmienda 2026-09-30», reemplaza «(diseño, sin construir)» por «(construida en M0 a M3 y el registry, 2026-09-30)».
- `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` #17: añade al final «**Avance (2026-09-30):** hechos registry (idempotencia, `get_write`, topes), M0 (`RiskClass.write_draft`, nodo `draft`, `Action.write_node_id`), M1 (G0-05, G0-22, G0-23, G0-25, AG-02). Siguen M3, M2/M4, el adaptador del constructor, el replay y los agentes.»

- [ ] **Step 7: Verification gates and commit**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo en verde.

```bash
git add agent_core/flows tests/m01 docs
git commit -F - <<'EOF'
feat(flows): G0-25 y AG-02 y documentos de la fase 3 de write_draft

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

# Fase 4 — M3, M2 y M4

## Task 11: M3 — congelar y ejecutar una escritura `draft`

**Files:**
- Modify: `agent_core/actions/store.py`, `agent_core/actions/execution.py`, `agent_core/actions/manager.py`
- Test: `tests/m03/test_draft_write.py`
- Modify (docs): `docs/specs/motor/m03-acciones.md`

**Interfaces:**
- Consumes: `Action.write_node_id`, `WriteToolConfig.draft` (Task 7).
- Produces:
  - `single_action(state, states, confirm_node_id=None, write_node_id=None)` (filtra también por `write_node_id`).
  - `ActionManager.freeze_draft_write(state, write_node, resolved_args, tool_def, ctx) -> RunState`: crea la acción directamente en `confirmed` (sin token, `idempotency_key = action_id`); con una acción `confirmed` de ese nodo en el flow activo (reentrada tras un step-up) devuelve el estado igual. Lanza `ValueError` si el nodo no es draft o la tool no es la `write_draft` que declara; `IllegalTransition` sin flow activo.
  - `ActionManager.execute_write` acepta también el nodo draft (busca la acción por `write_node_id`).

- [ ] **Step 1: Write the failing tests**

Crea `tests/m03/test_draft_write.py`:

```python
"""Escritura `draft` (ADR 0019): la acción se congela directo en `confirmed`, sin token, y sigue
execute → verify con los mismos dos commits y la misma recuperación."""

import pytest

from agent_core.domain import (
    ActionState,
    EntityRef,
    InvalidationReason,
    RunState,
    ToolDef,
    VerifyNode,
    WriteToolNode,
    canonical_bytes,
    sha256_hex,
)
from testing.fakes.tools import RecordedCall
from tests.m03.harness import (
    ARGS,
    RESOURCE,
    WRITE,
    WRITE_NODE,
    CrashAfterCall,
    ProcessDied,
    World,
    base_state,
    event_types,
    persist,
    reload,
)

DRAFT = ToolDef.model_validate({
    "id": "guardar_borrador", "version": "1.0.0", "risk_class": "write_draft", "min_auth_level": "session",
    "idempotent": True, "readback_by": "idempotency_key", "source": "borradores"})
DRAFT_REF = EntityRef(id=DRAFT.id, version=DRAFT.version)
READBACK = ToolDef.model_validate({
    "id": "leer_borrador", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
    "idempotent": True, "source": "borradores"})
DRAFT_NODE = WriteToolNode.model_validate({
    "id": "guardar", "type": "tool",
    "config": {"draft": True, "tool": "guardar_borrador@1.0.0", "args": {"titulo": "t"},
               "save_as": "borrador"},
    "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}})
VERIFY = VerifyNode.model_validate({
    "id": "verificar", "type": "verify",
    "config": {"readback": "leer_borrador@1.0.0", "by": "idempotency_key",
               "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "borrador_ok"},
    "next": {"verified": "fin", "failed": "esc"}})


def _world() -> World:
    w = World()
    w.tools.register(DRAFT, handler=lambda args: {**RESOURCE, **args})
    w.tools.register_readback(READBACK, of=DRAFT_REF)
    return w


def _frozen(w: World) -> RunState:
    state = persist(w, base_state())
    return w.manager.freeze_draft_write(state, DRAFT_NODE, ARGS, DRAFT, w.ctx())


def _draft_calls(w: World) -> list[RecordedCall]:
    return [c for c in w.tools.calls if c.tool == DRAFT_REF]


def test_freeze_creates_a_confirmed_action_without_token() -> None:  # Review Focus 5
    state = _frozen(_world())
    [action] = state.actions
    assert action.state is ActionState.confirmed
    assert (action.write_node_id, action.confirm_node_id, action.confirmation_token_hash,
            action.token_exp) == ("guardar", None, None, None)
    assert action.idempotency_key == action.action_id
    assert action.args == ARGS and action.args_hash == sha256_hex(canonical_bytes(ARGS))
    assert action.tool == DRAFT_REF


def test_freeze_on_reentry_keeps_the_frozen_action() -> None:
    w = _world()
    state = _frozen(w)
    again = w.manager.freeze_draft_write(state, DRAFT_NODE, {"otra": "cosa"}, DRAFT, w.ctx())
    assert [a.action_id for a in again.actions] == [state.actions[0].action_id]
    assert again.actions[0].args == ARGS  # congelada: los args nuevos no la cambian


def test_freeze_rejects_a_tool_that_is_not_the_declared_write_draft() -> None:  # Review Focus 5
    w = _world()
    state = persist(w, base_state())
    with pytest.raises(ValueError):
        w.manager.freeze_draft_write(state, DRAFT_NODE, ARGS, WRITE, w.ctx())  # write_reversible


def test_freeze_rejects_a_node_that_is_not_a_draft_write() -> None:
    w = _world()
    state = persist(w, base_state())
    with pytest.raises(ValueError):
        w.manager.freeze_draft_write(state, WRITE_NODE, ARGS, DRAFT, w.ctx())  # forma con confirm


def test_execute_runs_a_draft_with_two_commits_and_the_action_id_as_key() -> None:
    w = _world()
    state = _frozen(w)
    before = state.state_version
    state, result, events = w.manager.execute_write(state, DRAFT_NODE, w.ctx())
    [action] = state.actions
    assert (result, action.state) == ("ok", ActionState.executed)
    assert state.state_version == before + 2
    assert event_types(events) == ["action_dispatched", "tool_called"]
    assert state.facts["borrador"].value == {**RESOURCE, **ARGS}
    assert [(c.idempotency_key, c.args) for c in _draft_calls(w)] == [(action.action_id, ARGS)]
    assert reload(w).actions[0].state is ActionState.executed


def test_verify_after_a_draft_write_is_verified() -> None:
    w = _world()
    state, _, _ = w.manager.execute_write(_frozen(w), DRAFT_NODE, w.ctx())
    state, result, _ = w.manager.verify(state, VERIFY, w.ctx())
    assert result == "verified" and state.actions[0].state is ActionState.verified


def test_a_crash_after_the_call_recovers_through_verify_without_rewriting() -> None:
    w = _world()
    with pytest.raises(ProcessDied):
        w.manager.execute_write(_frozen(w), DRAFT_NODE, w.ctx(tools=CrashAfterCall(w.tools)))
    loaded = reload(w)
    [action] = loaded.actions
    assert (action.state, action.write_node_id) == (ActionState.executing, "guardar")
    assert w.manager.pending_recovery(loaded) == [action.action_id]
    calls = len(_draft_calls(w))
    recovered, result, _ = w.manager.verify(loaded, VERIFY, w.ctx())
    assert result == "verified" and recovered.actions[0].state is ActionState.verified
    assert len(_draft_calls(w)) == calls  # nunca se re-ejecuta (ADR 0007 §4)


def test_invalidate_cancels_a_frozen_draft_that_has_not_run() -> None:
    w = _world()
    state, _ = w.manager.invalidate(_frozen(w), InvalidationReason.abandoned)
    assert state.actions[0].state is ActionState.cancelled


def test_expire_tokens_ignores_draft_actions() -> None:
    w = _world()
    state, events = w.manager.expire_tokens(_frozen(w))
    assert events == [] and state.actions[0].state is ActionState.confirmed
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m03/test_draft_write.py -v`
Expected: FAIL (`AttributeError: 'ActionManager' object has no attribute 'freeze_draft_write'`).

- [ ] **Step 3: Implement**

`agent_core/actions/store.py`: reemplaza `single_action` por

```python
def single_action(state: RunState, states: Collection[ActionState], confirm_node_id: str | None = None,
                  write_node_id: str | None = None) -> Action:
    """La única acción del flow activo en `states` (y del `confirm` o del nodo draft dados).

    Ninguna o varias: bug del llamador."""
    if state.active_flow is None:
        raise IllegalTransition("no hay flow activo")
    flow = state.active_flow.flow
    found = [a for a in state.actions
             if a.flow == flow and a.state in states
             and (confirm_node_id is None or a.confirm_node_id == confirm_node_id)
             and (write_node_id is None or a.write_node_id == write_node_id)]
    if len(found) != 1:
        wanted = sorted(s.value for s in states)
        raise IllegalTransition(f"se esperaba una acción en {wanted}, hay {len(found)}")
    return found[0]
```

`agent_core/actions/execution.py`: amplía los imports de dominio con `Action`, `IllegalTransition`, `RiskClass`, `canonical_bytes` y `sha256_hex`, y el de `store` con `add_action`:

```python
from agent_core.actions.store import add_action, move, replace_action, single_action
```

En `Executions`, antes de `execute_write`, añade:

```python
    def freeze_draft_write(self, state: RunState, node: WriteToolNode, resolved_args: dict[str, JsonValue],
                           tool_def: ToolDef) -> RunState:
        """Congela la acción de una escritura `draft` directo en `confirmed`, sin token: no hay `confirm`.

        Con una acción `confirmed` de ese nodo en el flow activo (reentrada tras un step-up) conserva la
        congelada con sus args (M3 §3.7)."""
        tool = node.config.tool
        if not node.config.draft or tool is None:
            raise ValueError(f"{node.id}: no es una escritura draft")
        if tool_def.risk_class is not RiskClass.write_draft or tool_def.id != tool.id:
            raise ValueError(f"{node.id}: tool_def no es la write_draft que declara el nodo")
        if state.active_flow is None:
            raise IllegalTransition(f"escritura {node.id}: no hay flow activo")
        flow = state.active_flow.flow
        if any(a.write_node_id == node.id and a.flow == flow and a.state is ActionState.confirmed
               for a in state.actions):
            return state
        args = deepcopy(resolved_args)  # congelada: nadie comparte estado mutable con el llamador
        action_id = self._ids.new_id(IdKind.action)
        action = Action(action_id=action_id, write_node_id=node.id, flow=flow,
                        tool=EntityRef(id=tool_def.id, version=tool_def.version), args=args,
                        args_hash=sha256_hex(canonical_bytes(args)), state=ActionState.confirmed,
                        idempotency_key=action_id, created_at=self._clock.now())
        return add_action(state, action)
```

y en `execute_write` reemplaza `action = single_action(state, {ActionState.confirmed}, node.config.action_from)` por

```python
        if node.config.draft:
            action = single_action(state, {ActionState.confirmed}, write_node_id=node.id)
        else:
            action = single_action(state, {ActionState.confirmed}, node.config.action_from)
```

`agent_core/actions/manager.py`: añade el método, antes de `execute_write`:

```python
    def freeze_draft_write(self, state: RunState, write_node: WriteToolNode, resolved_args: dict[str, JsonValue],
                           tool_def: ToolDef, ctx: ActionContext) -> RunState:
        """Congela la acción de una escritura `draft` directo en `confirmed`, sin token (ADR 0019, M3 §3.7)."""
        _same_run(state, ctx)
        return self._executions.freeze_draft_write(state, write_node, resolved_args, tool_def)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/m03 -v`
Expected: PASS (todas, incluidas las previas).

- [ ] **Step 5: Documentar en m03**

En `docs/specs/motor/m03-acciones.md`: (a) en §2 añade a la interfaz `def freeze_draft_write(self, state, write_node, resolved_args, tool_def, ctx) -> RunState` y la nota «`execute_write` acepta también el nodo draft»; (b) en la tabla de §3.1 añade la fila `— | freeze_draft_write (ADR 0019) | confirmed` (sin token); (c) añade la sección:

```markdown
### 3.7 Escritura `draft` (ADR 0019)

Una tool `write_draft` se invoca con un nodo `tool` con `draft: true` y **sin `confirm`**. `freeze_draft_write` crea la acción directamente en `confirmed`: `write_node_id` es el nodo, `confirm_node_id`, token y vencimiento son nulos e `idempotency_key = action_id`. Con una acción `confirmed` de ese nodo en el flow activo (reentrada tras un `step_up_required`) conserva la acción congelada y sus args. Lanza `ValueError` si el nodo no es draft o la tool no es la `write_draft` que declara, e `IllegalTransition` sin flow activo. Desde ahí siguen `execute_write` (commit 1, tool, commit 2), `verify` y la recuperación `executing → verify` exactamente como en §3.4 a §3.6. `invalidate` cancela una acción draft `confirmed` que aún no corrió; `expire_tokens` la ignora (no tiene token).
```

- [ ] **Step 6: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`

```bash
git add agent_core/actions tests/m03/test_draft_write.py docs/specs/motor/m03-acciones.md
git commit -F - <<'EOF'
feat(actions): freeze_draft_write y ejecucion de una escritura draft en M3

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 12: M2 ejecuta el nodo `draft` y M4 lo recupera

**Files:**
- Modify: `agent_core/interpreter/handlers/write.py`, `agent_core/turn/recovery.py`
- Test: `tests/m02/test_write.py`, `tests/m04/test_recovery.py`
- Modify (docs): `docs/specs/motor/m02-interprete.md`, `docs/specs/motor/m04-ciclo-del-turno.md`

**Interfaces:**
- Consumes: `ActionManager.freeze_draft_write` y `execute_write` con nodo draft (Task 11).
- Produces: `handle_write` ejecuta un nodo `draft` (resuelve `args`, congela, ejecuta); `position_at_verify` encuentra el nodo de una acción draft por `write_node_id`.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/m02/test_write.py` (el archivo ya importa `ActionState`, `Stop`, `Scripted`, `ToolStatus`, `World`, `flow`, `tool_def`, y define `VERIFY`, `TAIL`, `_types`):

```python
DRAFT_WRITE = {"id": "guardar", "type": "tool",
               "config": {"draft": True, "tool": "guardar@1.0.0", "args": {"titulo": "mejorar", "n": 1},
                          "save_as": "borrador"},
               "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}}


def _draft_world(*, write_script: tuple[Scripted, ...] = ()) -> tuple[World, Flow]:
    w = World()
    w.add_tool(tool_def("guardar", "write_draft"), script=write_script,
               handler=lambda a: {"status": "Open", "id": "b-1", **a})
    readback = tool_def("obtener")
    w.add(readback)
    w.tools.register_readback(readback, of=w.tools_ref("guardar"))
    return w, flow(DRAFT_WRITE, VERIFY, *TAIL)


def test_draft_write_runs_without_a_confirm_and_verifies() -> None:
    w, f = _draft_world()
    done = w.step(w.persist(w.state(f)))
    assert done.stop is Stop.terminal and done.end_outcome is not None
    [action] = done.state.actions
    assert (action.state, action.confirm_node_id, action.write_node_id) == (ActionState.verified, None,
                                                                            "guardar")
    assert set(done.state.facts) == {"borrador", "pqr_ok"}
    persisted = _types(w.store.events["run-0001"])
    assert persisted == ["action_dispatched", "tool_called"]  # sin action_confirmed
    assert "action_verified" in _types(done.events)


def test_draft_write_with_an_uncertain_result_is_resolved_by_the_verify() -> None:
    w, f = _draft_world(write_script=(Scripted(ToolStatus.uncertain, error="timeout"),))
    done = w.step(w.persist(w.state(f)))
    assert [a.state for a in done.state.actions] == [ActionState.verified]
    assert len([c for c in w.tools.calls if c.tool == w.tools_ref("guardar")]) == 1  # una sola escritura
```

(importa `Flow` de `agent_core.domain` si el archivo no lo tiene ya: el import actual es `from agent_core.domain import ActionState, AuthLevel, EngineEvent, Flow, RunState`, así que ya está.)

Añade al final de `tests/m04/test_recovery.py` (importa `from testing.builders import action, run_state` — `action` ya está importado):

```python
def _draft_action(node: str = "guardar", flow: str = "borrador@1.0.0") -> Any:
    return action(flow=flow, state="executing", confirm_node_id=None, write_node_id=node,
                  confirmation_token_hash=None, token_exp=None)


def test_position_at_verify_para_una_escritura_draft() -> None:
    from testing.builders import run_state

    flow = Flow.model_validate({"id": "borrador", "version": "1.0.0", "priority": 1, "nodes": [
        {"id": "guardar", "type": "tool",
         "config": {"draft": True, "tool": "guardar@1.0.0", "args": {}, "save_as": "b"},
         "next": {"ok": "verificar", "uncertain": "verificar", "denied": "fin"}},
        {"id": "verificar", "type": "verify",
         "config": {"readback": "leer@1.0.0", "by": "idempotency_key", "predicate": True, "save_as": "v"},
         "next": {"verified": "fin", "failed": "fin"}},
        {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}]})
    state = run_state(active_flow={"flow": "borrador@1.0.0", "node_id": "guardar"}, actions=[_draft_action()])
    moved = position_at_verify(state, ["action-0001"], flow)
    assert moved.active_flow is not None and moved.active_flow.node_id == "verificar"


def test_una_accion_draft_no_se_asocia_a_una_escritura_con_confirm() -> None:
    from testing.builders import run_state

    w = World()
    flow = w.registry.get(EntityRef(id="disputa", version="1.0.0"), Flow)  # su escritura es de la forma confirm
    state = run_state(active_flow={"flow": "disputa@1.0.0", "node_id": "radicar"},
                      actions=[_draft_action(node="radicar", flow="disputa@1.0.0")])
    with pytest.raises(LookupError):
        position_at_verify(state, ["action-0001"], flow)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/m02/test_write.py tests/m04/test_recovery.py -v`
Expected: FAIL (`handle_write` intenta `execute_write` sin acción: `IllegalTransition`; `position_at_verify` no encuentra el nodo de la acción draft).

- [ ] **Step 3: Implement M2**

`agent_core/interpreter/handlers/write.py`: reemplaza `_action_tool` y `handle_write` por

```python
def _action_tool(state: RunState, node: WriteToolNode) -> EntityRef:
    for action in reversed(state.actions):
        if node.config.draft:
            if action.write_node_id == node.id:
                return action.tool
        elif node.config.action_from is not None and action.confirm_node_id == node.config.action_from:
            return action.tool
    raise IllegalTransition(f"no hay acción para el nodo de escritura {node.id}")


def handle_write(node: WriteToolNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    action_ctx = build_action_context(state, ctx)
    if node.config.draft:
        assert node.config.tool is not None  # `WriteToolConfig` lo garantiza para `draft`
        try:
            args = resolve_args(state, node.config.args)
        except MissingPath:
            return escalate_now(state, ctx, "validation_failed")
        definition = ctx.tools.definition(exact_ref(ctx, EntityKind.tool, node.config.tool))
        state = ctx.actions.freeze_draft_write(state, node, args, definition, action_ctx)
    # Los eventos que devuelve `execute_write` ya los persistió el `EventRecorder` de M3: no se agregan (§6).
    state, result, _persisted = ctx.actions.execute_write(state, node, action_ctx)
    tool = _action_tool(state, node)
    if result == "step_up_required":
        level = ctx.tools.definition(tool).min_auth_level
        return request_step_up(state, ctx, node.id, level, node.config.step_up_max_attempts, [])
    events: list[EngineEvent] = [Events(ctx).access_denied(state, tool)] if result == "denied" else []
    if result != "denied":
        state = clear_attempts(state, node.id)
    return NodeResult(state, result_key=result, events=events)
```

- [ ] **Step 4: Implement M4**

`agent_core/turn/recovery.py`: importa `Action` (`from agent_core.domain import Action, Flow, IllegalTransition, RunState, WriteToolNode, node_kind`), añade antes de `position_at_verify`

```python
def _writes_action(node: WriteToolNode, action: Action) -> bool:
    """El nodo de escritura de una acción: por `write_node_id` si es draft, por `action_from` si tiene confirm."""
    if action.write_node_id is not None:
        return node.config.draft and node.id == action.write_node_id
    return node.config.action_from is not None and node.config.action_from == action.confirm_node_id
```

y en `position_at_verify` reemplaza `if node.config.action_from is not None and node.config.action_from == action.confirm_node_id:` (el guard de la Task 7) por `if _writes_action(node, action):` y el `raise LookupError(...)` final por

```python
    raise LookupError(f"{flow.id}: no hay nodo de escritura para la acción {action.action_id}")
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/m02 tests/m03 tests/m04 -v`
Expected: PASS

- [ ] **Step 6: Documentar en m02 y m04**

`docs/specs/motor/m02-interprete.md` §3.3 (handlers): añade «**`tool` de escritura `draft` (ADR 0019):** `handle_write` resuelve los `args` del nodo (`MissingPath` escala con `validation_failed`, como `confirm`), llama a `ctx.actions.freeze_draft_write` y luego a `execute_write`; el resto (`ok`/`uncertain` → `verify`, `denied`, `step_up_required`) es el de la escritura con confirm.»

`docs/specs/motor/m04-ciclo-del-turno.md`: en el apartado de recuperación (paso 5, `position_at_verify`) añade «Una acción con `write_node_id` (escritura draft) se asocia a su nodo por ese id; una con `confirm_node_id`, por `action_from`. Una acción draft nunca se asocia a una escritura con confirm.»

- [ ] **Step 7: Verification gates and commit**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo en verde.

```bash
git add agent_core/interpreter agent_core/turn tests/m02 tests/m04 docs/specs/motor
git commit -F - <<'EOF'
feat(interpreter): M2 ejecuta la escritura draft y M4 la recupera por write_node_id

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

# Fase 5 — Adaptador del constructor

## Task 13: `BuilderToolExecutor`

**Files:**
- Create: `agent_core/composition/builder_tools.py`
- Modify: `agent_core/composition/__init__.py`, `agent_core/registry/__init__.py`
- Test: `tests/composition/test_builder_tools.py`

**Interfaces:**
- Consumes: `RegistryService` (con `idempotency_key`, `audit`, `get_write`), `AuditContext`, `Origin`, `EntityDraft`, `RegistryError`, `RegistryErrorCode`, `Proposal`; `DRAFT_OUTPUT_SCHEMA` de `agent_core.flows`.
- Produces:
  - `BUILDER_TOOL_DEFS: Mapping[str, ToolDef]` con las diez tools `registry/<nombre>@1.0.0`: `create_proposal`, `put_draft`, `freeze`, `reopen`, `evaluate` (`write_draft`), `validate` (`compute`) y `get_proposal`, `get_entity`, `list_versions`, `get_write` (`read`). Ninguna aprueba, publica, promueve ni revoca.
  - `BuilderToolExecutor(service: RegistryService, actor: Principal, ids: IdSource)` que implementa `ToolExecutor`: `definition(tool)` y `execute(tool, args, bound_params, ctx, idempotency_key=None)`.
  - Mapeo de errores: `forbidden_role`, `step_up_required` y `quota_exceeded` → `denied`; otro `RegistryError` → `uncertain` (escrituras) o `error` (lecturas), con `error = <código>`; argumentos mal formados → `denied` con `error = "invalid_args"`.

- [ ] **Step 1: Write the failing tests**

Crea `tests/composition/test_builder_tools.py`:

```python
"""El adaptador del constructor: permisos por su propia credencial, idempotencia y auditoría del run."""

from typing import Any

import pytest

from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
from agent_core.domain import EntityRef, Principal
from agent_core.domain.schema import unsupported_keyword
from agent_core.ports import ToolCallContext, ToolResult, ToolStatus
from agent_core.registry import Quotas, RegistryService
from testing.builders import principal
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _ref(name: str) -> EntityRef:
    return EntityRef(id=f"registry/{name}", version="1.0.0")


def _ctx(run_principal: Principal | None = None) -> ToolCallContext:
    who = run_principal or principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                                     attrs={"actor": "human"})
    return ToolCallContext(run_id="run-1", release="rel-x", principal=who)


def _executor(w: World, actor: Principal | None = None, service: RegistryService | None = None
              ) -> BuilderToolExecutor:
    return BuilderToolExecutor(service or w.service, actor or bot(), FakeIds())


def _value(result: ToolResult) -> dict[str, Any]:
    assert isinstance(result.result_full, dict), result
    return result.result_full


def _create(ex: BuilderToolExecutor, key: str = "k1", origin: str = "builder_chat") -> ToolResult:
    return ex.execute(_ref("create_proposal"), {"agent_id": AGENT, "origin": origin, "title": "t"}, {},
                      _ctx(), key)


def test_create_proposal_is_idempotent_and_audited_with_the_run_principal() -> None:
    w = World()
    ex = _executor(w)
    first, again = _create(ex), _create(ex)
    assert first.status is ToolStatus.ok and again.status is ToolStatus.ok
    assert _value(first)["proposal_id"] == _value(again)["proposal_id"]
    record = _value(ex.execute(_ref("get_write"), {"idempotency_key": "k1"}, {}, _ctx()))
    assert (record["op"], record["run_id"], record["on_behalf_of"]) == ("create_proposal", "run-1", "builder:ana")


def test_a_write_without_a_key_is_refused() -> None:
    w = World()
    with pytest.raises(ValueError):
        _executor(w).execute(_ref("freeze"), {"proposal_id": "x"}, {}, _ctx())


def test_permissions_come_from_the_executor_credential_not_from_the_run() -> None:  # Review Focus 7
    w = World()
    no_role = principal(type="builder", id="sin-rol", roles=[], attrs={})
    result = _create(_executor(w, actor=no_role))  # el run lo hace un supervisor con constructor y aprobador
    assert (result.status, result.error) == (ToolStatus.denied, "forbidden_role")


def test_the_quota_is_a_denial_without_effect() -> None:  # Review Focus 6
    w = World()
    limited = RegistryService(w.store, w.evaluator, w.clock, w.ids, quotas=Quotas(proposals_per_day=1))
    ex = _executor(w, service=limited)
    assert _create(ex, "a", "auto_detect").status is ToolStatus.ok
    second = _create(ex, "b", "auto_detect")
    assert (second.status, second.error) == (ToolStatus.denied, "quota_exceeded")


def test_a_registry_rejection_of_a_write_is_uncertain_and_leaves_no_record() -> None:  # Review Focus 6
    w = World()
    ex = _executor(w)
    proposal_id = _value(_create(ex))["proposal_id"]
    bad = prompt_draft(version="0.1.0").model_dump(mode="json")  # versión inválida: freeze la rechaza
    put = ex.execute(_ref("put_draft"), {"proposal_id": proposal_id, "expected_rev": 0, "changes": [bad]}, {},
                     _ctx(), "k2")
    assert put.status is ToolStatus.ok
    frozen = ex.execute(_ref("freeze"), {"proposal_id": proposal_id}, {}, _ctx(), "k3")
    assert (frozen.status, frozen.error) == (ToolStatus.uncertain, "validation_failed")
    readback = ex.execute(_ref("get_write"), {"idempotency_key": "k3"}, {}, _ctx())
    assert readback.status is ToolStatus.ok and readback.result_full is None  # el verify lo verá como failed


def test_malformed_arguments_do_not_raise() -> None:  # Review Focus 6
    w = World()
    result = _executor(w).execute(_ref("put_draft"), {"proposal_id": 7}, {}, _ctx(), "k")
    assert (result.status, result.error) == (ToolStatus.denied, "invalid_args")


def test_the_whole_constructor_cycle_through_the_tools() -> None:
    w = World()
    ex = _executor(w)
    pid = _value(_create(ex))["proposal_id"]
    changes = [prompt_draft().model_dump(mode="json"), SUITE.model_dump(mode="json")]
    assert ex.execute(_ref("put_draft"), {"proposal_id": pid, "expected_rev": 0, "changes": changes}, {}, _ctx(),
                      "k2").status is ToolStatus.ok
    assert _value(ex.execute(_ref("validate"), {"proposal_id": pid}, {}, _ctx()))["valid"] is True
    assert ex.execute(_ref("freeze"), {"proposal_id": pid}, {}, _ctx(), "k3").status is ToolStatus.ok
    evaluated = ex.execute(_ref("evaluate"), {"proposal_id": pid, "suite_id": "disputas-suite"}, {}, _ctx(),
                           "k4")
    assert _value(evaluated)["verdict"] == "pass"
    assert _value(ex.execute(_ref("get_write"), {"idempotency_key": "k4"}, {}, _ctx()))["verdict"] == "pass"
    assert _value(ex.execute(_ref("get_proposal"), {"proposal_id": pid}, {}, _ctx()))["state"] == "evaluated"


def test_the_builder_has_no_tool_to_approve_publish_promote_or_revoke() -> None:
    names = {tool_id.split("/")[1] for tool_id in BUILDER_TOOL_DEFS}
    assert names == {"create_proposal", "put_draft", "freeze", "reopen", "evaluate", "validate",
                     "get_proposal", "get_entity", "list_versions", "get_write"}
    assert not names & {"approve", "reject", "publish", "promote", "revoke", "import_seed"}


def test_every_tool_is_documented_and_every_write_declares_its_readback() -> None:
    for definition in BUILDER_TOOL_DEFS.values():
        assert definition.description and definition.args_schema is not None
        assert unsupported_keyword(definition.args_schema) is None
        if definition.risk_class.value == "write_draft":
            assert definition.readback_by == "idempotency_key"


def test_an_unknown_tool_or_version_is_a_key_error() -> None:
    ex = _executor(World())
    with pytest.raises(KeyError):
        ex.definition(EntityRef(id="registry/publish", version="1.0.0"))
    with pytest.raises(KeyError):
        ex.definition(EntityRef(id="registry/freeze", version="2.0.0"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/composition/test_builder_tools.py -v`
Expected: FAIL (`ModuleNotFoundError: agent_core.composition.builder_tools`).

- [ ] **Step 3: Implement**

En `agent_core/registry/__init__.py` importa también `Proposal` (`from agent_core.registry.models import (..., Proposal, ...)`) y agrégalo a `__all__` (orden alfabético).

Crea `agent_core/composition/builder_tools.py`:

```python
"""`BuilderToolExecutor`: las tools del agente constructor sobre `RegistryService` (ADR 0019 §4; spec write-draft §8).

Decide por su propia credencial (rol `constructor`, sin `attrs.actor = "human"`), nunca por la del run: el
principal del run solo viaja como `AuditContext`. No existe ninguna tool de aprobar, publicar, promover ni
revocar: publicar es un gate humano externo (ADR 0006)."""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

from pydantic import ValidationError

from agent_core.domain import EntityRef, JsonValue, Principal, ToolDef
from agent_core.flows import DRAFT_OUTPUT_SCHEMA
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus
from agent_core.registry import (
    AuditContext,
    EntityDraft,
    Origin,
    Proposal,
    RegistryError,
    RegistryErrorCode,
    RegistryService,
)

Args = dict[str, JsonValue]
Handler = Callable[[Args, str | None, AuditContext | None], JsonValue]

_STR: dict[str, JsonValue] = {"type": "string"}
_CHANGES = cast(dict[str, JsonValue], DRAFT_OUTPUT_SCHEMA["properties"])["changes"]


def _schema(required: list[str], properties: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": properties}


_PROPOSAL = _schema(["proposal_id"], {"proposal_id": _STR})


def _tool(name: str, risk: str, description: str, schema: dict[str, JsonValue]) -> ToolDef:
    extra: dict[str, Any] = {"readback_by": "idempotency_key"} if risk == "write_draft" else {}
    return ToolDef.model_validate({
        "id": f"registry/{name}", "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
        "idempotent": True, "description": description, "args_schema": schema, **extra})


BUILDER_TOOL_DEFS: Mapping[str, ToolDef] = MappingProxyType({d.id: d for d in (
    _tool("create_proposal", "write_draft", "Crea una propuesta de cambio en el registry (borrador).",
          _schema(["agent_id", "origin", "title"], {
              "agent_id": _STR, "title": _STR,
              "origin": {"type": "string", "enum": ["manual", "builder_chat", "auto_detect", "import"]}})),
    _tool("put_draft", "write_draft", "Reemplaza los cambios del borrador de una propuesta.",
          _schema(["proposal_id", "expected_rev", "changes"], {
              "proposal_id": _STR, "expected_rev": {"type": "integer"}, "changes": _CHANGES})),
    _tool("freeze", "write_draft", "Congela la propuesta y arma la candidata.", _PROPOSAL),
    _tool("reopen", "write_draft", "Reabre una propuesta congelada para seguir editando.", _PROPOSAL),
    _tool("evaluate", "write_draft", "Evalúa la candidata de la propuesta contra una suite.",
          _schema(["proposal_id", "suite_id"],
                  {"proposal_id": _STR, "suite_id": _STR, "suite_version": _STR})),
    _tool("validate", "compute", "Valida el borrador sin congelarlo.", _PROPOSAL),
    _tool("get_proposal", "read", "Lee una propuesta, sus cambios y su última evaluación.", _PROPOSAL),
    _tool("get_entity", "read", "Lee una versión de una entidad del registry.",
          _schema(["kind", "entity_id"], {"kind": _STR, "entity_id": _STR, "version": _STR})),
    _tool("list_versions", "read", "Lista las versiones de una entidad del registry.",
          _schema(["kind", "entity_id"], {"kind": _STR, "entity_id": _STR})),
    _tool("get_write", "read", "Consulta una escritura del constructor por su clave de idempotencia.",
          _schema(["idempotency_key"], {"idempotency_key": _STR})),
)})

# Bloqueos de política antes de cualquier efecto (ADR 0007 §6: `denied`).
_DENIED = frozenset({RegistryErrorCode.forbidden_role, RegistryErrorCode.step_up_required,
                     RegistryErrorCode.quota_exceeded})


def _failure(definition: ToolDef, code: RegistryErrorCode) -> ToolStatus:
    if code in _DENIED:
        return ToolStatus.denied
    return ToolStatus.uncertain if definition.is_write else ToolStatus.error


def _text(args: Args, name: str) -> str:
    value = args[name]
    if not isinstance(value, str):
        raise TypeError(name)
    return value


def _optional_text(args: Args, name: str) -> str | None:
    value = args.get(name)
    if value is not None and not isinstance(value, str):
        raise TypeError(name)
    return value


def _number(args: Args, name: str) -> int:
    value = args[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(name)
    return value


def _proposal(p: Proposal) -> dict[str, JsonValue]:
    return {"proposal_id": p.proposal_id, "rev": p.rev, "state": p.state.value,
            "base_release_id": p.base_release_id}


class BuilderToolExecutor:
    def __init__(self, service: RegistryService, actor: Principal, ids: IdSource) -> None:
        self._service, self._actor, self._ids = service, actor, ids
        self._handlers: Mapping[str, Handler] = MappingProxyType({
            "registry/create_proposal": self._create_proposal, "registry/put_draft": self._put_draft,
            "registry/freeze": self._freeze, "registry/reopen": self._reopen,
            "registry/evaluate": self._evaluate, "registry/validate": self._validate,
            "registry/get_proposal": self._get_proposal, "registry/get_entity": self._get_entity,
            "registry/list_versions": self._list_versions, "registry/get_write": self._get_write})

    def definition(self, tool: EntityRef) -> ToolDef:
        found = BUILDER_TOOL_DEFS.get(tool.id)
        if found is None or found.version != tool.version:
            raise KeyError(str(tool))
        return found

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        definition = self.definition(tool)
        if definition.is_write and idempotency_key is None:
            raise ValueError("una escritura siempre lleva idempotency_key (ADR 0007)")
        call_id = self._ids.new_id(IdKind.call)
        try:
            audit = self._audit(ctx)
            value = self._handlers[tool.id](args, idempotency_key, audit)
        except RegistryError as exc:
            return ToolResult(status=_failure(definition, exc.code), call_id=call_id, error=exc.code.value)
        except (ValidationError, KeyError, TypeError, ValueError):  # argumentos mal formados: no hubo efecto
            return ToolResult(status=ToolStatus.denied, call_id=call_id, error="invalid_args")
        return ToolResult(status=ToolStatus.ok, result_full=value, call_id=call_id)

    @staticmethod
    def _audit(ctx: ToolCallContext) -> AuditContext | None:
        """El principal del run viaja solo como actor de auditoría, nunca como fuente de permisos."""
        who = ctx.principal
        if who.id is None:
            return None
        return AuditContext(run_id=ctx.run_id, on_behalf_of=f"{who.type.value}:{who.id}")

    def _create_proposal(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        p = self._service.create_proposal(self._actor, _text(args, "agent_id"), Origin(_text(args, "origin")),
                                          _text(args, "title"), idempotency_key=key, audit=audit)
        return _proposal(p)

    def _put_draft(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        raw = args["changes"]
        if not isinstance(raw, list):
            raise TypeError("changes")
        changes = [EntityDraft.model_validate(item) for item in raw]
        p = self._service.put_draft(self._actor, _text(args, "proposal_id"), changes,
                                    _number(args, "expected_rev"), idempotency_key=key, audit=audit)
        return _proposal(p)

    def _freeze(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        view = self._service.freeze(self._actor, _text(args, "proposal_id"), idempotency_key=key, audit=audit)
        return {"candidate_hash": view.candidate_hash, "release_id_preview": view.release_id_preview}

    def _reopen(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        return _proposal(self._service.reopen(self._actor, _text(args, "proposal_id"), idempotency_key=key,
                                              audit=audit))

    def _evaluate(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        report = self._service.evaluate(self._actor, _text(args, "proposal_id"), _text(args, "suite_id"),
                                        _optional_text(args, "suite_version"), idempotency_key=key,
                                        audit=audit)
        return {"verdict": report.verdict}

    def _validate(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        report = self._service.validate(self._actor, _text(args, "proposal_id"))
        shown: list[JsonValue] = [{"rule": v.rule, "node_id": v.node_id, "message": v.message}
                                  for v in report.violations[:20]]
        return {"valid": not report.violations, "candidate_hash": report.candidate_hash, "violations": shown}

    def _get_proposal(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        detail = self._service.get_proposal(_text(args, "proposal_id"))
        changes: list[JsonValue] = [{"kind": c.kind, "id": c.id} for c in detail.changes]
        return {**_proposal(detail.proposal), "candidate_hash": detail.proposal.candidate_hash,
                "changes": changes, "last_verdict": detail.last_eval.verdict if detail.last_eval else None}

    def _get_entity(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        found = self._service.get_entity(_text(args, "kind"), _text(args, "entity_id"),
                                         _optional_text(args, "version"))
        return found.model_dump(mode="json")

    def _list_versions(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        versions = self._service.list_versions(_text(args, "kind"), _text(args, "entity_id"))
        shown: list[JsonValue] = [{"version": v.ref.version, "created_by": v.created_by} for v in versions]
        return shown

    def _get_write(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        record = self._service.get_write(_text(args, "idempotency_key"))
        return None if record is None else record.model_dump(mode="json")
```

En `agent_core/composition/__init__.py` importa `BUILDER_TOOL_DEFS` y `BuilderToolExecutor` de `agent_core.composition.builder_tools` y agrégalos a `__all__` siguiendo el estilo del archivo.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/composition/test_builder_tools.py tests/registry -v`
Expected: PASS

- [ ] **Step 5: Verification gates and commit**

Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: sin errores (composición puede importar `registry`, `flows` y `ports`).

```bash
git add agent_core/composition agent_core/registry/__init__.py tests/composition/test_builder_tools.py
git commit -F - <<'EOF'
feat(composition): BuilderToolExecutor, las tools del constructor sobre RegistryService

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

## Task 14: Hito 1 de punta a punta, documentos y verificación de las fases 1 a 5

**Files:**
- Test: `tests/composition/test_write_draft_hito1.py`
- Modify (docs): `docs/specs/2026-09-29-registry-design.md`, `docs/adr/0019-agentes-internos.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`, `docs/superpowers/specs/2026-09-30-write-draft-design.md`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: la prueba del hito 1 de la spec (§8, §14): un flow draft crea una propuesta y escribe un borrador con `act → verify`, sin `confirm`, de forma idempotente y auditada.

- [ ] **Step 1: Write the test**

Crea `tests/composition/test_write_draft_hito1.py`:

```python
"""Hito 1 de la spec write-draft: un flow con escrituras `draft` crea una propuesta y escribe un borrador de
punta a punta (motor real de M2 + M3, `BuilderToolExecutor` real, registry en memoria), sin `confirm`."""

from typing import Any

from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
from agent_core.domain import ActionState
from agent_core.interpreter import Stop
from testing.builders import principal
from tests.m02.harness import World as EngineWorld
from tests.m02.harness import flow
from tests.registry.helpers import bot, prompt_draft
from tests.registry.service_world import World as RegistryWorld

_NEXT_FAIL = {"verified": "fin", "failed": "esc"}
NODES: list[dict[str, Any]] = [
    {"id": "crear", "type": "tool",
     "config": {"draft": True, "tool": "registry/create_proposal@1.0.0",
                "args": {"agent_id": "atencion", "origin": "builder_chat", "title": "mejorar radicado"},
                "save_as": "propuesta"},
     "next": {"ok": "verificar_crear", "uncertain": "verificar_crear", "denied": "esc"}},
    {"id": "verificar_crear", "type": "verify",
     "config": {"readback": "registry/get_write@1.0.0", "by": "idempotency_key",
                "predicate": {"==": [{"var": "readback.op"}, "create_proposal"]}, "save_as": "crear_ok"},
     "next": {"verified": "guardar", "failed": "esc"}},
    {"id": "guardar", "type": "tool",
     "config": {"draft": True, "tool": "registry/put_draft@1.0.0",
                "args": {"proposal_id": "facts.propuesta.value.proposal_id",
                         "expected_rev": "facts.propuesta.value.rev",
                         "changes": [prompt_draft().model_dump(mode="json")]},
                "save_as": "borrador"},
     "next": {"ok": "verificar_guardar", "uncertain": "verificar_guardar", "denied": "esc"}},
    {"id": "verificar_guardar", "type": "verify",
     "config": {"readback": "registry/get_write@1.0.0", "by": "idempotency_key",
                "predicate": {"==": [{"var": "readback.op"}, "put_draft"]}, "save_as": "guardar_ok"},
     "next": _NEXT_FAIL},
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def test_a_draft_flow_creates_a_proposal_and_writes_a_draft_end_to_end() -> None:
    registry = RegistryWorld()
    engine = EngineWorld()
    engine.add(*BUILDER_TOOL_DEFS.values())
    executor = BuilderToolExecutor(registry.service, bot(), engine.ids)
    supervisor = principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                           attrs={"actor": "human"})
    f = flow(*NODES)
    done = engine.step(engine.persist(engine.state(f, principal=supervisor)), tools=executor)

    assert done.stop is Stop.terminal and done.end_outcome is not None
    assert [a.state for a in done.state.actions] == [ActionState.verified, ActionState.verified]
    assert all(a.confirm_node_id is None and a.write_node_id for a in done.state.actions)  # sin confirm
    created = done.state.facts["propuesta"].value
    assert isinstance(created, dict)
    detail = registry.service.get_proposal(str(created["proposal_id"]))
    assert [(c.kind, c.id) for c in detail.changes] == [("prompt", "p/resumen_radicado")]
    assert (detail.proposal.rev, detail.proposal.origin.value) == (1, "builder_chat")
    persisted = [e.type for e in engine.store.events["run-0001"]]
    assert persisted.count("action_dispatched") == 2 and "action_confirmed" not in persisted
    second = done.state.actions[1]
    record = registry.service.get_write(second.idempotency_key)  # la clave es el action_id
    assert record is not None
    assert (record.op, record.run_id, record.on_behalf_of) == ("put_draft", "run-0001", "builder:ana")
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/composition/test_write_draft_hito1.py -v`
Expected: PASS. Si falla por un motivo de cableado (por ejemplo, `run_state(principal=...)` rechaza un `builder` con un `subject` de cliente), ajusta el *fixture* de la prueba (quita el override de `principal` o pasa `subject=None`), no el código de producción; si falla por una causa del motor, **detente y reporta** (Hito 1 no se da por cumplido sin esta prueba en verde).

- [ ] **Step 3: Documentos finales de las fases 1 a 5**

- `docs/specs/2026-09-29-registry-design.md` §18: en la fila 2 añade «**Construido (2026-09-30):** `BuilderToolExecutor` en `agent_core/composition/builder_tools.py`»; en la fila 7 «**Construido:** `RiskClass.write_draft` y AG-02»; en la fila 10 «Topes 10 y 20 implementados; sigue diferido el tope de costo».
- `docs/adr/0019-agentes-internos.md`: en la línea «Estado» reemplaza «**Pendiente:** clase `write_draft` (G0-23, AG-02, ruta sin `confirm` en M3), adaptador real de `AgentPort` y los agentes mismos» por «**Implementado también (2026-09-30, fases 1 a 5 de la spec write-draft):** clase `write_draft` (M0, M1 con G0-05, G0-22, G0-23, G0-25 y AG-02, M2, M3 y M4), borradores idempotentes y topes en el registry y `BuilderToolExecutor`; el adaptador real de `AgentPort` (`LLMAgentPort`) ya existía. **Pendiente:** replay del bucle en M11, los dos agentes del constructor y `await_approval`».
- `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`: en la tabla de estado, #17 pasa a «**Parcial: fases 1 a 5 hechas (2026-09-30); siguen el replay de M11, los agentes y `await_approval`**» y el encabezado «sigue abierto #17 (medio)» se mantiene.
- `docs/superpowers/specs/2026-09-30-write-draft-design.md`: cambia la línea de estado por «**aprobada; fases 1 a 5 implementadas (2026-09-30)**; fases 6 a 8 pendientes».

- [ ] **Step 4: Verificación completa de las fases 1 a 5**

Run (con `docker compose up -d postgres`):

```bash
AGENTCORE_REQUIRE_POSTGRES=1 uv run pytest
uv run lint-imports
uv run mypy
uv run ruff check .
uv run agentcore contracts --check
```

Expected: todo en verde. Copia los resultados reales (conteo de pruebas pasadas, omitidas y fallidas) al informe final; si algo falla o no se corrió, dilo.

- [ ] **Step 5: Commit**

```bash
git add tests/composition/test_write_draft_hito1.py docs
git commit -F - <<'EOF'
test(composition): hito 1 de write_draft de punta a punta y documentos de las fases 1 a 5

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 6: Informa al usuario y detente**

Escribe al usuario el resumen de las fases 1 a 5: qué quedó hecho (por commit), las decisiones tomadas por tu cuenta (si las hubo), las desviaciones de la spec corregidas en la Task 0 y los resultados reales de la verificación. Pregunta si autoriza el push de `feat/write-draft` y el PR, y si seguimos con la fase 6 (plan nuevo). No hagas push ni PR sin su autorización.

---

## Cobertura de la spec (autoevaluación)

| Spec | Tarea |
|---|---|
| §4.1 a §4.3 tabla `reg_draft_writes`, operaciones con clave, `get_write` | 2, 3, 4 |
| §4.4 regla 8 (tres pruebas) | 6 |
| §4.5 topes | 1, 5 |
| §5 M0 | 7 |
| §6 M1 (G0-05, G0-23, reclamos, AG-02, G0-25, G0-22) | 8, 9, 10 |
| §7 M3, M2 (y M4, añadido en la Task 0) | 11, 12 |
| §8 `BuilderToolExecutor`, credencial, auditoría, hito 1 | 13, 14 |
| §9 M11 replay, §10 agentes, §11 `await_approval` | **fuera de este plan** (fases 6 a 8, plan propio) |
| §13 documentos | 6, 7, 10, 11, 12, 14 |
| §16 definición de terminado | verificación de la Task 14 |
