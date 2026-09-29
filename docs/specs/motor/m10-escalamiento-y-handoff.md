# M10 — Escalamiento y handoff

- Estado: borrador · Fase 2
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
    def escalate(self, state, request: EscalationRequest, events_so_far) -> tuple[RunState, list[EngineEvent], OutboxMessage, Message]
    def get(self, handoff_ref, reader: Principal, on_behalf_of) -> dict          # renderizado para el lector
    def record_resolution(self, handoff_ref, reader, resolution_code, handoff_quality, notes) -> EngineEvent
```

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
- Plantillas de traspaso y de resumen en ES y PT en `agent-registry`.

## 11. Abiertos

- `request_summary`: ¿plantilla o generado? La spec dice "validado con §8.3", lo que sugiere generado. Propuesta: plantilla en el MVP (sin costo de LLM ni riesgo de validación) y generado como mejora.
