# Temas abiertos — Motor de decisión (spec 2026-09-28)

- Estado: **9 de 9 temas originales resueltos; #10 (`read` construido), #11, #12, #14, #15 y #16 cerrados el 2026-09-30 (decisiones abajo); #13 resuelto salvo las piezas reales de las unidades 3, 6 y 7 (2026-09-30); #18 decidido el 2026-09-30 (construcción pendiente); sigue abierto #17 (medio); #19 (transferencia entre agentes) con las fases 1 a 7 en `main` (la 7 llegó desde la rama `feat/transferencia-demo`) y los spans OTel de la transferencia en la rama `feat/transferencia-otel`, con pendientes; #20 (conector de datasets reales para las evals) abierto (medio).** Reemplaza la versión anterior de este documento, cuyo contenido se descartó por basarse en hallazgos incorrectos.
- Fecha: 2026-09-28
- Spec: `2026-09-28-motor-de-decision-design.md` (rev. 15)
- Regla de trabajo: antes de resolver cada tema se lee el ADR que lo gobierna.

## Descartado de la iteración anterior

Estas propuestas **no se aplican**:
- **`token_map` efímero en memoria.** Contradice ADR 0008 (`token_map` cifrado en el estado del run). Además rompe la resolución de tokens entre turnos, la recuperación `executing → verify` (ADR 0007) y el render del transcript y del handoff después del cierre.
- **Tool `detect_language` de unidad 3 invocada en cada turno, con umbral 0.7 fijo.** El comportamiento sticky ya estaba en §4.3, y el replay ya consume la salida registrada de las guardas. El umbral fijo viola la regla de umbrales calibrados.
- **`respond` reclama éxito implícitamente.** Choca con la regla 6.1.6, que permite `respond` seguro en ramas de fallo.

Tampoco eran temas abiertos: la cadena de hash y `reportable_attrs` (ADR 0003), los datos PT (fuera de alcance, §7, ADR 0012), el destino del outbox (unidad 4, §15) y la numeración §4.x.

## Estado

| # | Tema | Severidad | Estado |
|---|---|---|---|
| 1 | `claims_success` referenciado pero no declarado | Alta | **Resuelto (rev. 6)** |
| 2 | `reauth` corre después de Understand | Alta | **Resuelto (rev. 7)** |
| 3 | Qué recalcula el replay y qué lee de eventos | Alta | **Resuelto (rev. 8)** |
| 4 | `sha256` sin clave en la vista `audit` | Media | **Resuelto (rev. 11)** |
| 5 | Detector de idioma sin especificar | Media | **Resuelto (rev. 9)** |
| 6 | `unclear` de `confirm` sin tope | Media | **Resuelto (rev. 10)** |
| 7 | `end(abandoned)` declarable; outcome vs modo sin validar | Baja | **Resuelto (rev. 12)** |
| 8 | Dos ADR con número 0009 | Baja | **Resuelto (rev. 12)** |
| 9 | `untrusted_text` falta en ADR 0008 | Baja | **Resuelto (rev. 12)** |
| 10 | ADR de conocimiento (0015) aceptado pero no integrado en la spec | Alta | **Resuelto como diseño (2026-09-30); construcción en fase 2** |
| 11 | Capa de analítica (cálculo y visualización de métricas) sin spec | Media | **Resuelto (2026-09-30)** |
| 12 | Política del contexto conversacional (`recent_turns`) sin definir | Media | **Resuelto (2026-09-30)** |
| 13 | Servidor arrancable: faltan las piezas externas del cableado | Alta | **Resuelto salvo las piezas reales de las unidades 3, 6 y 7 (2026-09-30)** |
| 14 | Emisión y firma de los roles `constructor`/`aprobador` y de `attrs.actor` | Alta | **Resuelto (2026-09-30)** |
| 15 | Validador `decide` de `collect`: qué campo de la decisión valida | Media | **Resuelto: no soportado hasta la fase 2 (2026-09-30)** |
| 16 | Límites, retención y topes del agente autónomo (registry) | Media | **Resuelto salvo el monto del tope de costo, que se fija al activar el constructor `task` (2026-09-30)** |
| 17 | Clase `write_draft` y dependencias del constructor sobre el registry | Media | **Parcial: fases 1 a 5 hechas (2026-09-30); siguen el replay de M11, los agentes y `await_approval`** |
| 18 | Auditoría de las lecturas de datos de clientes del administrador | Media | **Decidido (2026-09-30); construcción pendiente** |
| 19 | Transferencia entre agentes: pendientes | Media | **Fases 1 a 6 en `main`; fase 7 (demo, cableado, índice de un run abierto por sesión y replay de la sesión) de la rama `feat/transferencia-demo`, ya en `main`; spans OTel de la transferencia hechos (2026-10-02) en la rama `feat/transferencia-otel`; pendiente de aprobación; abiertos y pendientes listados abajo** |
| 20 | Conector de datasets reales para las evals de los agentes | Media | **Abierto** |

