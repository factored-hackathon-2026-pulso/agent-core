# M10 — Escalamiento y handoff: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `agent_core.handoff`: `HandoffService.escalate` (paquete estructurado, evento `escalated`, mensaje `handoff_created` al outbox, cierre del run para el bot y mensaje de traspaso), `get` (paquete renderizado con los permisos del lector vía M7) y `record_resolution` (una sola resolución por handoff), con T-M10-01…08 en verde.

**Architecture:** M10 solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de `agent_core.views` (contrato `handoff` de `.importlinter`). `escalate` es una función de construcción sin E/S propia salvo `uow.put_handoff`, que recibe la UoW del turno para que el paquete entre en la misma transacción que el cierre del run (M4 guarda el run, agrega los eventos y encola el outbox con lo que M10 devuelve). El paquete persistido lleva solo vista `audit` (enmascarada, sin tokens reversibles); `get` vuelve a proyectar desde `RunState.facts` (vista `full`) con `ViewService.project` + `render` de M7 según el lector. Cada archivo tiene una responsabilidad: tipos, textos, proyección, construcción del paquete y servicio.

**Tech Stack:** Python 3.12, Pydantic v2 (tipos de M0), pytest, ruff, mypy strict, import-linter. Sin dependencias nuevas.

**Spec:** `docs/specs/motor/m10-escalamiento-y-handoff.md` (la Task 1 lo sube a rev. 2 con las decisiones de este plan). Contexto: `docs/specs/motor/00-indice.md` (§3 dependencias, §4 puertos, §5 dueños del estado, §6 eventos), ADR 0013 (evento saliente, cierre del run, transcript renderizado), ADR 0006 (delegación al asignar), ADR 0007 (outbox e idempotencia; el outbox se commitea con el turno), ADR 0008 (vistas), spec general §9 y M0 §2.8/§2.10/§2.11. Léelos antes de empezar: el spec manda sobre este plan.

## Global Constraints

