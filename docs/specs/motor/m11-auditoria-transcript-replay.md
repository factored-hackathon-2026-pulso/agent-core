# M11 — Auditoría, transcript y replay

- Estado: rev. 2 (2026-09-29) · Fase 2 (cadena, spans, transcript) y fase 5 (replay)
- Paquete: `agent_core.audit` (+ paquete común `agent-telemetry`)
- Origen: spec general §8.4, §11, §13.2, §13.6 (huellas del transcript)
- ADRs: 0003 (dos planos, transcript separado, replay en dos modos), 0008 (huellas con clave)
- Usa: M0, M7 · Lo usan: M4 (cada turno), M9 (`access_denied`, lectura del transcript), unidad 6 (datos)

## 1. Propósito y límites

Tres responsabilidades:

1. **Log de auditoría:** encadenar por hash los eventos de cada run y persistirlos (append-only).
2. **Trazas y transcript:** spans OTel con atributos `agentcore.*`; envío del texto del turno al transcript store con huella con clave.
3. **Replay:** reconstruir un run desde sus registros en modo `fixture` (CI) o `audit` (runs reales).

**No hace:** el esquema completo del log ni la entrega de eventos salientes (unidad 4), la retención del transcript (unidad 7), comparar releases distintas (unidad 6).

## 2. Interfaz pública

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
    # orden: [user, *rejected, final]; falla del store -> TranscriptWriteError
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

## 3. Comportamiento

### 3.1 Cadena de hash

- `hash_n = sha256(JCS(evento_n sin hash) ‖ hash_{n−1})`, con `hash_0` = constante por run (`sha256("agentcore:" ‖ run_id)`).
- `seq` monotónico por run; el append ocurre dentro de la transacción del turno o de la de escritura (M3).
- La cadena usa `sha256` sin clave porque encadena eventos ya en vista `audit` (§8.1.1).
- Fórmula exacta: decisión 2.

### 3.2 Spans

`invoke_agent` › `agentcore.decide`, `agentcore.rule`, `execute_tool`, `chat`, con `agentcore.release`, `agentcore.agent`, `agentcore.flow`, `agentcore.node`, `agentcore.principal_type`, `agentcore.locale` y `gen_ai.*` (versión de semconv fijada). Captura de contenido desactivada por defecto; si se activa, vista `audit`. Exportación OTLP a Phoenix en la demo.

### 3.3 Transcript

- Por turno se envían al `TranscriptStore`, en vista `model`: mensaje del usuario, respuesta final y borradores rechazados con su motivo.
- La huella de cada entrada la calcula M11 con `fingerprint` de M7 (HMAC + `kid`); el store solo devuelve `entry_id`.
- `read_rendered` autoriza al lector y renderiza tokens con M7.
- Nunca se guarda razonamiento intermedio.

### 3.4 Replay: diseño

El replay **vuelve a correr el mismo motor** (M4 + M2) con puertos "grabados" que responden desde los registros, y compara la secuencia de eventos producida con la registrada.

| Puerto | En replay |
|---|---|
| `Clock` | instantes de `turn_started` / `expiry_evaluated`; `monotonic_ns()` devuelve una constante (solo alimenta campos de medición) |
| `GuardService` | salida registrada en `turn_started` |
| `DecisionService` / `UnderstandService` | `decision_made` / `command_emitted` |
| `ToolExecutor` (lectura y escritura) | `tool_called` |
| `LLMGateway` | borradores del fixture (`fixture`) o `response_emitted` (`audit`) |

Qué se recalcula en cada modo (tabla de §11):

| | Se lee siempre de eventos | Transiciones, manejadores, `pending_intents`, `locale`, contadores, invalidación | `rule`/`policy`, `compute`, predicado de `verify`, validador, reclamos |
|---|---|---|---|
| `fixture` | sí | recalcula | **recalcula** con las entradas `full` del fixture |
| `audit` | sí | recalcula | lee `rule_evaluated`, `tool_called` (compute), `action_verified`, `response_emitted` y comprueba que la transición sea la que dicta el flow |

