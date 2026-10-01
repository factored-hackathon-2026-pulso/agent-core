# M11 — Auditoría, transcript y replay

- Estado: rev. 4 (2026-10-01) · Fase 2 (cadena, spans, transcript) y fase 5 (replay)
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
def verify_transfer_link(target: RunState, sink: AuditSink) -> list[str]   # enlace entre cadenas (§3.1b); [] = válido
def link_problems(origin: RunOrigin, target_run_id, source_events, target_events) -> list[str]  # núcleo puro (interno)
# agent-telemetry
def span(name, **attrs) -> ContextManager       # agrega run_id, turn_id, session_id, agentcore.release
# Replay
class ReplayReport: mode; run_id; release; verdict: Literal["match", "diverged", "chain_broken"]
                    first_divergence: {event_seq, expected, actual} | None; chain_broken_at: int | None; duration_ms: int | None
                    chain_broken_run: str | None; chain_broken_reason: str | None   # decisión 24
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

### 3.1b Enlace entre cadenas de una transferencia (ADR 0021 D9, P2)

`verify_transfer_link(target, sink)` comprueba que la cadena de un run destino está atada a la del origen. Solo lee; devuelve la lista de problemas (vacía = enlace válido).

- **No aplica:** si `target.origin` es `None` (el run no nació de una transferencia) devuelve `[]`.
- **Qué ata:** `RunOrigin.from_event_hash` es el hash del `turn_completed` del origen del turno que transfirió (no el de `run_transferred`: ADR 0021 P2).
- **Búsqueda, no posición:** el hash se busca en toda la cadena de origen; no tiene que ser el último evento. Un evento posterior (p. ej. `access_denied` por una lectura sobre el run ya cerrado) no rompe el enlace.
- **Comprobaciones:** (1) la cadena de origen existe y pasa `check_chain`; (2) el hash está en ella y es el de un `turn_completed`; (3) hasta ese evento hay exactamente un `run_transferred` con el `transfer_id` del origen y su `to_run_id` es el run destino, y el `turn_id` del `turn_completed` es el del `run_transferred`; (4) el primer evento del origen es un `run_started` cuyo agente y `release` coinciden con `origin.from_agent` y `origin.from_release_id`; (5) el primer evento del destino es un `run_started` de ese `run_id` cuyo `origin` es idéntico al del estado, y cuyo agente y `release` coinciden con `to_agent` y `to_release_id` del `run_transferred`. Así un `RunOrigin` con `from_run_id`, hash y `transfer_id` reales pero agente o release falsos no pasa.
- **Alcance:** solo el enlace. La integridad de la cadena del destino la da `AuditLog.verify_chain`.
- **Sin datos:** los problemas son mensajes fijos, sin hashes, ids, slots ni texto. Con la cadena de origen ausente devuelve un único problema (no se puede comprobar más).
- El núcleo es puro (`link_problems(origin, target_run_id, source_events, target_events)`, en `audit/links.py`): `verify_transfer_link` lee las dos cadenas del `AuditSink` y lo llama. El replay `fixture` de una sesión lo usa sobre las cadenas del fixture antes de correr el motor (decisión 24).
- Conectar `verify_transfer_link` (sobre el almacén de auditoría) a `agentcore replay` por `run_id` sigue pendiente (spec de transferencia §8 y §12.10).

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
- Una sesión que transfiere (ADR 0021) lleva además `linked`: las cadenas de los runs destino, en orden de creación. El campo se omite del YAML cuando está vacío, así que los fixtures sin transferencia no cambian (decisión 24).
- La sesión de la demo de transferencia (fase 7) es el escenario `transferencia` sobre `tests/fixtures/registry-transfer-demo`: `recepcion` lee el directorio, elige `disputas` y le transfiere, y `disputas` responde en el mismo turno. Su fixture vive aparte, en `tests/fixtures/runs-transfer/transferencia.yaml`, para que `tests/fixtures/runs/` siga con los seis caminos de `registry-demo`. Se graba con `agentcore record transferencia --registry tests/fixtures/registry-transfer-demo --catalog tests/fixtures/catalogo-datos-prueba.yaml` (decisión 24).

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
| T-M11-11 | Enlace de transferencia (§3.1b): válido; un evento posterior en el origen no lo rompe; origen alterado, hash falsificado o de otro evento, `from_agent`, `from_release_id`, `to_agent` o `to_release_id` falsos (cada uno solo, sobre cadenas re-encadenadas válidas), `turn_completed` de otro turno, cadena de origen ausente, `run_transferred` ausente, con otro `transfer_id` o con otro `to_run_id`, y `run_started` del destino con otro origen dan problemas legibles sin datos; un run sin origen da `[]` (`tests/m11/test_transfer_links.py`) | — |
| T-M11-12 | Sesión con transferencia (decisión 24): `RecordedIds` reparte `to_run_id` y `transfer_id` sin repetir el run destino; un fixture sin `linked` se escribe sin la clave, los seis fixtures commiteados cargan con `linked == []` y se reescriben byte a byte, y uno con `linked` va y vuelve con `linked` como última clave; `from_event_hash` no se compara pero `transfer_id`, `from_run_id` y `depth` sí; el motor recibe `events + linked`, y un evento cambiado o un run ausente en lo producido diverge; una cadena enlazada alterada da `chain_broken` con `chain_broken_run` y motivo, y una del origen sin `chain_broken_run`; **enlace roto → `chain_broken` antes de correr el motor** (aunque el motor de prueba daría `match`): hash falsificado (`"0"*64`), cadena cambiada de otra grabación con los mismos ids, hash de otro evento, `transfer_id` del intento rechazado, `from_run_id`/`from_agent`/`from_release_id` falsos, agente del destino distinto de `to_agent`, run no nombrado por ningún `to_run_id`, run repetido del origen y run sin `origin`; un segundo run enlazado cuyo origen es el primero (profundidad 2) da `match`; solo un sha256 hex exacto en minúsculas queda exento en `check_fixture`, y no en un campo `pii_direct` (`tests/m11/test_replay_transfer.py`) | 2 |
| T-M11-13 | Fixture grabado de la sesión con transferencia (decisión 24, fase 7): `agentcore replay` de `tests/fixtures/runs-transfer/transferencia.yaml` con `--catalog` da `match` (dos runs, dos releases); el fixture está vigente (regrabarlo da los mismos bytes); su `full` de `directory/list` lleva `choices: [consultas, disputas]`; `linked` empieza con el `run_started` del destino con `origin`, y `verify_transfer_link` sobre las cadenas grabadas da `[]`; un enlace falsificado sobre ese fixture (`from_event_hash`, `transfer_id` o `from_release_id`, con la cadena del destino re-encadenada y válida) da problemas y `chain_broken` con `chain_broken_run` y el motivo; un evento del destino alterado sin re-encadenar da `chain_broken`; una release que el registro no tiene es un error del runner (`tests/composition/test_replay_transfer_fixture.py`) | 2 |

