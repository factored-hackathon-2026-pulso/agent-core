# M10 — Escalamiento y handoff

- Estado: rev. 2 (2026-09-29) · Fase 2
- Paquete: `agent_core.handoff`
- Origen: spec general §9, escalamiento de §4, §8.4 (transcript para el asesor), §12 (calidad del handoff), §13.10
- ADRs: 0013 (evento saliente, cierre del run, transcript renderizado), 0006 (delegación al asignar)
- Usa: M0, M7 · Lo usan: M4 (al escalar), M9 (rutas de handoff)

## 1. Propósito y límites

Convierte un escalamiento en un `HandoffPacket` estructurado, emite `handoff_created` al outbox en la transacción del turno, cierra el run para el bot y registra la resolución del receptor.

**No hace:** colas, disponibilidad de asesores ni mensajes fuera de horario (plataforma del asesor), firmar delegaciones (emisor de asignaciones), entregar el outbox (unidad 4), incrustar el transcript (lo referencia).

## 2. Interfaz pública

```python
class HandoffPacket:
    handoff_ref; run_id; release; agent: EntityRef; principal_type; subject: SubjectRef   # ref enmascarado
    target_queue; priority; reason_code; language
    request_summary: {text, citations}
    verified_facts: list[FactView]            # hechos con procedencia, vista del lector
    claimed_not_verified: list[SlotView]      # slots claimed
    actions_taken: list[ActionView]           # con estado de verificación
    open_questions: list[str]
    evidence_refs: list[str]                  # call_ids, policy@v evaluadas, decision_ids, page@v
    transcript_ref: str                       # → GET /v1/runs/{run_id}/transcript

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

## 3. Comportamiento

### 3.1 `escalate`

1. Invalida acciones pendientes (llama a M3 vía M4; M10 recibe el estado ya invalidado).
2. Construye el paquete:
   - `verified_facts`: todos los `facts` con su `source` (vista `audit` al guardar).
   - `claimed_not_verified`: slots `claimed`.
   - `actions_taken`: acciones con su estado (`verified`, `failed`, `uncertain`, `cancelled`).
   - `evidence_refs`: `call_id` de `tool_called`, `policy@v` de `rule_evaluated`, `decision_id`.
   - `request_summary`: **propuesta MVP:** plantilla determinista por `reason_code` y `locale`, rellenada con hechos (ver Abiertos).
   - `priority`: la del `EscalationRequest` (de `priority_expr` o de la interrupción).
3. Persiste el paquete en vista `audit` + referencias a hechos (el render al lector usa M7).
4. Estado: `status = escalated`, `outcome = escalated`, `handoff_ref`.
5. Evento: `escalated {reason_code, target_queue, priority, handoff_ref}`. `run_closed` lo emite M4 (único emisor, M0 §2.10).
6. Outbox: `handoff_created {handoff_ref, run_id, target_queue, priority, reason_code, language, reportable_attrs}`.
7. Mensaje final al principal: plantilla de traspaso en el `locale`.

Todo lo anterior entra en la **misma** transacción del turno (M4).

### 3.2 `get`

Autoriza al lector (asesor con delegación vigente sobre el subject, o `service` con scope) y renderiza con M7: los campos que la política le permite en claro, el resto enmascarado.

### 3.3 `record_resolution`

`{resolution_code, handoff_quality: useful | incomplete | unnecessary, notes?}` → evento `handoff_resolved`. Es etiqueta para la unidad 6 y señal para la auto-mejora. Una segunda resolución sobre el mismo handoff se rechaza (`409`).

## 4. Invariantes

- El paquete nunca incrusta el transcript.
- El outbox y el cierre del run se commitean juntos o no se commitea ninguno.
- Después de `escalate`, el run no acepta turnos (`410`, M4).
- `reason_code` pertenece a la lista de M0 o a los prefijos `rule:`, `policy:`, `interrupt:`.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Falla al construir el paquete | se escala igual con un paquete mínimo (`reason_code`, hechos, `transcript_ref`) y se marca `degraded_packet: true` |
| Destino del outbox caído | problema de la unidad 4; el mensaje queda en el outbox |

## 6. Eventos que emite

`escalated`, `handoff_resolved`; saliente: `handoff_created` (en un `OutboxMessage` de M0). Una segunda resolución → `409 handoff_already_resolved`.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M10-01 | `escalate` emite `handoff_created` en el outbox y cierra el run; el siguiente turno da `410` (con M4) | 10 |
| T-M10-02 | El paquete separa hechos verificados de slots `claimed` | — |
| T-M10-03 | `actions_taken` refleja `uncertain`/`verified`/`cancelled` | — |
| T-M10-04 | El paquete persistido no contiene `pii_direct` en claro | 6 |
| T-M10-05 | `get` como asesor con delegación ve los campos permitidos; sin delegación, `403` | 4 |
| T-M10-06 | Resolución registrada una sola vez | — |
| T-M10-07 | Escalamiento por monto (`policy:escalamiento-disputa-monto`) con prioridad correcta | 2 (fixture) |
| T-M10-08 | Falla del outbox en la transacción revierte también el cierre del run | — |

## 8. Evaluación

Precisión/recall de escalamiento (contra referencias de la unidad 6), `handoff_quality` de las resoluciones, calidad del handoff por LLM juez con rúbrica validada con muestra humana, distribución de `reason_code`.

## 9. Puntos de iteración

- `request_summary` generado: pasa por M8 (`validate`) sin cambiar el paquete.
- Destino del outbox: lo cambia la unidad 4 sin tocar M10.
- Devolución del caso al bot (producción): run nuevo o `resume`, fuera de este módulo.

## 10. Definición de terminado

- `escalate`, `get` y `record_resolution` con T-M10-01…08 en verde.
- Plantillas de traspaso en ES y PT en `agent-registry` (mensaje) y por defecto en `agent_core/handoff/texts.py` (resumen y respaldo).

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
12. `get` devuelve `run.subject` tal cual a lectores autorizados (`authorize_subject` ya concede ese subject; no pasa por render). `put_handoff` debe ser upsert (`record_resolution` sobrescribe el registro). El lease de `record_resolution` (id único por llamada) se libera solo al commit: si algo falla antes, queda tomado hasta el TTL de 30 s.

## 12. Abiertos

- Quién llena `open_questions` (M2/M10, índice §10).
- `request_summary` generado por LLM (pasaría por M8 `validate`) y plantillas de resumen en `agent-registry`.
- Un handoff con muchos hechos: no hay tope de tamaño del paquete; decidir con datos reales.
- Devolución del caso al bot (producción): fuera de este módulo (ADR 0013).