Pasos: `verify_chain` → si está rota, `chain_broken` → correr el motor con puertos grabados → comparar evento a evento (ignorando `event_id`/`seq`/`prev_hash`/`hash`/`ts` y los campos de `MEASURED_FIELDS`, M0 §2.10) → `match` o `diverged` con la primera divergencia.

### 3.5 Fixtures

- Formato: `tests/fixtures/runs/<camino>.yaml` con eventos + entradas en vista `full` (resultados de tools y salidas de modelos).
- Se generan grabando runs de la demo con **datos sintéticos** (`agentcore record`).
- CI rechaza un fixture con un valor fuera del catálogo de datos de prueba.
- Un fixture por camino del flow de demo: resuelto, cancelado, escalado por monto, `uncertain → verify`, step-up, interrupción.

## 4. Invariantes

- El log es append-only: no hay update ni delete (permiso de base de datos, no solo de código).
- Replay nunca llama a modelos ni tools reales.
- Replay `audit` nunca accede a la vista `full`.
- Ninguna huella del transcript es `sha256` sin clave.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Cadena rota | `chain_broken` en ambos modos |
| Fixture con datos no sintéticos | rechazo en CI |
| Transcript store caído | el turno falla y se reintenta (el texto no se pierde en silencio) — propuesta; ver Abiertos |
| Backend de trazas caído | se descartan spans; el log de auditoría no depende de él |

## 6. Eventos que emite

Ninguno de dominio. Encadena y persiste los de todos los módulos.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M11-01 | Replay `fixture` en CI da `match` para cada camino del flow de demo | 2 |
| T-M11-02 | Cambiar una regla, una `compute` o el validador que altera un resultado → `diverged` con la primera divergencia | 2 |
| T-M11-03 | Evento alterado → `chain_broken` en ambos modos | 2 |
| T-M11-04 | Replay `audit` sobre un run grabado da `match` sin acceder a `full` (el doble de `full` lanza si se usa) | 2 |
| T-M11-05 | Fixture con un valor no sintético se rechaza | 2 |
| T-M11-06 | Huella de entrada del transcript es HMAC con `kid` | 6 |
| T-M11-07 | Transcript renderizado respeta los permisos del lector | 6 |
| T-M11-08 | Suprimir el transcript no rompe `verify_chain` | — |
| T-M11-09 | Todo span y evento lleva `run_id` y `agentcore.release`; `trace_id` en respuestas | — |
| T-M11-10 | Un run grabado cuyos campos de medición difieren de los recalculados da `match`; un cambio en cualquier otro campo del mismo evento da `diverged` | 2 |

## 8. Evaluación

Es la **fuente de datos** de la unidad 6 y de la auto-mejora: exportación de eventos por run/release con los atributos de reporte de `run_started`, incluidos los campos de medición (latencias, uso del LLM, `turn_completed`). Las trazas OTel dan el detalle de un run; las métricas agregadas se calculan desde este log porque las trazas se muestrean. Métricas propias: runs con cadena íntegra (objetivo 100 %), tiempo de replay por run, cobertura de caminos por fixtures.

## 9. Puntos de iteración

- Almacén cifrado de entradas `full` (producción): habilita el recálculo completo de runs reales sin cambiar el algoritmo de replay (solo el origen de las entradas).
- Backend de trazas: Langfuse cambiando el endpoint OTLP.
- Evento nuevo: solo esquema en M0.

## 10. Definición de terminado

- Fase 2: cadena, spans, transcript y T-M11-06…09.
- T-M11-10 va con el replay (fase 5).
- Fase 5: replay `fixture` en CI con los seis caminos y T-M11-01…05; `audit` si alcanza.
- Comandos `agentcore record`, `agentcore replay --mode fixture|audit`.

## 11. Abiertos

- Transcript store caído: resuelto en rev. 2 (falla el turno); queda abierta la idempotencia del reintento con la unidad 7.

## 12. Decisiones de la rev. 2


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