## 8. Evaluación

Es la **fuente de datos** de la unidad 6 y de la auto-mejora: exportación de eventos por run/release con los atributos de reporte de `run_started`, incluidos los campos de medición (latencias, uso del LLM, `turn_completed`). Las trazas OTel dan el detalle de un run; las métricas agregadas se calculan desde este log porque las trazas se muestrean. Métricas propias: runs con cadena íntegra (objetivo 100 %), tiempo de replay por run, cobertura de caminos por fixtures.

## 9. Puntos de iteración

- Almacén cifrado de entradas `full` (producción): habilita el recálculo completo de runs reales sin cambiar el algoritmo de replay (solo el origen de las entradas).
- Backend de trazas: Langfuse cambiando el endpoint OTLP.
- Evento nuevo: solo esquema en M0.

## 10. Definición de terminado

- Fase 2: cadena, spans, transcript y T-M11-06…09.
- T-M11-10 va con el replay (fase 5).
- Transferencia entre agentes (rev. 4): `verify_transfer_link` y T-M11-11.
- Transferencia, fase 7: replay `fixture` de una sesión que transfiere (decisión 24) y T-M11-12. Pendientes: `verify_transfer_link` en `agentcore replay` y replay por `run_id` de la sesión.
- Transferencia, fase 7 (runner y fixture grabado): `RecordedEngineRunner` con todas las releases del registro, escenario `transferencia`, `tests/fixtures/runs-transfer/transferencia.yaml` en `match` y T-M11-13. Los seis fixtures de `tests/fixtures/runs/` no cambian.
- Fase 5: replay `fixture` en CI con los seis caminos y T-M11-01…05; `audit` si alcanza.
- Comandos `agentcore record`, `agentcore replay --mode fixture|audit`: implementados con códigos de salida 0/1/2/3; sin motor o sin `--registry` salen con 3 ("motor no disponible").
- **Task 14 (2026-09-29, hecho):** `record` real y replay `fixture` con el motor compuesto (`agent_core.composition.build_turn_engine`); T-M11-01 con los seis caminos en `match` (`tests/composition/test_replay_fixtures.py` y el paso de CI). El modo `audit` no está: ver riesgos. T-M11-02…10 tienen prueba en `tests/m11`.

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
9. **IDs en replay:** un run real usó `SystemIds`, así que `call_id`, `decision_id`, `action_id`, `handoff_ref`… son aleatorios y van dentro de los payloads (sí se comparan). `RecordedIds` reparte, por `IdKind`, los IDs grabados en orden de cadena. Riesgo: si un módulo crea IDs en un orden distinto al de los eventos, aparece una divergencia falsa (se detecta en la Task 14). **Transferencia (ADR 0021, fase 7):** también son IDs grabados `to_run_id` (`IdKind.run`, de `run_transferred`) y `transfer_id` (`IdKind.transfer`, de `run_transferred`, `transfer_rejected` y `transfer_received`), en orden de cadena y sin repetir: el run destino aparece primero como `to_run_id` en la cadena del origen y su propio `run_id` ya no suma otro.
10. **Reloj grabado:** `RecordedClock.now()` es constante dentro de un turno (el `ts` de su `turn_started`; el de `run_started` para el alta; `set()` para los instantes de `expiry_evaluated`/barrido). `monotonic_ns()` devuelve `0`.
11. **Modos:** `fixture` sirve a las tools el resultado en vista `full` del fixture (`FixtureFullSource`) y los borradores del LLM del fixture. `audit` sirve el `result` de vista `audit` del evento `tool_called`, los textos del transcript como borradores y pasa `ForbiddenFullSource`; el motor lee los resultados de `rule`/`verify`/validador con `RecordedReadings` (M2/M3/M8 deben respetar ese contrato en modo `audit`; ver riesgos).
12. **`ReplayReport`** añade campos opcionales (aditivos): `chain_broken_at: int | None` y `duration_ms: int | None`. `verdict` y `first_divergence` como en el spec.
13. **Catálogo de datos de prueba (§13.2 no lo define):** archivo YAML `tests/fixtures/catalogo-datos-prueba.yaml` con `email_domains`, `numbers` (documentos/teléfonos/productos inventados) y `values` (valores `pii_direct` permitidos). Un fixture se rechaza si en `inputs`/`full`/`drafts` hay un email fuera de los dominios, un número de 6+ dígitos fuera de `numbers`, o una hoja de un campo `pii_direct`/`pii_quasi` (según `FieldClassifier`) fuera de `values`. Los `events` no se escanean (vista `audit`, con hashes hex que darían falsos positivos). **Exención sha256 (fase 7, decisión del usuario):** una hoja que es exactamente un sha256 hex en minúsculas (`[0-9a-f]{64}`, coincidencia completa) no se rechaza por dígitos ni por email, por la misma razón: el resultado `full` de `directory/list` lleva el `hash` del directorio. No exime una hoja de un campo `pii_direct`/`pii_quasi` (esa regla va antes), ni un hash dentro de otro texto, en mayúsculas o de otra longitud.
14. **Formato del fixture:** YAML, `tests/fixtures/runs/<camino>.yaml`. Los números con decimales se leen como `Decimal` (loader propio); un `Decimal` se escribe como escalar `float` explícito. El campo opcional `linked` (último) va solo si no está vacío (decisión 24).
15. **`agent-telemetry`:** paquete de primer nivel `agent_telemetry/` en este repo (el "paquete común" se extraerá luego sin cambios de API). La versión de semconv GenAI queda fijada en la constante `SEMCONV_VERSION` (ADR 0003 no pudo confirmar su estabilidad; **verifica la versión contra el paquete `opentelemetry-semantic-conventions` instalado antes de fijarla**).
16. **Exportación de evaluación (§8):** `export_events(sink, run_ids, release=None)` recibe la lista de `run_id` (el `AuditSink` no tiene listado por release; el listado es de la unidad 6/4).
17. **Códigos de salida de `agentcore replay`:** `0` match, `1` diverged, `2` chain_broken, `3` error de uso o motor no disponible.
18. **Dónde vive el `EngineRunner` (decisión del usuario, 2026-09-29):** no en `agent_core.cli` sino en `testing/replay/` (herramienta de desarrollo; necesita el almacén en memoria de `testing.fakes`). `agent_core.cli.load_engine(registry)` importa `testing.replay` de forma perezosa; si el paquete `testing` no existe (wheel instalado) o falta `--registry`, sale con 3. Sustituye al marcador `agent_core.turn.build_engine_runner` (que `turn` no podía proveer: no puede importar `response`, `views` ni `adapters`).
19. **`--registry DIR` en `replay` y `record`** (decisión del usuario): registro de autoría de la release grabada, como `agentcore sweep`. `record` acepta además `--catalog` y rechaza un fixture con datos no sintéticos.
20. **Cómo reproduce el motor (modo `fixture`):** M2, M3, M4, M6, M7, M8 y M10 son los reales; las tools, el LLM, el reloj y los IDs salen de `RecordedPorts`. Las decisiones de M5 salen de los `decision_made` grabados (`RecordedProvider`) y `DecisionService` real las recalibra con un artefacto sintetizado que reproduce los `above_threshold` grabados (solo cadena de un proveedor: `fallback_depth > 0` no se reproduce; los `slots` de la 2.ª llamada de Understand tampoco). Con una transferencia, el `EngineRunner` devuelve los eventos de **todos** los runs de la sesión, run a run en orden de creación (decisión 24).
    - **Runner de `testing/replay` (fase 7):** fija **todas** las releases del registro de autoría (una por agente, ADR 0021 D3), no una sola. La release reproducida es `case.release` y debe ser la del alias `prod` del agente de entrada, que sale del `run_started` grabado; si el registro no la tiene, o el alias apunta a otra, el runner lanza `ValueError` (la CLI sale con 3). El artefacto sintetizado se registra con el nombre de cada `thresholds_from` de los modelos de decisión del registro (`cal-demo` en `registry-demo`, `cal-transfer-demo` en `registry-transfer-demo`). `IdKind.transfer` se reparte de lo grabado, como `run`. No se pasa `directory`: `directory/list` se sirve de lo grabado como cualquier tool. Los `turn_id` de los `turn_started` grabados se deduplican en orden antes de indexarlos por operación, porque el destino repite el `turn_id` del turno del origen; con un `turn_started` por operación (los seis caminos) no cambia nada.