## Resueltos
- **#1 (rev. 6):** `respond.claims` declarado + derivado; invariante por camino hasta `verified`; `respond` seguro = sin reclamos. Spec §2, §5, §6.1, §8.2, §11, §13.3, §14, §15; ADR 0007.
- **#2 (rev. 7):** credenciales rechazadas con `401`/`403` antes del turno; `principal_mismatch`; límites por principal después de la firma; `reauth` fuera de los manejadores. Spec §3, §4.1, §4.5, §10, §11, §13.5; ADR 0006 y 0010.
- **#3 (rev. 8):** replay en modos `fixture` (CI, recálculo completo) y `audit` (integridad + transiciones); almacén `full` como diseño de producción. Spec §0, §11, §13.2, §15; ADR 0003.
- **#4 (rev. 11):** huellas HMAC-SHA256 con clave rotada por `kid` y JCS, en vista `audit` y transcript; cadena de eventos sigue en `sha256`; clave por subject como diseño de producción. Spec §0, §8.1.1, §8.4, §11, §13.6, §14, §15; ADR 0008 y 0003.
- **#5 (rev. 9):** lingua-py local fijado en la release, reglas `short`/`undetermined`/histéresis/no soportado, umbrales por calibración (1.0 si faltan), comparación con JEV en la prueba de humo. Spec §2, §4.3, §8.3, §11, §13.8, §14, §15; ADR 0012 y 0005.
- **#6 (rev. 10):** `max_attempts` en `confirm`; reentrada idempotente; con `confirm` pendiente, `out_of_scope`/`clarify`/bajo umbral dan `unclear`. Spec §4.4, §4.5, §5, §8, §8.2, §13.11; ADR 0007.
- **#7 (rev. 12):** outcomes declarables por modo; `abandoned`/`escalated` solo los asigna el motor; regla 6.1.14; chequeo de modo en el gate; `end(failed)` como salida de fallo en modo task. Spec §2, §5, §6.1, §6.2, §13.1.
- **#8 (rev. 12):** el ADR de conocimiento pasa a `0015-consumo-de-conocimiento-nodo-knowledge.md`; 0009 queda para políticas protegidas.
- **#9 (rev. 12):** ADR 0008 enmendado con la clase `untrusted_text`, su catálogo por defecto y su envoltura en la vista `model`.