- Python `>=3.12,<3.13` (ADR 0001). Sin colas, Redis ni vector DB.
- `agent_core.handoff` solo importa `agent_core.domain`, `agent_core.ports` y `agent_core.views` (interfaz pública, nunca `agent_core.views.<archivo>`); lo verifica `uv run lint-imports`.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random`, `secrets` ni `os.urandom`: la hora sale del `Clock` y todo ID de `IdSource.new_id` (lo verifica `ruff`, TID251).
- Cifras en `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads`; el paquete se serializa con `to_jsonable` de M0 (conserva `Decimal`).
- `reason_code` pertenece a `ReasonCode` de M0 o a los prefijos `rule:`, `policy:`, `interrupt:` (tipo `ReasonCodeStr`; ya lo valida `EscalationRequest`).
- El paquete **nunca incrusta el transcript**: solo `transcript_ref = "/v1/runs/{run_id}/transcript"`.
- Ningún valor de vista `full` sale de M10 salvo por `HandoffService.get` y solo después de `AuthzPort.authorize_subject` y `ViewService.render` (que consulta `can_read_field` con `purpose="handoff"`). Nada `full` va a eventos, al outbox, a logs ni al paquete persistido.
- El paquete persistido y todo texto que M10 fabrica (resumen, mensaje de traspaso) no contienen valores de campos: solo conteos, ids y referencias.
- El outbox y el cierre del run se commitean juntos o no se commitea ninguno: M10 nunca abre ni commitea la UoW del turno.
- `run_closed` lo emite solo M4 (M0 §2.10). M10 emite `escalated` y `handoff_resolved`, y construye el `OutboxMessage` `handoff_created`.
- Fixtures y pruebas solo con datos sintéticos (`example.test`, documentos inventados). Nunca datos reales del dataset ni credenciales del diccionario de datos.
- No tocar otros módulos. Si hace falta algo fuera de una interfaz pública, detente y pregunta.
- Comandos: `uv run pytest tests/m10`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore contracts --check`.
- `ruff`: `line-length = 110`. Si solo marca orden de imports (`I001`) o de `__all__` (`RUF022`), corrige con `uv run ruff check --fix`; cualquier otro hallazgo se corrige a mano sin cambiar la semántica del plan.
- Commits: mensajes en español, prefijo `feat(m10):` / `test(m10):` / `docs(m10):`, y terminan con `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (si el ejecutor es otro modelo, pone su nombre real).

## Decisiones de este plan (ambigüedades del spec resueltas con criterio; van al spec en la Task 1)

1. **`request_summary` (Abierto §11 del spec): plantilla determinista en el MVP**, por clave de `reason_code` (`low_confidence`, …, `rule`, `policy`, `interrupt`) y `locale`. Solo lleva conteos y el id del flow activo, **nunca valores de campos** (así el resumen puede persistirse y renderizarse sin pasar por M7). `citations` = nombres de los hechos verificados. Las plantillas por defecto viven en `agent_core/handoff/texts.py` (ES y PT). Sobrescribirlas desde `agent-registry` exige un campo nuevo en `EngineTemplates` (cambio de M0): queda como abierto; el DoD "plantillas en agent-registry" se cumple para el **mensaje de traspaso**, que sí sale de `agent.templates.handoff`.
2. **Firma de `escalate`.** El spec da `escalate(state, request, events_so_far)` y dice que el paquete se persiste, pero no da la UoW. Se agregan dos argumentos de solo nombre: `uow: UnitOfWork` (M10 solo llama `uow.put_handoff`; M4 hace `save_run`, `append_events` con lo que M10 devuelve y `enqueue_outbox`, y commitea) y `turn_id: str | None = None` (para el evento `escalated`). Cambia la llamada de M4 (§3.5 de su spec, una línea).
3. **`events_so_far` = todos los eventos del run hasta ahora**, en orden, incluidos los del turno en curso que aún no están persistidos (M4 concatena `AuditSink.read(run_id)` con los del turno). De ahí salen `call_id` de `tool_called` y `policy@v` de `rule_evaluated`. Los `decision_id` salen de `state.decisions` y los `page@v` de los hechos con `source.kind == "knowledge"`.
4. **Formato de `evidence_refs`:** prefijo por tipo para que sean inequívocas: `call:<call_id>`, `policy:<id>@<versión>`, `decision:<decision_id>`, `page:<ref>`. Sin duplicados, en orden de aparición.
5. **Qué se persiste.** El registro guardado con `uow.put_handoff` es `{"packet": <HandoffPacket>, "resolution": null}` (clase `HandoffRecord`). El paquete lleva vista `audit` (enmascarada; sin tokens reversibles) y `subject.ref` enmascarado (`***`). Como `audit` no permite des-enmascarar, `get` **reconstruye** cada valor desde `RunState` (vista `full`) con el lector: `ViewService.project` (tokeniza en un vault descartable) y `ViewService.render` por cada string; el vault descartable no toca `RunState.token_map`. Si el run ya no existe → `404 not_found`.
6. **Autorización de `get`/`record_resolution`:** `AuthzPort.authorize_subject(reader, on_behalf_of, run.subject)` (M9 es dueño de la tabla §4.2: asesor con delegación vigente sobre el subject, `service` con scope). `allowed=False` → `EngineError(subject_forbidden)` (403). M10 no reimplementa la tabla. Handoff inexistente → `not_found` (404).
7. **`record_resolution` gana `on_behalf_of`** (argumento de solo nombre, opcional): un asesor no se autoriza sin delegación. La atomicidad y el "una sola vez" se obtienen en una UoW propia: `acquire_turn` con un lease corto (`resolve-<handoff_ref>`, 30 s) serializa dos resoluciones concurrentes (la perdedora recibe `409 turn_in_progress`), se relee el registro con el lease tomado, y marca `resolution` + agrega `handoff_resolved` en el mismo commit. El evento se agrega por el gancho `EventRecorder` (por defecto `uow.append_events` sin encadenar; M9/M11 inyectan el que encadena), igual que en M3. Las `notes` (texto libre del receptor) se guardan solo en el registro del handoff, **nunca** en el evento (`HandoffResolvedPayload` no las lleva).
8. **Precondiciones de `escalate`** (bugs de M4, no errores de usuario → `HandoffPreconditionError`, un `DomainError`): el run está `open` y ya no tiene acciones `proposed`/`confirmed` (M4 invocó `ActionManager.invalidate` antes). Un run ya escalado o cerrado no se escala otra vez.
9. **Estado que prepara M10** (índice §5, "M10 prepara, M4 aplica"): `status="escalated"`, `outcome=Outcome.escalated`, `handoff_ref`, `closed_at=now`, `last_activity_at=now`, `inactive_after=None`, `awaiting=none`, `awaiting_node_id=None`, `pending_offer=None`.
10. **`claimed_not_verified` = solo slots `claimed`.** Los slots `validated` no van al paquete (el spec solo pide los `claimed`).
11. **Paquete degradado (§5 del spec):** si construir el paquete completo lanza cualquier excepción, se escala igual con un paquete mínimo (`degraded_packet=True`: `reason_code`, hechos sin valores —id, nombre, origen—, `transcript_ref`). Persistir (`put_handoff`) que falle **no** se degrada: es falla de la transacción y se propaga.
12. **`open_questions`:** se copian de `RunState`, con una guarda: un texto en el que M7 (`find_clear_pii`) encuentra PII en claro se reemplaza por `***`. Quién las llena sigue abierto (M2/M10, índice §10).
13. **Idioma de los textos:** `state.locale`; si no hay texto para ese locale, cae a `es`. El mensaje de traspaso sale de `registry.get(agent.templates.handoff.require_exact(), Template)` con el texto del locale; si la plantilla no existe, no tiene ese locale o declara `reads` (M10 no sabe rellenar variables), se usa el mensaje por defecto de `texts.py`. Un fallo del registro nunca impide escalar.
14. **`reportable_attrs`** del outbox = `state.principal.attrs` filtrado por `AuthzPort.reportable_attrs()`.

## Trabajo en paralelo con M6 y M11

Otros agentes construyen a la vez M6 (`agent_core/guards`) y M11 (`agent_core/audit`). Para no colisionar:

- **Worktree propio:** `../agent-core-m10`, rama `feat/m10-escalamiento-y-handoff`, desde `main` (Task 1).
- **Archivos que M10 puede tocar:** `agent_core/handoff/**`, `tests/m10/**`, `docs/specs/motor/m10-escalamiento-y-handoff.md`, `docs/superpowers/plans/2026-09-29-m10-escalamiento-y-handoff.md`, y ediciones de una línea en `docs/specs/motor/00-indice.md` (§10) y `docs/specs/motor/m04-ciclo-del-turno.md` (§3.5).
- **Archivos que M10 NO toca:** `agent_core/guards/**`, `agent_core/audit/**`, `agent_core/domain/**`, `agent_core/ports/**`, `agent_core/views/**`, `testing/**`, `pyproject.toml`, `uv.lock`, `.importlinter`, `contracts/**`. Los dobles que M10 necesita (autorización, catálogo de campos, fábrica de mundo) viven en `tests/m10/helpers.py`.
- **Sin dependencia de M11:** el encadenado de eventos llega por el gancho `EventRecorder`; `escalate` no agrega eventos, los devuelve (M4 los pasa a M11).
- Si `main` recibe M6/M11 antes de cerrar, sincroniza la rama y repite la Task 9, Step 5 antes de abrir el PR.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `docs/specs/motor/m10-escalamiento-y-handoff.md` | spec rev. 2 (decisiones de arriba) |
| `docs/specs/motor/00-indice.md` | §10: `request_summary` pasa a resuelto |
| `docs/specs/motor/m04-ciclo-del-turno.md` | §3.5: nueva llamada a `escalate` |
| `agent_core/handoff/errors.py` | `HandoffPreconditionError` |
| `agent_core/handoff/packet.py` | `RequestSummary`, `FactView`, `SlotView`, `ActionView`, `HandoffPacket`, `Resolution`, `HandoffRecord` |
| `agent_core/handoff/texts.py` | plantillas ES/PT del resumen y del mensaje de traspaso; `reason_key`, `render_summary`, `default_handoff_message` |
| `agent_core/handoff/projection.py` | `Projector`: vista `audit` (persistida) y vista del lector (`get`) sobre `ViewService` |
| `agent_core/handoff/builder.py` | `build_packet`, `build_minimal_packet`, `evidence_refs`, vistas de hechos/slots/acciones |
| `agent_core/handoff/recorder.py` | `EventRecorder`, `append_events` (gancho de eventos fuera de un turno) |
| `agent_core/handoff/service.py` | `HandoffService` (`escalate`, `get`, `record_resolution`) |
| `agent_core/handoff/__init__.py` | interfaz pública |
| `tests/m10/helpers.py` | catálogo sintético, `HandoffAuthz`, constructores de eventos, `World` |
| `tests/m10/test_*.py` | pruebas por archivo, T-M10-01…08 y trazabilidad |

---

### Task 1: Worktree, línea base y spec rev. 2

**Files:**
- Modify: `docs/specs/motor/m10-escalamiento-y-handoff.md`
- Modify: `docs/specs/motor/00-indice.md` (§10, una fila)
- Modify: `docs/specs/motor/m04-ciclo-del-turno.md` (§3.5, una línea)
- Create: `tests/m10/__init__.py`
- Copy: este plan al worktree

**Interfaces:**
- Consumes: nada.
- Produces: el spec rev. 2 con las firmas que usan las Tasks 2–9 y el worktree con la suite en verde.

- [ ] **Step 1: Crear el worktree desde `main`**

```bash
cd C:/Users/USUARIO/Documents/factored/agent-core
git worktree add ../agent-core-m10 -b feat/m10-escalamiento-y-handoff main
cd ../agent-core-m10
uv sync
mkdir -p tests/m10 docs/superpowers/plans
cp ../agent-core/docs/superpowers/plans/2026-09-29-m10-escalamiento-y-handoff.md docs/superpowers/plans/
: > tests/m10/__init__.py
```

Todos los comandos que siguen corren en `C:/Users/USUARIO/Documents/factored/agent-core-m10`.

- [ ] **Step 2: Verificar que la base está en verde**

Run: `uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo pasa. Si algo falla en `main`, detente y repórtalo: no es parte de este plan.

- [ ] **Step 3: Actualizar §2 del spec (interfaz)**

En `docs/specs/motor/m10-escalamiento-y-handoff.md`: cambia `- Estado: borrador · Fase 2` por `- Estado: rev. 2 (2026-09-29) · Fase 2` y reemplaza el bloque `class HandoffService:` de §2 por exactamente:

````markdown
```python
class HandoffService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, registry: RegistryPort, views: ViewService,
                 authz: AuthzPort, keys: KeyProvider, clock: Clock, ids: IdSource,
                 record: EventRecorder = append_events)
    def escalate(self, state: RunState, request: EscalationRequest, events_so_far: list[EngineEvent], *,
                 uow: UnitOfWork, turn_id: str | None = None
                 ) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]
    def get(self, handoff_ref: str, reader: Principal, on_behalf_of: OnBehalfOf | None = None
            ) -> dict[str, JsonValue]                                            # renderizado para el lector
    def record_resolution(self, handoff_ref: str, reader: Principal, resolution_code: str,
                          handoff_quality: Literal["useful", "incomplete", "unnecessary"],
                          notes: str | None = None, *, on_behalf_of: OnBehalfOf | None = None) -> EngineEvent
```

`HandoffRecord = {packet: HandoffPacket, resolution: Resolution | None}` es lo que se guarda con `UnitOfWork.put_handoff`.
````

- [ ] **Step 4: Agregar las decisiones al spec**

Al final del spec, reemplaza la sección `## 11. Abiertos` completa por:

````markdown
## 11. Decisiones de la rev. 2 (2026-09-29)

1. `request_summary`: **plantilla determinista** por clave de `reason_code` y `locale`, solo con conteos e id del flow; `citations` = nombres de hechos verificados. Sobrescribirla desde `agent-registry` requiere un campo nuevo en `EngineTemplates` (M0): abierto para producción.
2. `escalate` recibe `uow` y `turn_id` (solo nombre). M10 solo llama `uow.put_handoff`; M4 guarda el run, agrega los eventos devueltos y encola el outbox. `events_so_far` son todos los eventos del run, incluidos los del turno en curso.
3. `evidence_refs`: `call:<id>`, `policy:<id>@<v>`, `decision:<id>`, `page:<ref>`.
4. El registro persistido es `{packet, resolution}`; el paquete lleva vista `audit` y `subject.ref` enmascarado. `get` reconstruye los valores desde `RunState` con M7 según el lector.
5. Autorización: `AuthzPort.authorize_subject` (tabla de M9). `403 subject_forbidden`; handoff o run inexistente, `404 not_found`.
6. `record_resolution` recibe `on_behalf_of`; una sola resolución por handoff, serializada con un lease corto; `notes` solo en el registro, nunca en el evento.
7. Precondiciones de `escalate`: run `open` y sin acciones `proposed`/`confirmed` (`HandoffPreconditionError` si no).
8. `claimed_not_verified` = slots `claimed`; los `validated` no van.
9. Paquete degradado si la construcción falla; falla de `put_handoff` no se degrada.
10. `open_questions` con guarda de PII en claro (`find_clear_pii`).
11. Textos: `state.locale` con caída a `es`; mensaje de traspaso desde `agent.templates.handoff` con respaldo por defecto.

## 12. Abiertos

- Quién llena `open_questions` (M2/M10, índice §10).
- `request_summary` generado por LLM (pasaría por M8 `validate`) y plantillas de resumen en `agent-registry`.
- Un handoff con muchos hechos: no hay tope de tamaño del paquete; decidir con datos reales.
- Devolución del caso al bot (producción): fuera de este módulo (ADR 0013).
````

Actualiza también §10 (DoD): la segunda viñeta pasa a `- Plantillas de traspaso en ES y PT en `agent-registry` (mensaje) y por defecto en `agent_core/handoff/texts.py` (resumen y respaldo).`

- [ ] **Step 5: Actualizar el índice y M4**

En `docs/specs/motor/00-indice.md` §10, reemplaza la fila `| \`request_summary\` del handoff: generado o por plantilla | M10 | **nuevo**; M10 propone plantilla en el MVP |` por `| \`request_summary\` del handoff: generado o por plantilla | M10 | **resuelto** en M10 rev. 2: plantilla determinista en el MVP; generado (vía M8) queda para producción |`.

En `docs/specs/motor/m04-ciclo-del-turno.md` §3.5 reemplaza la primera frase (`` `handoff.escalate(state, request)` devuelve … ``) por: `` `handoff.escalate(state, request, events_so_far, uow=uow, turn_id=turn_id)` devuelve `(estado, [escalated], OutboxMessage, Message)` y ya persistió el paquete con `uow.put_handoff`. M4 debe haber invalidado antes las acciones `proposed`/`confirmed`. El estado con `status = escalated`, `outcome = escalated`, el evento `escalated` y el mensaje de outbox entran en la **misma** transacción del turno. ``. Deja el resto del párrafo igual.

- [ ] **Step 6: Commit**

```bash
git add docs tests/m10/__init__.py
git commit -m "docs(m10): spec rev. 2 (firma de escalate, decisiones y abiertos), índice y M4 §3.5

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Tipos del paquete y errores

**Files:**
- Create: `agent_core/handoff/errors.py`, `agent_core/handoff/packet.py`
- Test: `tests/m10/test_packet.py`

**Interfaces:**
- Consumes: `agent_core.domain`: `Model`, `EntityRef`, `FactSource`, `ActionState`, `InvalidationReason`, `JsonValue`, `Locale`, `PrincipalType`, `ReasonCodeStr`, `SubjectRef`, `UtcDatetime`, `DomainError`.
- Produces:
  - `HandoffPreconditionError(DomainError)`.
  - `RequestSummary(text: str, citations: list[str])`.
  - `FactView(fact_id, name, value: JsonValue = None, source: FactSource, ts)`.
  - `SlotView(name, value: JsonValue, source_turn: int)`.
  - `ActionView(action_id, tool: EntityRef, state: ActionState, args: dict[str, JsonValue], cancel_reason: InvalidationReason | None)`.
  - `HandoffPacket(...)` con los campos del spec §2 más `degraded_packet: bool = False`.
  - `Resolution(resolution_code, handoff_quality, notes, resolved_at, reader_type, reader_id)`.
  - `HandoffRecord(packet, resolution=None)`.

- [ ] **Step 1: Escribir la prueba que falla**

`tests/m10/test_packet.py`:

```python
"""Tipos del handoff (M10 §2): round-trip por JSON y sin campos extra."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent_core.domain import FactSource, to_jsonable
from agent_core.handoff.packet import (
    ActionView,
    FactView,
    HandoffPacket,
    HandoffRecord,
    RequestSummary,
    Resolution,
    SlotView,
)

TS = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _packet(**over: object) -> HandoffPacket:
    base: dict[str, object] = {
        "handoff_ref": "handoff-0001", "run_id": "run-0001", "release": "rel-1", "agent": "atencion@1.0.0",
        "principal_type": "customer", "subject": {"kind": "customer", "ref": "***"},
        "target_queue": "disputas", "priority": "high", "reason_code": "customer_request", "language": "es",
        "request_summary": RequestSummary(text="resumen", citations=["cargo"]),
        "verified_facts": [FactView(fact_id="fact-0001", name="cargo", value={"amount": Decimal("120.50")},
                                    source=FactSource(kind="tool", ref="get_charge@1.0.0"), ts=TS)],
        "claimed_not_verified": [SlotView(name="documento", value="***", source_turn=1)],
        "actions_taken": [ActionView(action_id="action-0001", tool="radicar_pqr@1.0.0", state="verified",
                                     args={"monto": Decimal("500.00")})],
        "open_questions": [], "evidence_refs": ["call:call-0001"],
        "transcript_ref": "/v1/runs/run-0001/transcript",
    }
    return HandoffPacket.model_validate(base | over)


def test_record_round_trips_through_json_keeping_decimals() -> None:
    record = HandoffRecord(packet=_packet())
    restored = HandoffRecord.model_validate(to_jsonable(record))
    assert restored == record
    assert restored.packet.verified_facts[0].value == {"amount": Decimal("120.50")}
    assert restored.resolution is None


def test_packet_rejects_extra_fields_and_invalid_reason_code() -> None:
    with pytest.raises(ValidationError):
        _packet(transcript="no se incrusta")
    with pytest.raises(ValidationError):
        _packet(reason_code="inventado")
    assert _packet(reason_code="policy:escalamiento-disputa-monto").reason_code.startswith("policy:")


def test_degraded_flag_defaults_to_false() -> None:
    assert _packet().degraded_packet is False
    assert _packet(degraded_packet=True).degraded_packet is True


def test_resolution_quality_is_closed_set() -> None:
    ok = Resolution(resolution_code="resuelto", handoff_quality="useful", notes=None, resolved_at=TS,
                    reader_type="advisor", reader_id="adv-7")
    assert ok.handoff_quality == "useful"
    with pytest.raises(ValidationError):
        Resolution(resolution_code="x", handoff_quality="great", notes=None, resolved_at=TS,
                   reader_type="advisor", reader_id="adv-7")
```

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_packet.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.handoff.packet'`.

- [ ] **Step 3: Implementar**

`agent_core/handoff/errors.py`:

```python
"""Errores propios de M10. Los de cara al usuario (403, 404, 409) son `EngineError` de M0."""

from agent_core.domain import DomainError


class HandoffPreconditionError(DomainError):
    """`escalate` recibió un run que no puede escalarse: no está `open` o conserva acciones sin invalidar.

    Es un bug de quien llama (M4), no un error de usuario."""
```

`agent_core/handoff/packet.py`:

```python
"""Tipos del handoff (M10 §2). El paquete persistido lleva vista `audit`; nunca incrusta el transcript."""

from typing import Literal

from pydantic import Field, NonNegativeInt

from agent_core.domain import (
    ActionState,
    EntityRef,
    FactSource,
    InvalidationReason,
    JsonValue,
    Locale,
    Model,
    PrincipalType,
    ReasonCodeStr,
    SubjectRef,
    UtcDatetime,
)

HandoffQuality = Literal["useful", "incomplete", "unnecessary"]


class RequestSummary(Model):
    """Qué pidió el usuario y cómo se llegó aquí. `citations` son los nombres de los hechos citados."""

    text: str
    citations: list[str] = Field(default_factory=list)


class FactView(Model):
    """Hecho verificado con su procedencia. `value` en vista `audit` (sin valor en un paquete degradado)."""

    fact_id: str
    name: str  # clave en `RunState.facts`; también la fuente de la proyección
    value: JsonValue = None
    source: FactSource
    ts: UtcDatetime


class SlotView(Model):
    """Slot `claimed`: lo dijo el usuario y nadie lo verificó. `value` en vista `audit`."""

    name: str
    value: JsonValue
    source_turn: NonNegativeInt


class ActionView(Model):
    """Acción con su estado de verificación (`verified`, `failed`, `uncertain`, `cancelled`, …)."""

    action_id: str
    tool: EntityRef
    state: ActionState
    args: dict[str, JsonValue] = Field(default_factory=dict)  # vista `audit`
    cancel_reason: InvalidationReason | None = None


class HandoffPacket(Model):
    """Paquete estructurado del traspaso (spec general §9). Referencia el transcript, no lo incrusta."""

    handoff_ref: str
    run_id: str
    release: str
    agent: EntityRef
    principal_type: PrincipalType
    subject: SubjectRef | None  # ref enmascarado
    target_queue: str
    priority: str
    reason_code: ReasonCodeStr
    language: Locale
    request_summary: RequestSummary
    verified_facts: list[FactView]
    claimed_not_verified: list[SlotView]
    actions_taken: list[ActionView]
    open_questions: list[str]
    evidence_refs: list[str]
    transcript_ref: str
    degraded_packet: bool = False


class Resolution(Model):
    """Resolución del receptor: etiqueta para la unidad 6 y señal para la auto-mejora."""

    resolution_code: str
    handoff_quality: HandoffQuality
    notes: str | None = None  # solo aquí; nunca en el evento
    resolved_at: UtcDatetime
    reader_type: PrincipalType
    reader_id: str | None


class HandoffRecord(Model):
    """Lo que guarda `UnitOfWork.put_handoff`: el paquete y, una vez, su resolución."""

    packet: HandoffPacket
    resolution: Resolution | None = None
```

- [ ] **Step 4: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10/test_packet.py -v && uv run mypy && uv run ruff check agent_core/handoff tests/m10`
Expected: PASS y sin hallazgos. (`ActionView(tool="radicar_pqr@1.0.0")` valida porque `EntityRef` acepta texto.)

- [ ] **Step 5: Commit**

```bash
git add agent_core/handoff/errors.py agent_core/handoff/packet.py tests/m10/test_packet.py
git commit -m "feat(m10): tipos del paquete, registro persistido y errores

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Textos deterministas (resumen y mensaje de traspaso)

**Files:**
- Create: `agent_core/handoff/texts.py`
- Test: `tests/m10/test_texts.py`

**Interfaces:**
- Consumes: nada de otras tasks.
- Produces:
  - `reason_key(reason_code: str) -> str`: `"rule:x"` → `"rule"`; un código base se devuelve tal cual; un código desconocido → `"default"`.
  - `render_summary(reason_code: str, locale: str, *, flow: str | None, facts: int, actions: int, uncertain: int) -> str`.
  - `default_handoff_message(locale: str) -> str`.
  - `FALLBACK_LOCALE = "es"`.

- [ ] **Step 1: Escribir la prueba que falla**

`tests/m10/test_texts.py`:

```python
"""Textos por defecto: deterministas, ES/PT, sin valores de campos ni promesas de tiempos (ADR 0013)."""

import pytest

from agent_core.domain import ReasonCode
from agent_core.handoff.texts import FALLBACK_LOCALE, default_handoff_message, reason_key, render_summary

BASE_KEYS = [code.value for code in ReasonCode]


def test_reason_key_maps_prefixes_and_base_codes() -> None:
    assert reason_key("rule:mora-alta") == "rule"
    assert reason_key("policy:escalamiento-disputa-monto") == "policy"
    assert reason_key("interrupt:fraude") == "interrupt"
    assert reason_key("customer_request") == "customer_request"
    assert reason_key("algo_raro") == "default"


@pytest.mark.parametrize("locale", ["es", "pt"])
@pytest.mark.parametrize("code", [*BASE_KEYS, "rule:x", "policy:x", "interrupt:x"])
def test_every_reason_has_a_summary_in_both_locales(code: str, locale: str) -> None:
    text = render_summary(code, locale, flow="disputa-cargo", facts=2, actions=1, uncertain=0)
    assert text and "{" not in text and "}" not in text


def test_summary_is_deterministic_and_mentions_counts_only() -> None:
    a = render_summary("policy:x", "es", flow="disputa-cargo", facts=3, actions=2, uncertain=1)
    assert a == render_summary("policy:x", "es", flow="disputa-cargo", facts=3, actions=2, uncertain=1)
    assert "disputa-cargo" in a and "3" in a
    assert "incierta" in render_summary("tool_failure", "es", flow=None, facts=0, actions=1, uncertain=1)
    assert "incierta" not in render_summary("tool_failure", "es", flow=None, facts=0, actions=1, uncertain=0)


def test_unknown_locale_falls_back_to_spanish() -> None:
    assert FALLBACK_LOCALE == "es"
    assert render_summary("customer_request", "fr", flow=None, facts=0, actions=0, uncertain=0) == \
        render_summary("customer_request", "es", flow=None, facts=0, actions=0, uncertain=0)
    assert default_handoff_message("fr") == default_handoff_message("es")
    assert default_handoff_message("pt") != default_handoff_message("es")


def test_handoff_message_promises_no_response_time() -> None:
    for locale in ("es", "pt"):
        text = default_handoff_message(locale).lower()
        assert not any(word in text for word in ("minuto", "hora", "minuto", "segundos"))
```

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_texts.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.handoff.texts'`.

- [ ] **Step 3: Implementar**

`agent_core/handoff/texts.py`:

```python
"""Plantillas por defecto de M10 (ES y PT): resumen del handoff y mensaje de traspaso.

Son datos, no lógica: solo llevan conteos y el id del flow activo (nunca valores de campos), así el resumen
puede persistirse y mostrarse sin pasar por M7. El mensaje de traspaso no promete tiempos de atención
(ADR 0013: eso lo comunica la plataforma del asesor). Sobrescribirlos desde `agent-registry` es un abierto."""

from collections.abc import Mapping
from types import MappingProxyType

FALLBACK_LOCALE = "es"

_PREFIXES = ("rule", "policy", "interrupt")

_SUMMARY: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "customer_request": {
        "es": "El usuario pidió hablar con una persona. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "O usuário pediu para falar com uma pessoa. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "low_confidence": {
        "es": "El agente no logró entender con suficiente confianza la solicitud. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "O agente não conseguiu entender o pedido com confiança suficiente. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "budget_exceeded": {
        "es": "Se agotó el presupuesto de la conversación. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "O orçamento da conversa se esgotou. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "tool_failure": {
        "es": "Falló un sistema necesario para atender el caso. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Falhou um sistema necessário para atender o caso. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "verification_failed": {
        "es": "No se pudo verificar el resultado de una acción. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Não foi possível verificar o resultado de uma ação. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "validation_failed": {
        "es": "No se pudo validar una respuesta al usuario. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Não foi possível validar uma resposta ao usuário. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "release_revoked": {
        "es": "La versión del agente fue revocada durante la conversación. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "A versão do agente foi revogada durante a conversa. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "auth_insufficient": {
        "es": "El usuario no completó la verificación de identidad requerida. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "O usuário não concluiu a verificação de identidade exigida. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "rule": {
        "es": "Una regla de negocio exigió atención humana. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Uma regra de negócio exigiu atendimento humano. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "policy": {
        "es": "Una política exigió atención humana. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Uma política exigiu atendimento humano. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "interrupt": {
        "es": "Se activó una interrupción que exige atención humana. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "Foi acionada uma interrupção que exige atendimento humano. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
    "default": {
        "es": "El caso se escaló a atención humana. Flow activo: {flow}. Hechos verificados: {facts}. Acciones: {actions}.",
        "pt": "O caso foi encaminhado ao atendimento humano. Fluxo ativo: {flow}. Fatos verificados: {facts}. Ações: {actions}.",
    },
})

_UNCERTAIN: Mapping[str, str] = MappingProxyType({
    "es": " Hay {uncertain} acción(es) con resultado incierto: verificar antes de reintentar.",
    "pt": " Há {uncertain} ação(ões) com resultado incerto: verificar antes de tentar novamente.",
})

_NO_FLOW: Mapping[str, str] = MappingProxyType({"es": "ninguno", "pt": "nenhum"})

_HANDOFF_MESSAGE: Mapping[str, str] = MappingProxyType({
    "es": "Te voy a pasar con una persona de nuestro equipo que continuará con tu caso. Ya tiene el resumen de lo que hablamos.",
    "pt": "Vou transferir você para uma pessoa da nossa equipe que continuará com o seu caso. Ela já tem o resumo do que conversamos.",
})


def reason_key(reason_code: str) -> str:
    """Clave de plantilla de un `reason_code`: el prefijo (`rule`, `policy`, `interrupt`) o el código base."""
    prefix = reason_code.partition(":")[0]
    if ":" in reason_code and prefix in _PREFIXES:
        return prefix
    return reason_code if reason_code in _SUMMARY else "default"


def _locale(locale: str, table: Mapping[str, object]) -> str:
    return locale if locale in table else FALLBACK_LOCALE


def render_summary(reason_code: str, locale: str, *, flow: str | None, facts: int, actions: int,
                   uncertain: int) -> str:
    """Resumen determinista: solo conteos e id de flow (nunca valores de campos)."""
    by_locale = _SUMMARY[reason_key(reason_code)]
    loc = _locale(locale, by_locale)
    text = by_locale[loc].format(flow=flow or _NO_FLOW[loc], facts=facts, actions=actions)
    if uncertain > 0:
        text += _UNCERTAIN[loc].format(uncertain=uncertain)
    return text


def default_handoff_message(locale: str) -> str:
    """Mensaje final al principal cuando la plantilla del agente no está disponible."""
    return _HANDOFF_MESSAGE[_locale(locale, _HANDOFF_MESSAGE)]
```

- [ ] **Step 4: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10/test_texts.py -v && uv run ruff check agent_core/handoff tests/m10 && uv run mypy`
Expected: PASS. Si `ruff` marca `E501` por las líneas largas de las plantillas, envuélvelas con paréntesis y concatenación de strings sin cambiar el texto.

- [ ] **Step 5: Commit**

```bash
git add agent_core/handoff/texts.py tests/m10/test_texts.py
git commit -m "feat(m10): plantillas por defecto de resumen y traspaso (ES y PT)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Proyección por vista sobre M7

**Files:**
- Create: `agent_core/handoff/projection.py`, `tests/m10/helpers.py`
- Test: `tests/m10/test_projection.py`

**Interfaces:**
- Consumes: `agent_core.views`: `ViewService`, `TokenVault`, `FieldClassifier`, `FieldRule`, `DEFAULT_CATALOG`; puertos `KeyProvider`, `IdSource`.
- Produces:
  - `PURPOSE = "handoff"` (el `purpose` que se pasa a `can_read_field`).
  - `Projector(views: ViewService, keys: KeyProvider, ids: IdSource)` con `audit(run_id: str, value: JsonValue, source: str) -> JsonValue` y `for_reader(run_id: str, value: JsonValue, source: str, reader: Principal, obo: OnBehalfOf | None) -> JsonValue`.
  - En `tests/m10/helpers.py`: `CATALOG`, `HandoffAuthz`, `make_views(authz)`.

- [ ] **Step 1: Escribir los ayudantes y la prueba que falla**

`tests/m10/helpers.py` (por ahora solo lo que usa esta task; las Tasks 5–8 lo amplían):

```python
"""Ayudantes de las pruebas de M10. Solo datos sintéticos."""

from collections.abc import Mapping

from agent_core.domain import Agent, OnBehalfOf, Principal, PrincipalType, SubjectRef
from agent_core.ports import AuthzDecision
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule, ViewService
from testing.fakes.clock import FakeClock
from testing.fakes.keys import FakeKeyProvider

# Catálogo sintético: lo que en producción publica la unidad 3 como FieldClassification.
CATALOG: Mapping[str, FieldRule] = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "monto": FieldRule(field_class="financial"),
    "transaction_id": FieldRule(field_class="pii_direct", tag="tx"),
}


class HandoffAuthz:
    """Doble mínimo de `AuthzPort` para M10 (la `TableAuthz` real llega con M9).

    - `authorize_subject`: asesor con delegación a su nombre sobre ese subject; `service` con el scope
      `handoff:read`; nadie más.
    - `can_read_field`: solo para el propósito `handoff` y si el par `(lector, campo)` está concedido; el
      asesor además necesita su delegación."""

    def __init__(self, field_grants: set[tuple[str, str]] | None = None,
                 reportable: frozenset[str] = frozenset({"country"})) -> None:
        self._grants = field_grants or set()
        self._reportable = reportable

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        if principal.type is PrincipalType.service:
            allowed = "handoff:read" in principal.scopes
        elif principal.type is PrincipalType.advisor:
            allowed = (obo is not None and obo.grantee == principal.key and subject is not None
                       and obo.subject == subject)
        else:
            allowed = False
        return AuthzDecision(allowed=allowed, reason=None if allowed else "sin_delegacion")

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        if purpose != "handoff" or reader.id is None:
            return False
        if reader.type is PrincipalType.advisor and (obo is None or obo.grantee.id != reader.id):
            return False
        return (reader.id, field) in self._grants

    def reportable_attrs(self) -> frozenset[str]:
        return self._reportable

    def authorize_agent(self, principal: Principal, agent: Agent, subject: SubjectRef | None) -> AuthzDecision:
        raise NotImplementedError

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        raise NotImplementedError


def make_views(authz: HandoffAuthz | None = None, keys: FakeKeyProvider | None = None) -> ViewService:
    return ViewService(keys or FakeKeyProvider.default(), authz or HandoffAuthz(), FakeClock(),
                       FieldClassifier(CATALOG))
```

`tests/m10/test_projection.py`:

```python
"""Proyección del paquete: `audit` para lo persistido, vista del lector para `get`."""

from decimal import Decimal

from agent_core.handoff.projection import Projector
from testing.builders import advisor_with_delegation, principal
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m10.helpers import HandoffAuthz, make_views

FULL = {"document_number": "1023456789", "first_name": "Ana", "amount": Decimal("120.50")}


def _projector(authz: HandoffAuthz | None = None) -> Projector:
    keys = FakeKeyProvider.default()
    return Projector(make_views(authz, keys), keys, FakeIds())


def test_audit_masks_pii_and_keeps_financial_values() -> None:
    out = _projector().audit("run-0001", FULL, "cliente")
    assert out == {"document_number": "***6789", "first_name": "***", "amount": Decimal("120.50")}


def test_unclassified_scalar_is_masked() -> None:
    assert _projector().audit("run-0001", "1023456789", "documento") == "***"


def test_for_reader_shows_only_granted_fields() -> None:
    advisor, obo = advisor_with_delegation()
    projector = _projector(HandoffAuthz({("adv-7", "document_number")}))
    out = projector.for_reader("run-0001", FULL, "cliente", advisor, obo)
    assert out == {"document_number": "1023456789", "first_name": "***", "amount": Decimal("120.50")}


def test_for_reader_without_grants_equals_the_masked_view() -> None:
    out = _projector().for_reader("run-0001", FULL, "cliente", principal(), None)
    assert out == {"document_number": "***6789", "first_name": "***", "amount": Decimal("120.50")}


def test_for_reader_handles_nested_and_scalar_values() -> None:
    advisor, obo = advisor_with_delegation()
    projector = _projector(HandoffAuthz({("adv-7", "email")}))
    nested = {"contacts": [{"email": "ana@example.test"}, {"email": "beto@example.test"}], "note": None}
    assert projector.for_reader("run-0001", nested, "cliente", advisor, obo) == nested
```

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_projection.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.handoff.projection'`.

- [ ] **Step 3: Implementar**

`agent_core/handoff/projection.py`:

```python
"""Proyección de valores `full` para el handoff sobre M7.

- `audit`: lo que se persiste en el paquete (enmascarado, sin tokens reversibles).
- `for_reader`: lo que ve un lector concreto en `get`: M7 tokeniza en un vault descartable y `render`
  devuelve el valor real solo donde la política lo permite.

El vault es por llamada y no se sella: no toca `RunState.token_map`, que sigue siendo de M7/M4."""

from agent_core.domain import JsonValue, OnBehalfOf, Principal
from agent_core.ports import IdSource, KeyProvider
from agent_core.views import TokenVault, ViewService

PURPOSE = "handoff"


class Projector:
    def __init__(self, views: ViewService, keys: KeyProvider, ids: IdSource) -> None:
        self._views = views
        self._keys = keys
        self._ids = ids

    def audit(self, run_id: str, value: JsonValue, source: str) -> JsonValue:
        vault = TokenVault(run_id, self._keys, self._ids)
        return self._views.project(value, source, [], vault).audit

    def for_reader(self, run_id: str, value: JsonValue, source: str, reader: Principal,
                   obo: OnBehalfOf | None) -> JsonValue:
        vault = TokenVault(run_id, self._keys, self._ids)
        model_view = self._views.project(value, source, [], vault).model
        return self._render(model_view, vault, reader, obo)

    def _render(self, node: JsonValue, vault: TokenVault, reader: Principal,
                obo: OnBehalfOf | None) -> JsonValue:
        if isinstance(node, str):
            return self._views.render(node, vault, reader, PURPOSE, obo).text
        if isinstance(node, list):
            return [self._render(item, vault, reader, obo) for item in node]
        if isinstance(node, dict):
            return {key: self._render(item, vault, reader, obo) for key, item in node.items()}
        return node
```

- [ ] **Step 4: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10/test_projection.py -v && uv run mypy && uv run ruff check agent_core/handoff tests/m10`
Expected: PASS. (`TokenVault`, `ViewService` se importan de `agent_core.views`, su interfaz pública.)

- [ ] **Step 5: Commit**

```bash
git add agent_core/handoff/projection.py tests/m10/helpers.py tests/m10/test_projection.py
git commit -m "feat(m10): proyección audit y por lector sobre M7

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Constructor del paquete (T-M10-02, T-M10-03 y parte de T-M10-04)

**Files:**
- Create: `agent_core/handoff/builder.py`
- Modify: `tests/m10/helpers.py` (agrega `NOW`, `make_state`, constructores de eventos)
- Test: `tests/m10/test_builder.py`

**Interfaces:**
- Consumes: `Projector` (Task 4), tipos de la Task 2, `render_summary` (Task 3), `ViewService.find_clear_pii`.
- Produces (todas en `agent_core.handoff.builder`):
  - `TRANSCRIPT_PATH = "/v1/runs/{run_id}/transcript"`.
  - `evidence_refs(state: RunState, events: Sequence[EngineEvent]) -> list[str]`.
  - `build_packet(*, state, request: EscalationRequest, handoff_ref: str, events: Sequence[EngineEvent], projector: Projector, views: ViewService) -> HandoffPacket`.
  - `build_minimal_packet(*, state, request, handoff_ref) -> HandoffPacket` (`degraded_packet=True`).

- [ ] **Step 1: Ampliar los ayudantes**

Agrega al final de `tests/m10/helpers.py` (y estos imports arriba, ordenados por `ruff --fix`):

```python
from datetime import timedelta
from decimal import Decimal
from typing import Any

from agent_core.domain import (
    EntityRef,
    EngineEvent,
    RuleEvaluated,
    RuleEvaluatedPayload,
    RunState,
    ToolCalled,
    ToolCalledPayload,
)
from testing.builders import NOW, action, run_state

DOC = "1023456789"


def make_state(**over: Any) -> RunState:
    """Run abierto con hechos, slots, decisiones y acciones ya invalidadas (como lo entrega M4)."""
    base: dict[str, Any] = {
        "active_flow": {"flow": "disputa-cargo@1.0.0", "node_id": "responder"},
        "facts": {
            "cliente": {
                "fact_id": "fact-0001",
                "value": {"document_number": DOC, "first_name": "Ana"},
                "source": {"kind": "tool", "ref": "get_customer@1.0.0"},
                "ts": NOW,
            },
            "cargo": {
                "fact_id": "fact-0002",
                "value": {"amount": Decimal("120.50"), "currency": "USD"},
                "source": {"kind": "tool", "ref": "get_charge@1.0.0", "inputs": ["fact-0001"]},
                "ts": NOW,
            },
            "politica_pagina": {
                "fact_id": "fact-0003",
                "value": {"status": "vigente"},
                "source": {"kind": "knowledge", "ref": "disputas/plazos@snap-1.0.0"},
                "ts": NOW,
            },
        },
        "slots": {
            "documento": {"value": DOC, "status": "claimed", "source_turn": 1},
            "motivo": {"value": "no reconozco el cargo", "status": "validated", "source_turn": 2},
        },
        "decisions": {
            "coincide": {
                "decision_id": "decision-0001", "value": {"match": "unica"}, "p_cal": {"match": 0.93},
                "provider_used": "classifier", "model_version": "clf-demo-1",
            }
        },
        "actions": [
            action(action_id="action-0001", confirm_node_id="c1", state="verified"),
            action(action_id="action-0002", confirm_node_id="c2", state="uncertain"),
            action(action_id="action-0003", confirm_node_id="c3", state="cancelled", cancel_reason="escalated"),
        ],
        "open_questions": ["¿fecha exacta del cargo?"],
        "turn_count": 3,
    }
    return run_state(**(base | over))


def tool_called(call_id: str, *, turn_id: str = "turn-0001") -> EngineEvent:
    return ToolCalled(
        event_id=f"event-{call_id}", run_id="run-0001", turn_id=turn_id, release="rel-2026-09-28", ts=NOW,
        payload=ToolCalledPayload(node_id="n1", tool=EntityRef(id="get_charge", version="1.0.0"),
                                  call_id=call_id, status="ok", args={}, latency_ms=3),
    )


def rule_evaluated(policy: str | None, *, turn_id: str = "turn-0001") -> EngineEvent:
    return RuleEvaluated(
        event_id=f"event-rule-{policy}", run_id="run-0001", turn_id=turn_id, release="rel-2026-09-28", ts=NOW,
        payload=RuleEvaluatedPayload(node_id="n2", policy=EntityRef.parse(policy) if policy else None,
                                     inputs={}, result=True),
    )
```

(`timedelta` se importa solo si lo usas; quita imports sin uso al correr `ruff --fix`.)

- [ ] **Step 2: Escribir la prueba que falla**

`tests/m10/test_builder.py`:

```python
"""Construcción del paquete: T-M10-02, T-M10-03 y la parte de T-M10-04 que no depende del servicio."""

from decimal import Decimal

from agent_core.domain import EscalationRequest, dumps
from agent_core.handoff.builder import build_minimal_packet, build_packet, evidence_refs
from agent_core.handoff.projection import Projector
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m10.helpers import DOC, make_state, make_views, rule_evaluated, tool_called

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _build(state=None, request=REQUEST, events=()):  # type: ignore[no-untyped-def]
    keys = FakeKeyProvider.default()
    views = make_views(keys=keys)
    return build_packet(state=state or make_state(), request=request, handoff_ref="handoff-0001",
                        events=list(events), projector=Projector(views, keys, FakeIds()), views=views)


def test_t_m10_02_packet_separates_verified_facts_from_claimed_slots() -> None:
    """T-M10-02: los hechos verificados y los slots `claimed` van en campos distintos."""
    packet = _build()
    assert [f.name for f in packet.verified_facts] == ["cliente", "cargo", "politica_pagina"]
    assert [s.name for s in packet.claimed_not_verified] == ["documento"]  # el slot validated no va
    assert packet.verified_facts[0].source.ref == "get_customer@1.0.0"
    assert packet.verified_facts[0].value == {"document_number": "***6789", "first_name": "***"}
    assert packet.verified_facts[1].value == {"amount": Decimal("120.50"), "currency": "USD"}
    assert packet.claimed_not_verified[0].value == "***"


def test_t_m10_03_actions_taken_reflects_verification_state() -> None:
    """T-M10-03: `actions_taken` refleja verified, uncertain y cancelled."""
    packet = _build()
    states = {a.action_id: a.state.value for a in packet.actions_taken}
    assert states == {"action-0001": "verified", "action-0002": "uncertain", "action-0003": "cancelled"}
    cancelled = next(a for a in packet.actions_taken if a.action_id == "action-0003")
    assert cancelled.cancel_reason is not None and cancelled.cancel_reason.value == "escalated"
    assert all(a.args["monto"] == Decimal("500.00") for a in packet.actions_taken)  # financial pasa
    assert all(a.args["transaction_id"] == "***" for a in packet.actions_taken)  # pii_direct tag tx


def test_packet_header_and_transcript_reference() -> None:
    packet = _build()
    assert (packet.handoff_ref, packet.run_id, packet.release) == ("handoff-0001", "run-0001", "rel-2026-09-28")
    assert str(packet.agent) == "atencion@1.0.0"
    assert packet.principal_type.value == "customer"
    assert packet.subject is not None and (packet.subject.kind, packet.subject.ref) == ("customer", "***")
    assert (packet.target_queue, packet.priority, packet.reason_code) == ("disputas", "normal", "customer_request")
    assert packet.language == "es"
    assert packet.transcript_ref == "/v1/runs/run-0001/transcript"
    assert packet.degraded_packet is False
    assert packet.request_summary.citations == ["cliente", "cargo", "politica_pagina"]
    assert "disputa-cargo" in packet.request_summary.text


def test_evidence_refs_collect_calls_policies_decisions_and_pages_without_duplicates() -> None:
    events = [tool_called("call-0001"), rule_evaluated("escalamiento-disputa-monto@1.0.0"),
              tool_called("call-0002"), tool_called("call-0001"), rule_evaluated(None),
              rule_evaluated("escalamiento-disputa-monto@1.0.0")]
    refs = evidence_refs(make_state(), events)
    assert refs == ["call:call-0001", "policy:escalamiento-disputa-monto@1.0.0", "call:call-0002",
                    "decision:decision-0001", "page:disputas/plazos@snap-1.0.0"]


def test_open_questions_with_clear_pii_are_masked() -> None:
    state = make_state(open_questions=["¿fecha exacta del cargo?", f"¿confirmas el documento {DOC}?"])
    assert _build(state).open_questions == ["¿fecha exacta del cargo?", "***"]


def test_built_packet_has_no_clear_pii() -> None:
    """Parte de T-M10-04: el paquete construido no contiene `pii_direct` en claro."""
    state = make_state()
    packet = _build(state)
    facts_full = {name: fact.value for name, fact in state.facts.items()} | {"documento": DOC}
    assert make_views().find_clear_pii(dumps(packet.model_dump(mode="python")), facts_full) == []
    assert DOC not in dumps(packet.model_dump(mode="python"))


def test_minimal_packet_is_degraded_and_carries_no_values() -> None:
    packet = build_minimal_packet(state=make_state(), request=REQUEST, handoff_ref="handoff-0001")
    assert packet.degraded_packet is True
    assert packet.reason_code == "customer_request" and packet.transcript_ref == "/v1/runs/run-0001/transcript"
    assert [f.name for f in packet.verified_facts] == ["cliente", "cargo", "politica_pagina"]
    assert all(f.value is None for f in packet.verified_facts)
    assert packet.claimed_not_verified == [] and packet.actions_taken == []
    assert packet.subject is not None and packet.subject.ref == "***"
```

- [ ] **Step 3: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_builder.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'agent_core.handoff.builder'`.

- [ ] **Step 4: Implementar**

`agent_core/handoff/builder.py`:

```python
"""Construcción del `HandoffPacket` (M10 §3.1, paso 2). Funciones puras sobre el estado y los eventos.

Todo valor de cliente entra por `Projector.audit` (M7): el paquete persistido nunca lleva `full`."""

from collections.abc import Sequence

from agent_core.domain import (
    ActionState,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    RuleEvaluated,
    RunState,
    SubjectRef,
    ToolCalled,
)
from agent_core.handoff.packet import ActionView, FactView, HandoffPacket, RequestSummary, SlotView
from agent_core.handoff.projection import Projector
from agent_core.handoff.texts import render_summary
from agent_core.views import ViewService

TRANSCRIPT_PATH = "/v1/runs/{run_id}/transcript"
MASK = "***"


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _fact_views(state: RunState, projector: Projector) -> list[FactView]:
    return [
        FactView(fact_id=fact.fact_id, name=name, value=projector.audit(state.run_id, fact.value, name),
                 source=fact.source, ts=fact.ts)
        for name, fact in state.facts.items()
    ]


def _slot_views(state: RunState, projector: Projector) -> list[SlotView]:
    return [
        SlotView(name=name, value=projector.audit(state.run_id, slot.value, name), source_turn=slot.source_turn)
        for name, slot in state.slots.items()
        if slot.status == "claimed"
    ]


def _action_views(state: RunState, projector: Projector) -> list[ActionView]:
    return [
        ActionView(action_id=a.action_id, tool=a.tool, state=a.state,
                   args=_as_dict(projector.audit(state.run_id, a.args, a.tool.id)),
                   cancel_reason=a.cancel_reason)
        for a in state.actions
    ]


def evidence_refs(state: RunState, events: Sequence[EngineEvent]) -> list[str]:
    """`call:<id>`, `policy:<id>@<v>`, `decision:<id>` y `page:<ref>`, sin duplicados y en orden de aparición."""
    refs: list[str] = []

    def add(ref: str) -> None:
        if ref not in refs:
            refs.append(ref)

    for event in events:
        if isinstance(event, ToolCalled):
            add(f"call:{event.payload.call_id}")
        elif isinstance(event, RuleEvaluated) and event.payload.policy is not None:
            add(f"policy:{event.payload.policy}")
    for decision in state.decisions.values():
        add(f"decision:{decision.decision_id}")
    for fact in state.facts.values():
        if fact.source.kind == "knowledge":
            add(f"page:{fact.source.ref}")
    return refs


def _open_questions(state: RunState, views: ViewService) -> list[str]:
    facts_full = {name: fact.value for name, fact in state.facts.items()}
    facts_full |= {name: slot.value for name, slot in state.slots.items()}
    return [MASK if views.find_clear_pii(question, facts_full) else question for question in state.open_questions]


def _summary(state: RunState, request: EscalationRequest, facts: Sequence[FactView]) -> RequestSummary:
    uncertain = sum(1 for a in state.actions if a.state is ActionState.uncertain)
    text = render_summary(
        request.reason_code, state.locale,
        flow=state.active_flow.flow.id if state.active_flow else None,
        facts=len(facts), actions=len(state.actions), uncertain=uncertain,
    )
    return RequestSummary(text=text, citations=[f.name for f in facts])


def _masked_subject(state: RunState) -> SubjectRef | None:
    return None if state.subject is None else SubjectRef(kind=state.subject.kind, ref=MASK)


def build_packet(*, state: RunState, request: EscalationRequest, handoff_ref: str,
                 events: Sequence[EngineEvent], projector: Projector, views: ViewService) -> HandoffPacket:
    facts = _fact_views(state, projector)
    return HandoffPacket(
        handoff_ref=handoff_ref, run_id=state.run_id, release=state.release, agent=state.agent,
        principal_type=state.principal.type, subject=_masked_subject(state),
        target_queue=request.target_queue, priority=request.priority, reason_code=request.reason_code,
        language=state.locale, request_summary=_summary(state, request, facts),
        verified_facts=facts, claimed_not_verified=_slot_views(state, projector),
        actions_taken=_action_views(state, projector), open_questions=_open_questions(state, views),
        evidence_refs=evidence_refs(state, events),
        transcript_ref=TRANSCRIPT_PATH.format(run_id=state.run_id),
    )


def build_minimal_packet(*, state: RunState, request: EscalationRequest, handoff_ref: str) -> HandoffPacket:
    """Paquete de respaldo (§5 del spec): `reason_code`, hechos sin valores y `transcript_ref`."""
    return HandoffPacket(
        handoff_ref=handoff_ref, run_id=state.run_id, release=state.release, agent=state.agent,
        principal_type=state.principal.type, subject=_masked_subject(state),
        target_queue=request.target_queue, priority=request.priority, reason_code=request.reason_code,
        language=state.locale, request_summary=RequestSummary(text=request.reason_code),
        verified_facts=[FactView(fact_id=f.fact_id, name=name, source=f.source, ts=f.ts)
                        for name, f in state.facts.items()],
        claimed_not_verified=[], actions_taken=[], open_questions=[], evidence_refs=[],
        transcript_ref=TRANSCRIPT_PATH.format(run_id=state.run_id), degraded_packet=True,
    )
```

- [ ] **Step 5: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10/test_builder.py -v && uv run mypy && uv run ruff check --fix agent_core/handoff tests/m10`
Expected: PASS. Si `find_clear_pii` reporta el slot `documento` como fuga en `test_built_packet_has_no_clear_pii`, revisa que `_slot_views` use `Projector.audit` (debe salir `***`). Si `mypy` se queja del `# type: ignore` de `_build` en el test, tipa el helper con `RunState | None` y `Sequence[EngineEvent]` (los tests no se tipan con `mypy`, pero mantenlos limpios).

- [ ] **Step 6: Commit**

```bash
git add agent_core/handoff/builder.py tests/m10/helpers.py tests/m10/test_builder.py
git commit -m "feat(m10): construcción del paquete (hechos, claimed, acciones, evidencias, degradado)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `HandoffService.escalate` (T-M10-01, T-M10-04, T-M10-07, T-M10-08)

**Files:**
- Create: `agent_core/handoff/recorder.py`, `agent_core/handoff/service.py`
- Modify: `tests/m10/helpers.py` (agrega `World`, `make_world`, `commit_turn`)
- Test: `tests/m10/test_escalate.py`

**Interfaces:**
- Consumes: Tasks 2–5; puertos `UnitOfWork`, `UnitOfWorkFactory`, `RegistryPort`, `AuthzPort`, `KeyProvider`, `Clock`, `IdSource`, `IdKind`; `Agent`, `Template`, `Escalated`, `EscalatedPayload`, `HandoffCreatedPayload`, `OutboxMessage`, `Message`, `Outcome`, `Awaiting`.
- Produces:
  - `EventRecorder = Callable[[UnitOfWork, RunState, list[EngineEvent]], None]` y `append_events(uow, state, events) -> None` (`agent_core.handoff.recorder`).
  - `HandoffService.__init__(self, *, uow_factory, registry, views, authz, keys, clock, ids, record=append_events)`.
  - `HandoffService.escalate(self, state, request, events_so_far, *, uow, turn_id=None) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]`.
  - Orden fijo de IDs: `handoff`, luego `event`, luego `message` (determinismo).

- [ ] **Step 1: Ampliar los ayudantes**

Agrega a `tests/m10/helpers.py` (imports arriba, `ruff --fix` los ordena):

```python
from dataclasses import dataclass

from agent_core.domain import Budgets, EngineTemplates, EscalationRequest, Template
from agent_core.handoff.service import HandoffService
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.storage import InMemoryStore
from agent_core.domain import Message, OutboxMessage


def make_agent(**over: Any) -> Agent:
    refs = {name: f"{name}@1.0.0" for name in
            ("clarify", "abstain", "handoff", "pending_ack", "pending_offer", "unsupported_language",
             "input_too_large")}
    base: dict[str, Any] = {
        "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "disputa-cargo@1.0.0",
        "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"], "default_locale": "es", "budgets": {
            "max_nodes_per_turn": 20, "max_model_calls_per_turn": 5, "max_tokens_per_run": 10000,
            "max_cost_per_run": Decimal("1.00"), "max_wall_ms_per_turn": 20000},
        "templates": refs, "max_clarifications": 2, "on_clarify_exhausted": "escalate",
        "default_target_queue": "general",
    }
    return Agent.model_validate(base | over)


HANDOFF_TEMPLATE = Template(id="handoff", version="1.0.0", locales={
    "es": "Te paso con una persona del equipo de disputas.", "pt": "Vou passar você para a equipe de disputas."})


@dataclass
class World:
    store: InMemoryStore
    registry: InMemoryRegistry
    ids: FakeIds
    clock: FakeClock
    authz: HandoffAuthz
    service: HandoffService


def make_world(*, authz: HandoffAuthz | None = None, with_template: bool = True,
               views: ViewService | None = None, record: Any = None) -> World:
    store = InMemoryStore()
    registry = InMemoryRegistry()
    registry.add(make_agent(), *([HANDOFF_TEMPLATE] if with_template else []))
    keys = FakeKeyProvider.default()
    authz = authz or HandoffAuthz()
    clock = FakeClock()
    ids = FakeIds()
    kwargs = {} if record is None else {"record": record}
    service = HandoffService(uow_factory=store.uow, registry=registry, views=views or make_views(authz, keys),
                             authz=authz, keys=keys, clock=clock, ids=ids, **kwargs)
    return World(store, registry, ids, clock, authz, service)


def seed_run(world: World, state: RunState) -> RunState:
    """Deja el run guardado como M4 lo tendría antes del turno (versión 1)."""
    with world.store.uow() as uow:
        saved = uow.save_run(state, expected_version=0)
        uow.commit()
    return saved


def escalate_and_commit(world: World, state: RunState, request: EscalationRequest,
                        events: list[EngineEvent] | None = None
                        ) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]:
    """Lo que hará M4: una transacción con el paquete, el run cerrado, los eventos y el outbox."""
    with world.store.uow() as uow:
        result = world.service.escalate(state, request, events or [], uow=uow, turn_id="turn-0001")
        closed, new_events, outbox, _message = result
        uow.save_run(closed, expected_version=state.state_version)
        uow.append_events(state.run_id, new_events)
        uow.enqueue_outbox(outbox)
        uow.commit()
    return result
```

- [ ] **Step 2: Escribir la prueba que falla**

`tests/m10/test_escalate.py`:

```python
"""`HandoffService.escalate`: T-M10-01, T-M10-04, T-M10-07 y T-M10-08 (con la transacción que armará M4)."""

import pytest

from agent_core.domain import EscalationRequest, Escalated, Outcome, dumps
from agent_core.handoff import HandoffPreconditionError
from agent_core.handoff.packet import HandoffRecord
from testing.fakes.storage import SimulatedCrash
from tests.m10.helpers import DOC, escalate_and_commit, make_state, make_world, rule_evaluated, seed_run, tool_called
from testing.fakes.storage import InMemoryOutbox

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")
MONTO = EscalationRequest(reason_code="policy:escalamiento-disputa-monto", target_queue="disputas-alto-monto",
                          priority="high")


def test_t_m10_01_escalate_emits_handoff_created_and_closes_the_run_for_the_bot() -> None:
    """T-M10-01: emite `handoff_created` en el outbox y cierra el run (el `410` del turno siguiente es de M4)."""
    world = make_world()
    state = seed_run(world, make_state())
    closed, events, outbox, message = escalate_and_commit(world, state, REQUEST)

    stored = world.store.runs["run-0001"]
    assert stored.status == "escalated" and stored.outcome is Outcome.escalated
    assert stored.handoff_ref == closed.handoff_ref == "handoff-0001"
    assert stored.closed_at == world.clock.now() and stored.inactive_after is None
    assert stored.awaiting.value == "none" and stored.awaiting_node_id is None
    assert stored.actions == state.actions  # M10 no toca las acciones

    (pending,) = InMemoryOutbox(world.store).pending(10)
    assert pending == outbox and pending.type == "handoff_created" and pending.run_id == "run-0001"
    assert pending.payload == {
        "handoff_ref": "handoff-0001", "run_id": "run-0001", "target_queue": "disputas", "priority": "normal",
        "reason_code": "customer_request", "language": "es", "reportable_attrs": {"country": "CO"},
    }
    (event,) = world.store.events["run-0001"]
    assert isinstance(event, Escalated) and event == events[0]
    assert event.turn_id == "turn-0001" and event.release == "rel-2026-09-28"
    assert event.payload.model_dump() == {"reason_code": "customer_request", "target_queue": "disputas",
                                          "priority": "normal", "handoff_ref": "handoff-0001"}
    assert not any(e.type == "run_closed" for e in events)  # `run_closed` es solo de M4
    assert message.kind == "template" and message.locale == "es"
    assert message.text == "Te paso con una persona del equipo de disputas."
    assert "handoff-0001" in world.store.handoffs


def test_handoff_message_falls_back_to_the_default_when_the_template_is_missing() -> None:
    world = make_world(with_template=False)
    state = seed_run(world, make_state(locale="pt"))
    *_, message = world.service.escalate(state, REQUEST, [], uow=world.store.uow(), turn_id=None)
    assert message.locale == "pt" and "equipe" in message.text


def test_t_m10_04_persisted_packet_has_no_clear_pii() -> None:
    """T-M10-04: lo persistido no contiene `pii_direct` en claro."""
    world = make_world()
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    raw = world.store.handoffs["handoff-0001"]
    facts_full = {n: f.value for n, f in state.facts.items()} | {"documento": DOC}
    assert world.service._views.find_clear_pii(dumps(raw), facts_full) == []  # noqa: SLF001
    assert DOC not in dumps(raw)
    record = HandoffRecord.model_validate(raw)
    assert record.resolution is None and not record.packet.degraded_packet


def test_t_m10_07_amount_policy_escalation_keeps_priority_and_evidence() -> None:
    """T-M10-07: escalamiento por monto (`policy:escalamiento-disputa-monto`) con la prioridad correcta."""
    world = make_world()
    state = seed_run(world, make_state())
    events = [tool_called("call-0001"), rule_evaluated("escalamiento-disputa-monto@1.0.0")]
    _, _, outbox, _ = escalate_and_commit(world, state, MONTO, events)
    packet = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).packet
    assert (packet.reason_code, packet.priority, packet.target_queue) == (
        "policy:escalamiento-disputa-monto", "high", "disputas-alto-monto")
    assert "policy:escalamiento-disputa-monto@1.0.0" in packet.evidence_refs and "call:call-0001" in packet.evidence_refs
    assert outbox.payload["priority"] == "high" and outbox.payload["reason_code"] == MONTO.reason_code
    assert world.store.events["run-0001"][0].payload.priority == "high"  # type: ignore[union-attr]


def test_t_m10_08_outbox_failure_rolls_back_the_run_closure_and_the_packet() -> None:
    """T-M10-08: si el commit falla, el cierre del run, el paquete y el outbox se revierten juntos."""
    world = make_world()
    state = seed_run(world, make_state())
    world.store.inject("on_commit")
    with pytest.raises(SimulatedCrash), world.store.uow() as uow:
        closed, events, outbox, _ = world.service.escalate(state, REQUEST, [], uow=uow, turn_id="turn-0001")
        uow.save_run(closed, expected_version=state.state_version)
        uow.append_events(state.run_id, events)
        uow.enqueue_outbox(outbox)
        uow.commit()
    assert world.store.runs["run-0001"].status == "open"
    assert world.store.handoffs == {} and world.store.outbox_pending == {} and world.store.events == {}


@pytest.mark.parametrize("over", [{"status": "closed", "outcome": "resolved", "closed_at": "2026-09-28T12:00:00Z",
                                  "inactive_after": None}])
def test_escalate_rejects_a_run_that_is_not_open(over: dict[str, object]) -> None:
    world = make_world()
    state = make_state(**over)
    with pytest.raises(HandoffPreconditionError):
        world.service.escalate(state, REQUEST, [], uow=world.store.uow())


def test_escalate_rejects_runs_with_uninvalidated_actions() -> None:
    from testing.builders import action

    world = make_world()
    state = make_state(actions=[action(state="proposed")])
    with pytest.raises(HandoffPreconditionError):
        world.service.escalate(state, REQUEST, [], uow=world.store.uow())


def test_a_failure_building_the_packet_escalates_with_a_degraded_packet() -> None:
    class Broken:
        def project(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("proyección caída")

    world = make_world()
    world.service._projector._views = Broken()  # type: ignore[assignment]  # noqa: SLF001
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    packet = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).packet
    assert packet.degraded_packet is True and packet.reason_code == "customer_request"
    assert world.store.runs["run-0001"].status == "escalated"
    assert all(f.value is None for f in packet.verified_facts)


def test_escalate_is_deterministic_and_draws_ids_in_a_fixed_order() -> None:
    def run() -> tuple[object, ...]:
        world = make_world()
        state = seed_run(world, make_state())
        closed, events, outbox, message = escalate_and_commit(world, state, REQUEST, [tool_called("call-0001")])
        return (closed.model_dump(), [e.model_dump() for e in events], outbox.model_dump(), message.model_dump(),
                world.store.handoffs["handoff-0001"])

    assert run() == run()
    world = make_world()
    state = seed_run(world, make_state())
    closed, events, outbox, _ = escalate_and_commit(world, state, REQUEST)
    assert (closed.handoff_ref, events[0].event_id, outbox.message_id) == ("handoff-0001", "event-0001", "message-0001")


def test_reportable_attrs_are_filtered_by_the_authz_port() -> None:
    world = make_world()
    state = seed_run(world, make_state())
    _, _, outbox, _ = escalate_and_commit(world, state, REQUEST)
    assert outbox.payload["reportable_attrs"] == {"country": "CO"}  # `segment` no es reportable
```

- [ ] **Step 3: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_escalate.py -v`
Expected: FAIL con `ImportError`/`ModuleNotFoundError` de `agent_core.handoff.service` (y de `HandoffPreconditionError` en la interfaz pública, que la Task 9 completa; por ahora impórtalo desde `agent_core.handoff.errors` si la interfaz pública aún no lo exporta, y ajusta la línea al cerrar la Task 9).

- [ ] **Step 4: Implementar el gancho de eventos**

`agent_core/handoff/recorder.py`:

```python
"""Gancho para agregar eventos fuera de la transacción de un turno (patrón de M3).

Por defecto los agrega sin encadenar (fase 1, sin M11); M9 inyecta el que encadena con `TurnRecorder` de M11."""

from collections.abc import Callable

from agent_core.domain import EngineEvent, RunState
from agent_core.ports import UnitOfWork

EventRecorder = Callable[[UnitOfWork, RunState, list[EngineEvent]], None]


def append_events(uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
    uow.append_events(state.run_id, events)
```

- [ ] **Step 5: Implementar `escalate`**

`agent_core/handoff/service.py` (esta task escribe el archivo completo con `escalate`; las Tasks 7 y 8 agregan `get` y `record_resolution` en la misma clase):

```python
"""`HandoffService` (M10 §2–3). Único emisor de `escalated` y `handoff_resolved`; construye `handoff_created`."""

from collections.abc import Sequence

from agent_core.domain import (
    ActionState,
    Agent,
    Awaiting,
    EngineEvent,
    Escalated,
    EscalatedPayload,
    EscalationRequest,
    HandoffCreatedPayload,
    Message,
    Outcome,
    OutboxMessage,
    RunState,
    Template,
    to_jsonable,
)
from agent_core.handoff.builder import build_minimal_packet, build_packet
from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.packet import HandoffPacket, HandoffRecord
from agent_core.handoff.projection import Projector
from agent_core.handoff.recorder import EventRecorder, append_events
from agent_core.handoff.texts import default_handoff_message
from agent_core.ports import (
    AuthzPort,
    Clock,
    IdKind,
    IdSource,
    KeyProvider,
    RegistryPort,
    UnitOfWork,
    UnitOfWorkFactory,
)
from agent_core.views import ViewService

_PENDING = (ActionState.proposed, ActionState.confirmed)


class HandoffService:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, registry: RegistryPort, views: ViewService,
                 authz: AuthzPort, keys: KeyProvider, clock: Clock, ids: IdSource,
                 record: EventRecorder = append_events) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._views = views
        self._authz = authz
        self._clock = clock
        self._ids = ids
        self._record = record
        self._projector = Projector(views, keys, ids)

    # --- escalate --------------------------------------------------------------------------------------

    def escalate(self, state: RunState, request: EscalationRequest, events_so_far: Sequence[EngineEvent], *,
                 uow: UnitOfWork, turn_id: str | None = None
                 ) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]:
        """Persiste el paquete en la UoW del turno y devuelve lo que M4 debe guardar en esa misma transacción."""
        self._check_can_escalate(state)
        now = self._clock.now()
        handoff_ref = self._ids.new_id(IdKind.handoff)
        packet = self._packet(state, request, handoff_ref, events_so_far)
        uow.put_handoff(handoff_ref, to_jsonable(HandoffRecord(packet=packet)))

        closed = state.model_copy(update={
            "status": "escalated", "outcome": Outcome.escalated, "handoff_ref": handoff_ref,
            "closed_at": now, "last_activity_at": now, "inactive_after": None,
            "awaiting": Awaiting.none, "awaiting_node_id": None, "pending_offer": None,
        })
        event = Escalated(
            event_id=self._ids.new_id(IdKind.event), run_id=state.run_id, turn_id=turn_id,
            session_id=state.session_id, release=state.release, ts=now,
            payload=EscalatedPayload(reason_code=request.reason_code, target_queue=request.target_queue,
                                     priority=request.priority, handoff_ref=handoff_ref),
        )
        outbox = OutboxMessage(
            message_id=self._ids.new_id(IdKind.message), type="handoff_created", run_id=state.run_id,
            payload=to_jsonable(HandoffCreatedPayload(
                handoff_ref=handoff_ref, run_id=state.run_id, target_queue=request.target_queue,
                priority=request.priority, reason_code=request.reason_code, language=state.locale,
                reportable_attrs={k: v for k, v in sorted(state.principal.attrs.items())
                                  if k in self._authz.reportable_attrs()},
            )),
            created_at=now,
        )
        return closed, [event], outbox, self._closing_message(state)

    @staticmethod
    def _check_can_escalate(state: RunState) -> None:
        if state.status != "open":
            raise HandoffPreconditionError(f"{state.run_id}: solo se escala un run abierto ({state.status})")
        if any(a.state in _PENDING for a in state.actions):
            raise HandoffPreconditionError(f"{state.run_id}: invalida las acciones pendientes antes de escalar")

    def _packet(self, state: RunState, request: EscalationRequest, handoff_ref: str,
                events: Sequence[EngineEvent]) -> HandoffPacket:
        try:
            return build_packet(state=state, request=request, handoff_ref=handoff_ref, events=events,
                                projector=self._projector, views=self._views)
        except Exception:  # noqa: BLE001 - escalar nunca falla por armar el paquete (§5 del spec)
            return build_minimal_packet(state=state, request=request, handoff_ref=handoff_ref)

    def _closing_message(self, state: RunState) -> Message:
        text = default_handoff_message(state.locale)
        try:
            agent = self._registry.get(state.agent, Agent)
            template = self._registry.get(agent.templates.handoff.require_exact(), Template)
            custom = template.locales.get(state.locale)
            if custom and not template.reads:
                text = custom
        except Exception:  # noqa: BLE001 - un fallo del registro no impide escalar
            pass
        return Message(kind="template", text=text, locale=state.locale)
```

Nota: `HandoffService._views` y `_projector` se usan en las pruebas de degradación y de PII; no los renombres.

- [ ] **Step 6: Exportar temporalmente y ejecutar**

Crea `agent_core/handoff/__init__.py` con lo mínimo para esta task (la Task 9 lo deja final):

```python
"""M10 — escalamiento y handoff (ADR 0013)."""

from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.service import HandoffService

__all__ = ["HandoffPreconditionError", "HandoffService"]
```

Run: `uv run pytest tests/m10/test_escalate.py -v`
Expected: PASS. Puntos que suelen fallar y su causa:
- `KeyError` de `run_closed`/`escalated` en el `EVENT_EMITTERS`: no aplica, M10 no valida emisores.
- `VersionConflict` en `escalate_and_commit`: `seed_run` devuelve el estado con `state_version=1`; pásalo (no el original) a `escalate`.
- `ValidationError` al cerrar el run: revisa que `awaiting_node_id=None` y `awaiting=none` estén en el mismo `update` (el validador de `RunState` exige coherencia).

- [ ] **Step 7: Verificar todo el repo**

Run: `uv run pytest && uv run mypy && uv run ruff check --fix . && uv run lint-imports`
Expected: verde, con el contrato `M10 (handoff)` en `KEPT`.

- [ ] **Step 8: Commit**

```bash
git add agent_core/handoff tests/m10
git commit -m "feat(m10): HandoffService.escalate (paquete, evento, outbox, cierre del run) con T-M10-01/04/07/08

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `HandoffService.get` (T-M10-05)

**Files:**
- Modify: `agent_core/handoff/service.py` (agrega `get` y utilidades compartidas con la Task 8)
- Test: `tests/m10/test_get.py`

**Interfaces:**
- Consumes: Task 6 (`HandoffService`, `_projector`, `_uow_factory`), `EngineError`, `ProblemCode`.
- Produces:
  - `HandoffService.get(handoff_ref: str, reader: Principal, on_behalf_of: OnBehalfOf | None = None) -> dict[str, JsonValue]`: el paquete (`to_jsonable`) con `verified_facts[*].value`, `claimed_not_verified[*].value`, `actions_taken[*].args` y `subject.ref` según el lector, más la clave `resolution` (o `None`).
  - Privados que reutiliza la Task 8: `_load_record(uow, handoff_ref) -> HandoffRecord`, `_load_run(uow, run_id) -> RunState`, `_authorize(reader, obo, run) -> None`.

- [ ] **Step 1: Escribir la prueba que falla**

`tests/m10/test_get.py`:

```python
"""`get`: T-M10-05. Renderizado por lector con M7; sin delegación, 403."""

from decimal import Decimal

import pytest

from agent_core.domain import EngineError, EscalationRequest, ProblemCode
from testing.builders import advisor_with_delegation, principal
from tests.m10.helpers import DOC, HandoffAuthz, escalate_and_commit, make_state, make_world, seed_run

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _world(grants: set[tuple[str, str]]):  # type: ignore[no-untyped-def]
    world = make_world(authz=HandoffAuthz(grants))
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    return world


def test_t_m10_05_advisor_with_delegation_sees_the_permitted_fields() -> None:
    """T-M10-05: el asesor con delegación ve los campos permitidos; el resto, enmascarado."""
    world = _world({("adv-7", "document_number")})
    advisor, obo = advisor_with_delegation()
    out = world.service.get("handoff-0001", advisor, obo)
    cliente = out["verified_facts"][0]["value"]  # type: ignore[index]
    assert cliente == {"document_number": DOC, "first_name": "***"}
    assert out["verified_facts"][1]["value"] == {"amount": Decimal("120.50"), "currency": "USD"}  # type: ignore[index]
    assert out["claimed_not_verified"][0]["value"] == "***"  # type: ignore[index]  # `documento` sin clasificar
    assert out["subject"] == {"kind": "customer", "ref": "cust-001"}  # el asesor delegado conoce el subject
    assert out["resolution"] is None and out["handoff_ref"] == "handoff-0001"
    assert out["transcript_ref"] == "/v1/runs/run-0001/transcript"


def test_t_m10_05_without_delegation_it_is_forbidden() -> None:
    """T-M10-05: sin delegación, `403`."""
    world = _world({("adv-7", "document_number")})
    advisor, _ = advisor_with_delegation()
    with pytest.raises(EngineError) as denied:
        world.service.get("handoff-0001", advisor, None)
    assert denied.value.code is ProblemCode.subject_forbidden and denied.value.status == 403
    with pytest.raises(EngineError) as customer:
        world.service.get("handoff-0001", principal(), None)  # un customer tampoco lee handoffs
    assert customer.value.status == 403


def test_delegation_over_another_subject_is_forbidden() -> None:
    world = _world(set())
    advisor, obo = advisor_with_delegation()
    other = obo.model_copy(update={"subject": {"kind": "customer", "ref": "cust-999"}})
    with pytest.raises(EngineError) as denied:
        world.service.get("handoff-0001", advisor, other)
    assert denied.value.status == 403


def test_service_principal_with_scope_can_read() -> None:
    world = _world({("svc-1", "document_number")})
    service = principal(type="service", id="svc-1", scopes=["handoff:read"], attrs={})
    out = world.service.get("handoff-0001", service)
    assert out["verified_facts"][0]["value"]["document_number"] == DOC  # type: ignore[index]
    with pytest.raises(EngineError):
        world.service.get("handoff-0001", principal(type="service", id="svc-2", scopes=[], attrs={}))


def test_unknown_handoff_is_not_found() -> None:
    world = _world(set())
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as missing:
        world.service.get("handoff-9999", advisor, obo)
    assert missing.value.code is ProblemCode.not_found and missing.value.status == 404


def test_get_does_not_change_what_is_persisted() -> None:
    world = _world({("adv-7", "document_number")})
    advisor, obo = advisor_with_delegation()
    before = world.store.handoffs["handoff-0001"]
    world.service.get("handoff-0001", advisor, obo)
    assert world.store.handoffs["handoff-0001"] == before
    assert world.store.runs["run-0001"].token_map is None  # el vault de `get` es descartable
```

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_get.py -v`
Expected: FAIL con `AttributeError: 'HandoffService' object has no attribute 'get'`.

- [ ] **Step 3: Implementar**

En `agent_core/handoff/service.py`, agrega a los imports `EngineError`, `JsonValue`, `OnBehalfOf`, `Principal`, `ProblemCode` (de `agent_core.domain`) y estos métodos dentro de `HandoffService`:

```python
    # --- get -------------------------------------------------------------------------------------------

    def get(self, handoff_ref: str, reader: Principal,
            on_behalf_of: OnBehalfOf | None = None) -> dict[str, JsonValue]:
        """Paquete renderizado para `reader`: lo que la política le permite en claro, el resto enmascarado."""
        with self._uow_factory() as uow:
            record = self._load_record(uow, handoff_ref)
            run = self._load_run(uow, record.packet.run_id)
        self._authorize(reader, on_behalf_of, run)
        packet = record.packet
        if not packet.degraded_packet:
            run_id = run.run_id
            facts = [
                view.model_copy(update={"value": self._projector.for_reader(
                    run_id, run.facts[view.name].value, view.name, reader, on_behalf_of)})
                if view.name in run.facts else view
                for view in packet.verified_facts
            ]
            slots = [
                view.model_copy(update={"value": self._projector.for_reader(
                    run_id, run.slots[view.name].value, view.name, reader, on_behalf_of)})
                if view.name in run.slots else view
                for view in packet.claimed_not_verified
            ]
            by_id = {a.action_id: a for a in run.actions}
            actions = [
                view.model_copy(update={"args": self._args_for(run_id, by_id[view.action_id], reader,
                                                               on_behalf_of)})
                if view.action_id in by_id else view
                for view in packet.actions_taken
            ]
            packet = packet.model_copy(update={"verified_facts": facts, "claimed_not_verified": slots,
                                               "actions_taken": actions})
        packet = packet.model_copy(update={"subject": run.subject})  # autorizado sobre ese subject
        out: dict[str, JsonValue] = to_jsonable(packet)
        out["resolution"] = to_jsonable(record.resolution)
        return out

    def _args_for(self, run_id: str, action: Action, reader: Principal,
                  obo: OnBehalfOf | None) -> dict[str, JsonValue]:
        value = self._projector.for_reader(run_id, action.args, action.tool.id, reader, obo)
        return value if isinstance(value, dict) else {}

    # --- utilidades compartidas con record_resolution --------------------------------------------------

    @staticmethod
    def _load_record(uow: UnitOfWork, handoff_ref: str) -> HandoffRecord:
        raw = uow.get_handoff(handoff_ref)
        if raw is None:
            raise EngineError(ProblemCode.not_found, handoff_ref)
        return HandoffRecord.model_validate(raw)

    @staticmethod
    def _load_run(uow: UnitOfWork, run_id: str) -> RunState:
        run = uow.load_run(run_id)
        if run is None:
            raise EngineError(ProblemCode.not_found, run_id)
        return run

    def _authorize(self, reader: Principal, obo: OnBehalfOf | None, run: RunState) -> None:
        decision = self._authz.authorize_subject(reader, obo, run.subject)
        if not decision.allowed:
            raise EngineError(ProblemCode.subject_forbidden, decision.reason or "")
```

Importa también `Action` de `agent_core.domain`. La tabla de autorización (delegación vigente, alcance, expiración) es de `AuthzPort`/M9: M10 solo respeta su decisión.

- [ ] **Step 4: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10 -v && uv run mypy && uv run ruff check --fix . && uv run lint-imports`
Expected: PASS. Si `for_reader` devuelve `"***"` para `document_number` con el asesor, revisa que el `field` que `render` pregunta a la política sea `document_number` (nombre del campo, no la ruta): lo pone `TokenVault.tokenize(..., field_name(path), ...)` de M7.

- [ ] **Step 5: Commit**

```bash
git add agent_core/handoff/service.py tests/m10/test_get.py
git commit -m "feat(m10): HandoffService.get renderizado por lector con M7 (T-M10-05)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `HandoffService.record_resolution` (T-M10-06)

**Files:**
- Modify: `agent_core/handoff/service.py`
- Test: `tests/m10/test_resolution.py`

**Interfaces:**
- Consumes: Task 7 (`_load_record`, `_load_run`, `_authorize`), `EventRecorder`, `HandoffResolved`, `HandoffResolvedPayload`, `TurnInProgress`.
- Produces: `HandoffService.record_resolution(handoff_ref: str, reader: Principal, resolution_code: str, handoff_quality: HandoffQuality, notes: str | None = None, *, on_behalf_of: OnBehalfOf | None = None) -> EngineEvent`. Errores: `422 invalid_request` (código o calidad inválidos, `notes` > 2000), `404 not_found`, `403 subject_forbidden`, `409 handoff_already_resolved`, `409 turn_in_progress` (resolución concurrente).

- [ ] **Step 1: Escribir la prueba que falla**

`tests/m10/test_resolution.py`:

```python
"""`record_resolution`: T-M10-06. Una sola resolución por handoff; las notas no van al evento."""

from datetime import timedelta

import pytest

from agent_core.domain import EngineError, EscalationRequest, HandoffResolved, ProblemCode
from agent_core.handoff.packet import HandoffRecord
from testing.builders import advisor_with_delegation
from tests.m10.helpers import HandoffAuthz, escalate_and_commit, make_state, make_world, seed_run

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _world():  # type: ignore[no-untyped-def]
    world = make_world(authz=HandoffAuthz())
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    return world


def test_t_m10_06_resolution_is_recorded_once() -> None:
    """T-M10-06: la resolución se registra una sola vez; la segunda da `409 handoff_already_resolved`."""
    world = _world()
    advisor, obo = advisor_with_delegation()
    event = world.service.record_resolution("handoff-0001", advisor, "caso_resuelto", "useful",
                                            "cliente atendido", on_behalf_of=obo)
    assert isinstance(event, HandoffResolved)
    assert event.payload.model_dump() == {"handoff_ref": "handoff-0001", "resolution_code": "caso_resuelto",
                                          "handoff_quality": "useful", "reader_type": advisor.type}
    assert event.run_id == "run-0001" and event.turn_id is None and event.ts == world.clock.now()
    assert [e.type for e in world.store.events["run-0001"]] == ["escalated", "handoff_resolved"]
    record = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"])
    assert record.resolution is not None and record.resolution.notes == "cliente atendido"

    with pytest.raises(EngineError) as again:
        world.service.record_resolution("handoff-0001", advisor, "otra", "incomplete", on_behalf_of=obo)
    assert again.value.code is ProblemCode.handoff_already_resolved and again.value.status == 409
    assert len(world.store.events["run-0001"]) == 2  # sin segundo evento
    assert HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).resolution == record.resolution


def test_notes_never_reach_the_event_and_get_returns_the_resolution() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    world.service.record_resolution("handoff-0001", advisor, "caso_resuelto", "useful", "nota libre",
                                    on_behalf_of=obo)
    assert "nota libre" not in world.store.events["run-0001"][-1].model_dump_json()
    got = world.service.get("handoff-0001", advisor, obo)
    assert got["resolution"]["handoff_quality"] == "useful"  # type: ignore[index]


def test_resolution_requires_delegation() -> None:
    world = _world()
    advisor, _ = advisor_with_delegation()
    with pytest.raises(EngineError) as denied:
        world.service.record_resolution("handoff-0001", advisor, "x", "useful")
    assert denied.value.status == 403
    assert HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).resolution is None


@pytest.mark.parametrize(("code", "quality", "notes"), [
    ("", "useful", None), ("Con Mayúsculas", "useful", None), ("ok", "great", None), ("ok", "useful", "x" * 2001),
])
def test_invalid_input_is_a_422(code: str, quality: str, notes: str | None) -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as bad:
        world.service.record_resolution("handoff-0001", advisor, code, quality, notes, on_behalf_of=obo)  # type: ignore[arg-type]
    assert bad.value.code is ProblemCode.invalid_request and bad.value.status == 422


def test_unknown_handoff_is_not_found() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as missing:
        world.service.record_resolution("handoff-9999", advisor, "ok", "useful", on_behalf_of=obo)
    assert missing.value.status == 404


def test_concurrent_resolution_is_rejected_while_the_lease_is_held() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with world.store.uow() as other:  # otro turno/resolución con el lease vigente
        other.acquire_turn("run-0001", "otro-turno", world.clock.now(), timedelta(seconds=30))
        with pytest.raises(EngineError) as busy:
            world.service.record_resolution("handoff-0001", advisor, "ok", "useful", on_behalf_of=obo)
    assert busy.value.code is ProblemCode.turn_in_progress


def test_the_recorder_hook_receives_the_event_in_the_same_transaction() -> None:
    seen: list[tuple[str, list[str]]] = []

    def recorder(uow, state, events):  # type: ignore[no-untyped-def]
        seen.append((state.run_id, [e.type for e in events]))
        uow.append_events(state.run_id, events)

    world = make_world(authz=HandoffAuthz(), record=recorder)
    escalate_and_commit(world, seed_run(world, make_state()), REQUEST)
    advisor, obo = advisor_with_delegation()
    world.service.record_resolution("handoff-0001", advisor, "ok", "useful", on_behalf_of=obo)
    assert seen == [("run-0001", ["handoff_resolved"])]
```

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_resolution.py -v`
Expected: FAIL con `AttributeError: 'HandoffService' object has no attribute 'record_resolution'`.

- [ ] **Step 3: Implementar**

En `agent_core/handoff/service.py` agrega imports (`re`, `timedelta`, `HandoffResolved`, `HandoffResolvedPayload`, `TurnInProgress`, `Resolution`, `HandoffQuality`) y, dentro de la clase:

```python
    # --- record_resolution -----------------------------------------------------------------------------

    def record_resolution(self, handoff_ref: str, reader: Principal, resolution_code: str,
                          handoff_quality: HandoffQuality, notes: str | None = None, *,
                          on_behalf_of: OnBehalfOf | None = None) -> EngineEvent:
        """Registra la resolución del receptor una sola vez: marca el registro y agrega `handoff_resolved`."""
        _validate_resolution(resolution_code, handoff_quality, notes)
        now = self._clock.now()
        with self._uow_factory() as uow:
            record = self._load_record(uow, handoff_ref)
            run = self._load_run(uow, record.packet.run_id)
            self._authorize(reader, on_behalf_of, run)
            lease = f"resolve-{handoff_ref}"
            try:
                uow.acquire_turn(run.run_id, lease, now, _RESOLUTION_LEASE)
            except TurnInProgress:
                raise EngineError(ProblemCode.turn_in_progress, "resolución en curso") from None
            record = self._load_record(uow, handoff_ref)  # relee con el lease tomado
            if record.resolution is not None:
                uow.release_turn(run.run_id, lease)
                uow.commit()
                raise EngineError(ProblemCode.handoff_already_resolved, handoff_ref)
            event = HandoffResolved(
                event_id=self._ids.new_id(IdKind.event), run_id=run.run_id, turn_id=None,
                session_id=run.session_id, release=run.release, ts=now,
                payload=HandoffResolvedPayload(handoff_ref=handoff_ref, resolution_code=resolution_code,
                                               handoff_quality=handoff_quality, reader_type=reader.type),
            )
            resolution = Resolution(resolution_code=resolution_code, handoff_quality=handoff_quality,
                                    notes=notes, resolved_at=now, reader_type=reader.type, reader_id=reader.id)
            uow.put_handoff(handoff_ref, to_jsonable(record.model_copy(update={"resolution": resolution})))
            self._record(uow, run, [event])
            uow.release_turn(run.run_id, lease)
            uow.commit()
            return event
```

Y a nivel de módulo, antes de la clase:

```python
_RESOLUTION_LEASE = timedelta(seconds=30)
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_:-]{0,63}")
_QUALITIES = frozenset({"useful", "incomplete", "unnecessary"})
_MAX_NOTES = 2000


def _validate_resolution(code: str, quality: str, notes: str | None) -> None:
    if _CODE_RE.fullmatch(code) is None:
        raise EngineError(ProblemCode.invalid_request, "resolution_code inválido")
    if quality not in _QUALITIES:
        raise EngineError(ProblemCode.invalid_request, "handoff_quality inválido")
    if notes is not None and len(notes) > _MAX_NOTES:
        raise EngineError(ProblemCode.invalid_request, "notes demasiado largas")
```

Deja `HandoffQuality` importado de `agent_core.handoff.packet`.

- [ ] **Step 4: Ejecutar y confirmar que pasa**

Run: `uv run pytest tests/m10 -v && uv run mypy && uv run ruff check --fix . && uv run lint-imports`
Expected: PASS. Si `test_concurrent…` falla porque el servicio no ve el lease, confirma que la prueba toma el lease con un `turn_id` distinto y con el `now` del `FakeClock` (el doble vence leases con el `now` que recibe).

- [ ] **Step 5: Commit**

```bash
git add agent_core/handoff/service.py tests/m10/test_resolution.py
git commit -m "feat(m10): record_resolution con una sola resolución por handoff (T-M10-06)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Interfaz pública, trazabilidad y cierre

**Files:**
- Modify: `agent_core/handoff/__init__.py`
- Create: `tests/m10/test_public_api.py`, `tests/m10/test_traceability.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: la interfaz pública de M10 que importan M4 y M9.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m10/test_public_api.py`:

```python
"""Interfaz pública de M10: lo que importan M4 y M9 (M10 §2)."""

import agent_core.handoff as handoff

EXPECTED = {
    "ActionView", "EventRecorder", "FactView", "HandoffPacket", "HandoffPreconditionError", "HandoffQuality",
    "HandoffRecord", "HandoffService", "RequestSummary", "Resolution", "SlotView", "append_events",
}


def test_public_api() -> None:
    assert set(handoff.__all__) == EXPECTED
    for name in EXPECTED:
        assert getattr(handoff, name) is not None
```

`tests/m10/test_traceability.py`:

```python
"""Cada prueba T-M10-NN del spec tiene al menos una prueba que la nombra."""

from pathlib import Path


def test_every_spec_test_id_has_a_test() -> None:
    here = Path(__file__).parent
    others = [p for p in here.glob("test_*.py") if p.name != Path(__file__).name]
    text = "".join(p.read_text(encoding="utf-8") for p in others)
    missing = [f"T-M10-{n:02d}" for n in range(1, 9) if f"T-M10-{n:02d}" not in text.replace("t_m10_", "T-M10-")]
    assert missing == []
```

(Los nombres de las pruebas usan `test_t_m10_01_…` y sus docstrings `T-M10-01`; el `replace` cubre ambos.)

- [ ] **Step 2: Ejecutar y confirmar que falla**

Run: `uv run pytest tests/m10/test_public_api.py tests/m10/test_traceability.py -v`
Expected: `test_public_api` FALLA (faltan exportaciones); la trazabilidad puede pasar si las Tasks 5–8 nombraron T-M10-01…08 (01, 04, 07, 08 en `test_escalate.py`; 02 y 03 en `test_builder.py`; 05 en `test_get.py`; 06 en `test_resolution.py`).

- [ ] **Step 3: Implementar la interfaz pública**

`agent_core/handoff/__init__.py`:

```python
"""M10 — escalamiento y handoff (ADR 0013): paquete estructurado, evento saliente y cierre del run."""

from agent_core.handoff.errors import HandoffPreconditionError
from agent_core.handoff.packet import (
    ActionView,
    FactView,
    HandoffPacket,
    HandoffQuality,
    HandoffRecord,
    RequestSummary,
    Resolution,
    SlotView,
)
from agent_core.handoff.recorder import EventRecorder, append_events
from agent_core.handoff.service import HandoffService

__all__ = [
    "ActionView", "EventRecorder", "FactView", "HandoffPacket", "HandoffPreconditionError", "HandoffQuality",
    "HandoffRecord", "HandoffService", "RequestSummary", "Resolution", "SlotView", "append_events",
]
```

Cambia en `tests/m10/test_escalate.py` el import de `HandoffPreconditionError` a `from agent_core.handoff import HandoffPreconditionError` si aún apunta a `agent_core.handoff.errors`.

- [ ] **Step 4: Ejecutar el módulo**

Run: `uv run pytest tests/m10 -v`
Expected: PASS (T-M10-01…08 y las pruebas complementarias).

- [ ] **Step 5: Correr todas las comprobaciones del repo**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo en verde; el contrato `M10 (handoff) solo usa domain, ports y views` en `KEPT`; `contracts --check` sin cambios (M10 no toca tipos de M0). Verifica además que M10 no importa internos de M7: `git grep -n "agent_core.views\." -- agent_core/handoff` debe estar vacío (solo `from agent_core.views import …`).

- [ ] **Step 6: Commit**

```bash
git add agent_core/handoff/__init__.py tests/m10/test_public_api.py tests/m10/test_traceability.py tests/m10/test_escalate.py
git commit -m "feat(m10): interfaz pública y trazabilidad T-M10-01…08

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Marcar la definición de terminado**

Revisa punto por punto contra el spec rev. 2 y el índice §8, y repórtalo al usuario:

- [ ] `escalate`, `get` y `record_resolution` con T-M10-01…08 en verde (`uv run pytest tests/m10`).
- [ ] Plantillas de traspaso (mensaje) en ES y PT **en `agent-registry`**: pendiente fuera de este repo (el `Template` `handoff` de la prueba es sintético); en este repo quedan los respaldos y el resumen en `agent_core/handoff/texts.py`. Díselo al usuario explícitamente.
- [ ] Interfaz pública exportada y con tipos (`test_public_api.py`, `mypy`).
- [ ] `import-linter` en verde.
- [ ] Eventos que emite (`escalated`, `handoff_resolved`) validados contra el esquema de M0: se construyen con las clases de M0 (`Escalated`, `HandoffResolved`), que validan sus payloads; `handoff_created` va en `OutboxMessage` con `HandoffCreatedPayload`.
- [ ] Sin TODO sin issue (`git grep -n "TODO" -- agent_core/handoff tests/m10` vacío).
- [ ] Trabajo pendiente en otros módulos, para avisar:
  - **M4:** llamar `escalate(..., uow=uow, turn_id=...)` tras `ActionManager.invalidate`, pasar como `events_so_far` el historial del run más los eventos del turno, guardar el run devuelto, agregar los eventos, encolar el outbox y emitir `run_closed{closed_by: escalation}`; el `410` del turno siguiente y T-M10-01 completo (con M4) se prueban allí.
  - **M9:** `post_resolution` debe pasar `on_behalf_of` y traducir `EngineError`; inyectar el `EventRecorder` que encadena (M11) al construir `HandoffService`.
  - **M0/registry:** si se quiere el resumen desde `agent-registry`, agregar un campo de plantilla de resumen a `EngineTemplates` (cambio de interfaz).

---

## Self-review (hecho al escribir el plan)

- **Cobertura del spec:** §2 interfaz → Tasks 2, 6, 7, 8, 9; §3.1 pasos 1–7 → Task 6 (paso 1 por precondición, 2 → Task 5, 3 → Task 6 `put_handoff`, 4 → estado, 5 → `escalated`, 6 → outbox, 7 → mensaje); §3.2 → Task 7; §3.3 → Task 8; §4 invariantes → transcript solo por referencia (Task 5), outbox+cierre atómicos (Task 6, T-M10-08), run sin turnos tras escalar (estado `escalated`; `410` es de M4), `reason_code` validado por `ReasonCodeStr` (Task 2); §5 fallas → paquete degradado (Task 5/6), outbox caído (unidad 4, queda en el outbox); §6 eventos → Tasks 6 y 8; §7 T-M10-01…08 → tabla abajo; §10 DoD → Task 9 Step 7; §11 Abierto → Decisión 1.
- **Trazabilidad de pruebas:** T-M10-01 `test_escalate.py`; 02 y 03 `test_builder.py`; 04 `test_escalate.py` (y parte en `test_builder.py`); 05 `test_get.py`; 06 `test_resolution.py`; 07 y 08 `test_escalate.py`.
- **Consistencia de nombres:** `Projector.audit/for_reader`, `build_packet/build_minimal_packet/evidence_refs`, `HandoffService.escalate/get/record_resolution`, `HandoffRecord.packet/resolution`, `FactView.name`, `_load_record/_load_run/_authorize`, `_RESOLUTION_LEASE`, `PURPOSE = "handoff"`: usados igual en todas las tasks.