21. **IDs de `fact` y `message`:** no viajan en ningún evento, así que `RecordedIds` inventaba `fact-replay-0001` y el borrador grabado (que cita `fact-000N`) fallaba por citas. El replay `fixture` usa `RecordedIds` para los tipos presentes en la cadena (event, run, session, turn, call, decision, action, handoff) y la secuencia de `FakeIds` (con que se graban los fixtures) para el resto y para los secretos. Es el riesgo 9 confirmado; el orden de IDs de evento no importa porque el comparador ignora `event_id`.
22. **Operaciones del fixture:** `inputs` = `{"op": "start"|"turn"|"confirm", "text"?, "answer"?, "lang"?, "auth"?}`. El token de confirmación no se graba: el replay usa el de la última confirmación pendiente. Un solo `Driver` (`testing/engine_world.py`) aplica las operaciones al grabar y al reproducir.
23. **Caminos y límites:** `resuelto`, `cancelado`, `escalado_por_monto`, `uncertain_verify` (`uncertain` con efecto → `verify` → resuelto), `step_up` e `interrupcion`. `step_up` pide el step-up y lo completa (2026-09-30): el turno siguiente llega con credencial elevada, M4 refresca `principal.auth` (`_refresh_auth`) y el flow termina `resolved`. Los fixtures se regraban con `agentcore record` y `test_el_fixture_committeado_esta_vigente` falla si quedan desactualizados.