## 10. Integración del ADR 0015 (conocimiento) — resuelto; `read` construido
**Decidido el 2026-09-30:** se aprueba M12 (`motor/m12-conocimiento.md`) como la integración del ADR 0015 en el motor; la spec general pasa a apuntar a M12 y deja de contradecir al ADR. La **construcción** va a la fase 2: no entra en el MVP de la demo (el calendario no da para `read` antes del congelamiento del 02/10). `navigate` queda fuera del MVP.
- **Numeración:** reglas de M1 G0-17…G0-21 y comprobaciones 6 y 7 del validador de respuesta, como propone M12.
- **Hechos de conocimiento y reclamos de éxito:** una página solo se cita; nunca alimenta un reclamo (los reclamos salen de acciones verificadas, ADR 0011).
- **Caída del `KnowledgeSource`:** el nodo sale por `not_found` y emite un evento con motivo `source_unavailable`; no cambia el esquema del nodo y la auditoría distingue la causa.
**Construcción de `read` cerrada el 2026-09-30** (`SCHEMA_VERSION` 1.0.0, cambio mayor por el tipo de nodo nuevo y el reemplazo de `knowledge_refs`): `RunState.pages`, `PageView`, el nodo `knowledge`, `knowledge_from[]`/`purpose` en `respond.generate`, el evento `knowledge_read`, `AuthzPort.knowledge_view`, `FileKnowledgeSource` con su suite de contrato, G0-17…G0-21 y las comprobaciones 6 y 7 (T-M12-01…06 en verde; ver `motor/m12-conocimiento.md` §10).
Sigue abierto, fuera de esta construcción: el modo `navigate` (esquema y G0-20 listos; el runtime sale por `not_found` con motivo `navigate_unavailable`), la búsqueda, y los puntos de `m12-conocimiento.md` §12 (G0-06 para `not_found`/`denied`, anclas sin verificar al publicar, servicio real de la unidad 7).