24. **Sesión con transferencia (ADR 0021, fase 7; decisión del usuario).** El origen transfiere y el destino corre en el mismo turno (llama a Understand y a sus tools), así que reproducir solo el run origen no alcanza:
    - **Formato:** `Fixture.linked: list[AnyEvent]` (último campo, vacío por defecto) con las cadenas de los runs destino, en orden de creación. `build_fixture(..., linked=...)` lo llena al grabar; `dump_fixture` lo omite si está vacío.
    - **Cadenas y enlace, antes de correr el motor** (función pura sobre el fixture, sin `RunState` ni `AuditSink`): primero `check_chain` del run reproducido; después, para cada run de `linked` (agrupado por `run_id` en orden de aparición): (1) no repite un run anterior de la sesión; (2) su cadena pasa `check_chain`; (3) empieza con un `run_started` con `origin`; (4) `origin.from_run_id` es el run reproducido o un run enlazado anterior; (5) `link_problems` (el núcleo de `verify_transfer_link`, §3.1b) no encuentra problemas contra esa cadena: `from_event_hash` es el hash del `turn_completed` que cierra el turno del `run_transferred` con ese `transfer_id`, cuyo `to_run_id` es este run; `from_agent`/`from_release_id` coinciden con el `run_started` del origen; `to_agent`/`to_release_id` con el `run_started` del destino. Así todo run enlazado está nombrado por un `to_run_id`.
    - **Veredicto:** la primera falla da `chain_broken` con `chain_broken_at` (el `seq` roto, o `0` si falla el enlace), `chain_broken_run` (el run enlazado; `None` si se rompió la cadena del run reproducido) y `chain_broken_reason` (el motivo de `check_chain` o el primer mensaje de `link_problems`: textos fijos, sin datos). El `run_id` del reporte sigue siendo el del run reproducido. `agentcore replay` muestra el run enlazado y el motivo.
    - **Puertos grabados** sobre `events + linked`: reloj, IDs, tools, decisiones y lecturas de las dos cadenas.
    - **Comparación** sobre `events + linked` contra lo que devuelve el `EngineRunner`. Un run de más o de menos es una divergencia. `first_divergence.event_seq` es el `seq` dentro de la cadena de su run (el `run_id` va en `expected`).
    - **No se compara** `run_started.payload.origin.from_event_hash`: es un hash de cadena y el replay no reproduce hashes (`ts`, `duration_ms`), por lo mismo que se ignoran `hash` y `prev_hash`. El resto de `origin` sí se compara. El hash no queda sin comprobar: lo comprueba el paso de enlace sobre lo grabado (arriba).
    - **Pendiente:** `verify_transfer_link` sobre el almacén de auditoría todavía no está conectado a `agentcore replay` (el replay `fixture` usa su núcleo puro sobre el fixture).
    - **Pendiente:** el replay por `run_id` (modo `audit`) no sigue la sesión: `linked` queda vacío y un run origen que transfirió diverge.
    - **Grabación (`record_scenario`):** `events` es la cadena del run de entrada (`Driver.run_id`) y `linked`, las cadenas de los demás runs de la sesión en el orden de `list_runs_by_session`. Cada escenario graba sobre su registro (`SCENARIO_REGISTRY`, por defecto `registry-demo`) y su mundo (`transfer_world` para `transferencia`); `--registry` lo sustituye.

### Riesgos / abiertos para Task 14 (integración)

1. **Orden de la cadena en `AuditLog.recorder()`.** *(Verificado con el motor compuesto: `verify_chain` da ok en los seis caminos y sobre Postgres; sigue siendo un riesgo si M4 cambia el orden de volcado.)* Solo agrega los eventos de M3. M3 (`actions/context.py:46-50`) y el spec de M4 (§14) exigen volcar primero los eventos pendientes del turno y después los de M3. M4 debe envolverlo (vuelca lo pendiente con `AuditLog.append` y luego estos eventos) o `recorder()` recibe un `pending` opcional (callable). Si no, el orden de la cadena difiere del orden causal y el replay diverge en falso.
2. **El replay `audit` recalcula huellas que no puede reproducir.** `RecordedToolExecutor` devuelve el `result` de vista `audit` como `result_full`; M3 (`actions/execution.py:146`) lo re-proyecta y re-huella (`result_fp` difiere). `response_emitted.transcript_fp` necesita la clave de producción y el `kid` grabado (se rompe tras una rotación). El modo `audit` descarta `source`/`required_level` (el camino de step-up pierde `required_level`). Dirección sugerida: un `AuditProjector` grabado que devuelva `(result, result_fp)` grabados por `call_id`, y un proveedor de claves fijado al `kid` grabado.
3. **Appends concurrentes al mismo run** (M9 `append_standalone` contra un turno en curso) chocan con `UniqueViolation` en `(run_id, seq)` en Postgres: usar `pg_advisory_xact_lock(run_id)` o reintentar. El doble en memoria no impone la unicidad `(run_id, seq)`; conviene que la imponga.
4. **Evolución de esquema (§9 de M0).** El hash es sobre `to_jsonable(modelo)`, incluidos los campos `None` y los valores por defecto: agregar cualquier campo a un evento de M0 cambia el hash recalculado de todos los eventos guardados y rompe `verify_chain` de las cadenas históricas. Abierto para la regla de evolución de M0.
5. **`PgAuditEvents` no es un `AuditSink`** (no tiene `append_outside_turn`). Un `AuditSink` Postgres para M9 debe encadenar o negarse.
6. **Modo `audit` con fuente `Fixture`** deja `drafts = []`: cualquier llamada al LLM se desincroniza. Quien implemente `EngineRunner` no debe exponer texto del transcript (el motor recibe `text_model`). *(El marcador `agent_core.turn.build_engine_runner` se sustituyó por `testing.replay`, ver decisión 18.)* `JsonLogFormatter` descarta `exc_info` a propósito (PII).
7. **`check_chain([])` devuelve ok**, así que `chain_integrity` cuenta un run desconocido o vacío como íntegro. (El replay por `run_id` sí lanza `RunNotFound` para un run sin eventos.)