## 11. Capa de analítica — resuelto
**Decidido el 2026-09-30:**
- **Cálculo:** vistas SQL sobre el log de eventos en Postgres; sin componentes nuevos.
- **Consumo en la demo:** Phoenix para latencia y costo (spans OTel `agentcore.*` desde el 2026-10-02: `invoke_agent` por turno con hijos derivados de los eventos y `agentcore.transfer`; ver M11 §3.2 y la sección «Telemetría» del README) y una vista SQL con las métricas de negocio (contención, resolución segura, `abandoned`, aclaraciones por run, modo degradado). No se construye un dashboard propio.
- **Comparación entre releases:** no hay una regla nueva. El gate del registry (guardarraíles y margen de ruido, registry §6) decide las promociones; la analítica solo informa.
- **Retención:** sin purga en el MVP (las tablas son inmutables y pequeñas); se revisa en la fase 2 junto con la retención del registry (#16).
Actualización (2026-09-30): el DSL de métricas por agente, el catálogo de eventos y la regla de que no hay acción automática en producción se decidieron en el ADR 0020; este tema conserva el cálculo, la visualización, las alertas y la retención.
Actualización (2026-10-01): el evaluador en memoria del DSL existe (`agent_core/registry/evaluation/metric_eval.py`): NULL para campos ausentes, percentil discreto y redondeo a 4 decimales son la semántica que el compilador a SQL debe reproducir (T-EVAL-14, hoy parcial; T-EVAL-04 pendiente).
Abierto del gate (spec de evaluación §13.14): un agente sin `eval_suite` aún puede llegar a `validated`; lo frena el gate al evaluar (T-EVAL-11 parcial). Falta una relación agente → suite al validar.
Pendiente de construcción: las vistas SQL (la conexión a Phoenix queda documentada en la sección «Telemetría» del README); no bloquean la fase 1 porque los eventos ya capturan los datos.

## 12. Política del contexto conversacional — resuelto
**Decidido el 2026-09-30** (ratifica lo que ya implementa `EngineConfig.recent_turns`; ver m04 §15 y m05 §3.2):
- **Tamaño:** `n` fijo de 6 turnos, por despliegue; se mide en turnos, no en tokens, y no es configurable por agente.
- **Conversaciones largas:** se trunca a las últimas `n` entradas; no hay resumen en el MVP (un resumen es texto generado por un modelo y arrastra la duda de `untrusted_text` y del replay).
- **Contenido:** mensaje del cliente y respuesta del agente, en vista `model`, sin borradores rechazados. Los hechos siguen saliendo solo de acciones verificadas, nunca del historial.
- **Entre runs:** nada; un asunto retomado es un run nuevo con contexto limpio.

## 13. Servidor arrancable — resuelto salvo las piezas reales
**Decidido el 2026-09-30:** la demo corre con un modelo real y el resto como dobles etiquetados. Spec: `docs/superpowers/specs/2026-09-30-servidor-arrancable-design.md`; plan: `docs/superpowers/plans/2026-09-30-servidor-arrancable.md`.
- **Construido:** `agentcore serve` (`agent_core/composition/serve.py`, `serve_ports.py`, `build_engine` en `engine.py`). Reales: Postgres, gateway de LLM, JEV a través del llm-gateway (`POST /v1/jev`, ADR 0024; la key de JEV vive en el servicio), claves HMAC/cifrado (`EnvKeyProvider`) y claves públicas de identidad (`--identity-keys`, `agent_core/adapters/identity_keys.py`). `uvicorn` declarado en `pyproject.toml`.
- **Dobles, solo con `AGENTCORE_ALLOW_DEMO=1`** (`testing/serve_demo.py`; `serve` los lista al arrancar): tools, `AuthzPort`, transcript en memoria, calibración, proveedor `classifier`, `FieldClassifier`, `grant_active` y, sin `--identity-keys`, el verificador de demo. Sin la variable, `serve` sale con código 2 y nombra cada pieza faltante.
- **Sigue pendiente, con su punto de enchufe y su dueño:**

| Pieza real | Opción de `serve` | Dueño |
|---|---|---|
| `ToolExecutor` | `--tools` | unidad 3 |
| `AuthzPort` | `--authz` | unidad 3 |
| `TranscriptStore` persistente | `--transcript` | unidad 7 |
| Calibración con artefactos etiquetados | `--calibration` | unidad 6 (P9) |
| Artefactos del proveedor `classifier` | `--classifier` | unidad 6 (P9) |
| Catálogo de `FieldClassifier` (clasificación de campos publicada por el equipo de datos, m07) | `--field-classifier` | equipo de datos (sin unidad asignada) |
| Servicio de identidad real con `grant_active` | `--grant-active` (y `--identity-keys`) | servicio de identidad (sin unidad asignada) |

- **API HTTP del registry:** `serve --registry-api` (opt-in) la monta con evaluación real (gateway, JEV y calibración de `serve`; tools en `LocalSandbox`), verificador del staff (`--staff-keys`) y base propia de evaluaciones (`--eval-dsn`). Spec: `docs/superpowers/specs/2026-09-30-serve-registry-api-design.md`. Una falla de JEV o del classifier durante una evaluación da `failed_infra`. Abiertos de esa spec: la evaluación es síncrona y los transcripts de evaluación van al almacén de `serve`. El CLI `agentcore registry` no cambia.
- **Aviso de alias de LLM sin configurar (gateway §5):** cableado con `--agents` (o `AGENTCORE_SERVE_AGENTS`); revisa la release `prod` de cada agente indicado y solo avisa.

## 14. Roles del registry y `attrs.actor` — resuelto
**Decidido el 2026-09-30.** Implementado en `agent_core/registry/roles.py`, `testing/fakes/authz.py` y `testing/fakes/identity.py`; el detalle de permisos está en registry §8 y la enmienda de datos de clientes en ADR 0006.

| Perfil | Principal | Registry | Datos de clientes |
|---|---|---|---|
| Usuario con un problema | `customer` | Ninguno | Solo los propios |
| Asesor | `advisor` con delegación firmada | Ninguno (por ahora) | Solo el subject delegado |
| Supervisor | `builder`, persona, `constructor` + `aprobador` | Construye, aprueba, publica y promueve a `staging` y a `prod` | Ninguno |
| Administrador | `builder`, persona, `constructor` + `aprobador` + `admin` | Todo, y además revoca releases e importa la semilla | Sí, con `step_up` y un scope |
| Agente constructor | `builder` con credencial de servicio, sin `actor` | Solo `constructor` | Ninguno |

- **Solo un `builder` opera el registry**, lecturas incluidas; la lista de roles es cerrada.
- **`aprobador` y `admin` exigen persona y `step_up`** (código `step_up_required`).
- **Emisores:** el staff tiene su propio emisor y su propia clave; la API del registry solo verifica esas claves (`TestStaffIssuer` en la demo; `testing/demo_identities.py` emite supervisor, administrador y bot).
- **Roles:** salen de los grupos del proveedor de identidad del staff; el núcleo solo los valida.
- **Autoaprobación:** se mantiene permitida, registrada en `approvals`. La aprobación de cuatro ojos sigue siendo de la fase 2 (registry §16).
- **Datos de clientes del administrador:** ADR 0006 enmendado; contrato de `AuthzPort` actualizado.

## 15. Validador `decide` de `collect` — resuelto: no soportado hasta la fase 2
**Decidido el 2026-09-30:** M1 lo rechaza en G0-01 y queda así para la demo; para validar un slot bastan `type`, `regex` y `enum`. Contrato previsto para la fase 2: el `decision_model` declara un campo calibrado `valid` (booleano); el slot se acepta si `valid` es verdadero y supera el umbral calibrado, y si queda bajo el umbral cuenta como intento fallido y repregunta. El texto del slot se proyecta como `untrusted_text` (m02 D8).

## 16. Límites, retención y topes del agente autónomo — resuelto salvo los topes
**Decidido el 2026-09-30:**
- **Límites:** se ratifican los valores que ya aplica `registry/validation.py`: 50 cambios por propuesta, 262 144 bytes por entidad y 200 nodos por flow.
- **Retención:** se conserva todo en el MVP. Fase 2: purgar propuestas abandonadas de más de 90 días y conservar las últimas N evaluaciones por propuesta.
- **Topes del constructor autónomo (decidido el 2026-09-30):** 10 propuestas por día y 20 evaluaciones por propuesta. Sigue sin fijar el **monto del tope de costo por propuesta** (USD); se fija al activar el constructor en modo `task`, que no se activa en la demo. Los topes de 10 propuestas por día y 20 evaluaciones por propuesta ya se aplican en `RegistryService` (2026-09-30); el tope de costo sigue diferido.

## 17. `write_draft` y dependencias del constructor — abierto
Registry §18: clase de riesgo `write_draft` (G0-23, AG-02, ruta de M3), regla G0-25 del gateway (el prompt del nodo `agent` debe ser `structured: prompted`), adaptador de `ToolExecutor` del constructor con su propia credencial, `readback_by` de borradores y catálogo de campos y plantillas de handoff como entidades versionadas. Nada de esto está construido; el constructor solo puede correr de solo lectura.
**Avance (2026-09-30):** hechos registry (idempotencia, `get_write`, topes), M0 (`RiskClass.write_draft`, nodo `draft`, `Action.write_node_id`) y M1 (G0-05, G0-13, G0-22, G0-23, G0-25, AG-02). Siguen M3, M2/M4, el adaptador del constructor, el replay y los agentes.

## 18. Auditoría de las lecturas de datos de clientes del administrador — decidido; construcción pendiente
El administrador puede leer runs, transcripts y campos de clientes (ADR 0006, enmienda 2026-09-30), pero una lectura autorizada no deja hoy ningún evento: solo se registran los rechazos (`access_denied`).
**Decidido el 2026-09-30:**
- **Evento `privileged_read`** con principal, subject, propósito y `trace_id`, **sin el contenido leído**. Es un cambio de M0 (evento nuevo, versión menor: regenerar `contracts/` y avisar) y de M9 (emitirlo en cada lectura autorizada del administrador).
- **Dónde se anota:** en la cadena de eventos del run cuando la lectura es de un run; cuando no lo es, en un log de auditoría propio.
- **Motivo de la lectura obligatorio.**
**Pendiente de construcción:** el esquema del evento y su versión, la forma exacta del log de auditoría propio (tabla, retención y quién lo consulta) y el parámetro por el que M9 recibe el motivo. Hay que cerrar esos tres puntos antes de implementar.

## 19. Transferencia entre agentes: pendientes — abierto
ADR 0021 (propuesto; implementado en las ramas `feat/transferencia-entre-agentes`, fases 1 a 6, `feat/transferencia-demo`, fase 7, y `feat/transferencia-otel`, spans OTel de la transferencia hechos el 2026-10-02; pendiente de aprobación) y spec `2026-09-30-transferencia-entre-agentes-design.md` (reconciliada con lo construido el 2026-10-01; §12 tiene el detalle). Lo que sigue no está decidido aquí, salvo lo marcado como cerrado.

**Trabajo fuera de las fases 1 a 6 (decisión P7 del plan):**
- **REL-T1** (compatibilidad de `accepts` al publicar recepción) y la **evaluación** de la spec §7 (`transferred_to`, `transfer_packet`, `routing_scenarios`, gate del especialista, `yardstick_loosened`). Dependen de `feat/eval-metrics`.
- ~~**Agentes de la demo** (fase 7)~~: **hecho** (recepción, disputas y consultas; registro `registry-transfer-demo`, demo en proceso y HTTP, fixture grabado). Las **suites de evaluación** de esos agentes quedan para la fase 8 (decisión del usuario).
- ~~**Spans OTel** (`agentcore.transfer` y el *span link*).~~ **Hecho (2026-10-02)** (spec de transferencia §8, T-TR-16; m04 §3.9). El enlace entre spans no se persiste; el fixture grabado de la sesión es idéntico con y sin telemetría (T-M11-15).
- **Replay de sesión:** el modo `fixture` ya reproduce la sesión (fase 7). Pendiente: conectar `verify_transfer_link` sobre el almacén de auditoría a `agentcore replay` y el replay por `run_id` (modo `audit`).
- **Vuelta a recepción** (`on_out_of_scope: transfer`) y **especialistas que exigen `step_up`** (hoy se excluyen del directorio).
- **Tope y presupuesto por sesión:** `max_transfers_per_session = 1` es un valor de la demo.

**Abiertos de la implementación (decide el usuario):**
- **Umbral comodín `"*"`:** P3 cierra solo la parte del umbral del Abierto 1 (spec §12.1). Nada genera `"*"` sin conexión (`calibrate` no lo emite): las elecciones de runtime son siempre `low_confidence` hasta que la calibración o un artefacto hecho a mano lo aporte. Siguen abiertos el proveedor (JEV `choice` o `llm_structured`) y cómo se genera.
- **Hash del directorio en el linaje de la sesión** (m09 §11): (a) leer `run_transferred` de la cadena de origen con un puerto nuevo en `ApiDeps`; (b) `directory_hash` opcional en `RunOrigin` (cambio de M0); (c) retirarlo de la spec.
- ~~**Restricción en la base de "a lo sumo un run abierto por sesión"**~~: **cerrado en la fase 7** con el índice `runs_one_open_per_session` (spec §12.8). La ruta de Postgres está sin verificar (sin docker).
- **Alcance de la atomicidad:** cubre runs, cadenas, uso y resultados; los transcripts y las escrituras de M3 quedan fuera (m04 §11). Falta decidir si el destino puede escribir en el turno de la transferencia y qué pasa con una escritura del origen seguida de una caída antes del `commit`.
- **`client_turn_id` único por sesión:** requisito documentado, no comprobado.
- **Rutas de Postgres sin verificar** (sin docker en la ejecución): `aliases_named` del registry, los métodos de sesión de la unidad de trabajo y, desde la fase 7, el índice `runs_one_open_per_session` con su traducción del `UniqueViolation` a `VersionConflict`. Correr `docker compose up -d postgres && uv run pytest tests/integration`.
- **Catálogos de campos de producción:** deben clasificar como `public` los campos del directorio (`choices`, `agent_id`, `release_id`). Además, `accepts` y `supported_locales` de las fichas no tienen regla y un router real los vería tokenizados (`⟦pii:n⟧`); no es una línea de catálogo (nombres de slot dinámicos, búsqueda por último segmento): o se proyectan fuera de la entrada del router en el flow, o hay trabajo de catálogo de producción.
- **Limitaciones de la demo (fase 7):** los flows `disputa-cargo` y `consulta-pqr` no leen el slot `problema` transferido (vuelven a preguntar); el bucle `aclarar` de recepción no tiene tope propio (solo lo acota el número de turnos del usuario); `serve` en modo demo no transfiere (Q4).
- **`VersionConflict` del índice de run abierto** llega al cliente como `500 internal_error` (ni M4 ni M9 lo capturan): decidir si se mapea a 409 o se reintenta.
- **Salida de replay:** `ReplayReport` y `--json` ganaron `chain_broken_run` y `chain_broken_reason`.
- **Decisiones del usuario (fase 7, plan):** Q1 sí (fixture con `linked`), Q2 consecuencia (exención de sha256), Q3 solo en el catálogo de la demo, Q4 `serve` demo no transfiere, Q5 sin suites de evaluación.
- **Decisiones tomadas en la ejecución que el usuario debe confirmar:** `transfer` fuera de `TERMINAL` (M4 sigue `rejected`); `Slot` sin `source` (procedencia por `origin` y `accepted_slots`); `from_event_hash` = hash del `turn_completed`; `RunStartedPayload` omite `origin` cuando es `None` para no invalidar cadenas anteriores a 1.2.0; la tool `directory/list` vive en `composition` (cierra el Abierto 5 de la spec).
- **Más decisiones a confirmar:** `SCHEMA_VERSION` 1.2.0 se asignó en la rama (la spec §12.7 decía "se asigna al integrar"); se usó la rama `feat/transferencia-entre-agentes` en lugar de la rama designada por la sesión.

**Observabilidad (spans y logs, 2026-10-02): decisiones que el usuario debe confirmar.** Las 16 «Preguntas para el usuario» del plan (`docs/superpowers/plans/2026-10-02-observabilidad-y-spans-de-transferencia.md`) se aplicaron con la opción recomendada; ninguna se discutió con el usuario. Las que tocan el comportamiento:
- **Un turno de abandono (`410 run_closed` por vencimiento) cierra `invoke_agent` con estado `ERROR` y `error.type = EngineError`.** El `410` se lanza dentro del span, para que lleve `agentcore.problem_code`; pero el vencimiento es un desenlace normal y un dashboard lo contaría como error. Opciones: dejarlo, o cerrar el span sin error y conservar solo el código.
- **El aviso `WARNING` de «la telemetría falló» (logger `agent_core.turn`, solo el tipo de la excepción) no tiene límite de frecuencia:** con una telemetría rota de forma permanente sale uno por falla, y como `record` corre tras cada `EventChain.append`, eso es uno o más por turno (medido: 2 o 3 por turno con `FailingTelemetry('record', 'exit')`, 12 en 4 turnos en el camino de `step_up`).
- **`setup_tracing` ya no instala el provider global de OTel.** Los tracers de agentcore salen de `tracer(name)`; una librería que use el tracer global de OTel no exportará por esta vía.
- **`.importlinter` prohíbe a M4 importar `agent_telemetry`.** M4 informa por su puerto local `TurnTelemetry`; la implementación real vive en `composition`.
- **uvicorn arranca con `log_config=None` y `access_log=False`;** el único handler de root es el formatter JSON, a nivel `INFO` fijo (no se configura por variable). Se silencian `openai`, `httpx` y `httpcore` (`WARNING`).
- **Soporte parcial de `OTEL_*`:** solo las variables listadas en M11 §3.2 y el README; protocolo `http/protobuf` (no `grpc` ni `http/json`); un valor inválido o un header mal formado es un error de arranque (exit 2).
- **Telemetría de mejor esfuerzo:** los hijos derivados de los eventos de un turno que luego se revierte se exportan igual, sin deduplicar (m04 §3.9). El `latency_ms` es monótono y el `ts` es de pared, así que el inicio derivado de un hijo es aproximado.
- **Cierre con el colector inalcanzable:** el apagado vacía los spans pendientes y puede tardar hasta unos 10 s; un segundo Ctrl-C en ese lapso imprime un traceback de Python que no es JSON (sin secretos). Opciones: dejarlo (documentado en el README), o acortar el timeout del vaciado.
- **Contenido apagado:** los atributos de span son una lista cerrada (`ALLOWED_ATTRIBUTES`). `set_content` (vista `audit`) solo actúa tras `agent_telemetry.configure(capture_content=True)`, y `agentcore serve` no tiene un interruptor para eso: hoy el contenido no sale de ninguna forma por configuración.

**Pendientes y hallazgos de la revisión final (por lectura del código; lo marcado no se ejecutó):**
- **Cablear `directory/list`** (cerrado en la fase 7; texto de la revisión final): `DirectoryToolExecutor`/`RegistryDirectory` no están en `composition/__init__.py` y `build_turn_engine`/`EngineDeps` no envuelven `deps.tools`; solo las pruebas los usan (`tests/m04/harness.py` importa el módulo interno).
- **Linaje por run del registry** (`RunLineage`, `lineage_for_run`) sin `origin`.
- **Replay del run origen que transfirió, por lectura del código, no ejecutado:** `RecordedIds.from_events` (`audit/replay/ports.py:113-122`) no recoge `run_transferred.to_run_id` ni `transfer_id`; `IdKind.run` está en `_RECORDED_KINDS` (`testing/replay/runner.py`), así que `Transferer.validate` recibe `run-replay-0001`; el motor reproducido seguiría hacia el destino; el runner conoce una sola release.
- **G0-22 sin cambios ni prueba de transferencia:** `decide.choices_from` puede leer un hecho de un nodo `agent`; la garantía real es G0-26/G0-27 y destino ∈ `snapshot.choices` en M4. Endurecer queda pendiente.
- **Autorización y `locale`:** la tool filtra solo con `authorize_agent`, M4 también llama `authorize_subject`; `locale` es argumento del flow (el e2e fija `"es"`): un especialista listado puede rechazarse con `not_eligible` (falla seguro).

## 20. Conector de datasets reales para las evals — abierto
Encontrado al diseñar la evaluación por agente (`2026-09-30-evaluacion-y-metricas-design.md`, ADR 0020). En la rama `feat/eval-metrics` se numeró #13 y luego #19; al integrarla con la rama principal, donde #13 era el servidor arrancable y #19 la transferencia entre agentes, pasó a #20. Las evals podrán correr sobre casos de un dataset real, pero la fuente `dataset` está diseñada y desactivada. Falta decidir:
- **Origen:** BD transaccional o warehouse, y quién es el dueño de los datos.
- **Autorización:** el ADR firmado por el dueño que enmiende la regla 5 de CLAUDE.md y el registry §8.
- **Protección:** paso por las vistas tokenizadas de M7 (ADR 0008), sin PII hacia modelos, logs ni eventos.
- **Etiquetas:** cuándo el resultado histórico (por ejemplo, lo que hizo el asesor humano) sirve como referencia.

La fuente `dataset` es `DatasetScenario` (`source: dataset`, `agent_core/registry/suite.py`); el servicio la rechaza con `dataset_source_disabled` (T-EVAL-12). Los datos reales no viven en el repo ni en el registry; el registry solo guarda `dataset_id` y `dataset_hash`. Por qué es media: no bloquea la fase 1, porque la suite `scripted` es la base obligatoria del gate.
