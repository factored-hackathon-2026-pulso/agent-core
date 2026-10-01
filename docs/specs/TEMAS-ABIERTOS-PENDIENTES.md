# Temas abiertos — Motor de decisión (spec 2026-09-28)

- Estado: **9 de 9 temas originales resueltos; #10 (`read` construido), #11, #12, #14, #15 y #16 cerrados el 2026-09-30 (decisiones abajo); #13 resuelto salvo las piezas reales de las unidades 3, 6 y 7 (2026-09-30); #18 decidido el 2026-09-30 (construcción pendiente); sigue abierto #17 (medio) y se abre #19 (medio, conector de datasets reales para las evals, ADR 0020).** Reemplaza la versión anterior de este documento, cuyo contenido se descartó por basarse en hallazgos incorrectos.
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
| 17 | Clase `write_draft` y dependencias del constructor sobre el registry | Media | **Abierto** |
| 18 | Auditoría de las lecturas de datos de clientes del administrador | Media | **Decidido (2026-09-30); construcción pendiente** |
| 19 | Conector de datasets reales para las evals de los agentes | Media | **Abierto** |

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
- **Consumo en la demo:** Phoenix para latencia y costo (ya hay spans OTel `agentcore.*`) y una vista SQL con las métricas de negocio (contención, resolución segura, `abandoned`, aclaraciones por run, modo degradado). No se construye un dashboard propio.
- **Comparación entre releases:** no hay una regla nueva. El gate del registry (guardarraíles y margen de ruido, registry §6) decide las promociones; la analítica solo informa.
- **Retención:** sin purga en el MVP (las tablas son inmutables y pequeñas); se revisa en la fase 2 junto con la retención del registry (#16).
Actualización (2026-09-30): el DSL de métricas por agente, el catálogo de eventos y la regla de que no hay acción automática en producción se decidieron en el ADR 0020; este tema conserva el cálculo, la visualización, las alertas y la retención.
Pendiente de construcción: las vistas SQL y la conexión a Phoenix; no bloquean la fase 1 porque los eventos ya capturan los datos.

## 12. Política del contexto conversacional — resuelto
**Decidido el 2026-09-30** (ratifica lo que ya implementa `EngineConfig.recent_turns`; ver m04 §15 y m05 §3.2):
- **Tamaño:** `n` fijo de 6 turnos, por despliegue; se mide en turnos, no en tokens, y no es configurable por agente.
- **Conversaciones largas:** se trunca a las últimas `n` entradas; no hay resumen en el MVP (un resumen es texto generado por un modelo y arrastra la duda de `untrusted_text` y del replay).
- **Contenido:** mensaje del cliente y respuesta del agente, en vista `model`, sin borradores rechazados. Los hechos siguen saliendo solo de acciones verificadas, nunca del historial.
- **Entre runs:** nada; un asunto retomado es un run nuevo con contexto limpio.

## 13. Servidor arrancable — resuelto salvo las piezas reales
**Decidido el 2026-09-30:** la demo corre con un modelo real y el resto como dobles etiquetados. Spec: `docs/superpowers/specs/2026-09-30-servidor-arrancable-design.md`; plan: `docs/superpowers/plans/2026-09-30-servidor-arrancable.md`.
- **Construido:** `agentcore serve` (`agent_core/composition/serve.py`, `serve_ports.py`, `build_engine` en `engine.py`). Reales: Postgres, gateway de LLM, JEV por HTTP (`AGENTCORE_JEV_API_KEY`), claves HMAC/cifrado (`EnvKeyProvider`) y claves públicas de identidad (`--identity-keys`, `agent_core/adapters/identity_keys.py`). `uvicorn` declarado en `pyproject.toml`.
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
- **Topes del constructor autónomo (decidido el 2026-09-30):** 10 propuestas por día y 20 evaluaciones por propuesta. Sigue sin fijar el **monto del tope de costo por propuesta** (USD); se fija al activar el constructor en modo `task`, que no se activa en la demo. Aún no hay código que aplique los topes.

## 17. `write_draft` y dependencias del constructor — abierto
Registry §18: clase de riesgo `write_draft` (G0-23, AG-02, ruta de M3), regla G0-25 del gateway (el prompt del nodo `agent` debe ser `structured: prompted`), adaptador de `ToolExecutor` del constructor con su propia credencial, `readback_by` de borradores y catálogo de campos y plantillas de handoff como entidades versionadas. Nada de esto está construido; el constructor solo puede correr de solo lectura.

## 18. Auditoría de las lecturas de datos de clientes del administrador — decidido; construcción pendiente
El administrador puede leer runs, transcripts y campos de clientes (ADR 0006, enmienda 2026-09-30), pero una lectura autorizada no deja hoy ningún evento: solo se registran los rechazos (`access_denied`).
**Decidido el 2026-09-30:**
- **Evento `privileged_read`** con principal, subject, propósito y `trace_id`, **sin el contenido leído**. Es un cambio de M0 (evento nuevo, versión menor: regenerar `contracts/` y avisar) y de M9 (emitirlo en cada lectura autorizada del administrador).
- **Dónde se anota:** en la cadena de eventos del run cuando la lectura es de un run; cuando no lo es, en un log de auditoría propio.
- **Motivo de la lectura obligatorio.**
**Pendiente de construcción:** el esquema del evento y su versión, la forma exacta del log de auditoría propio (tabla, retención y quién lo consulta) y el parámetro por el que M9 recibe el motivo. Hay que cerrar esos tres puntos antes de implementar.

## 19. Conector de datasets reales para las evals — abierto
Encontrado al diseñar la evaluación por agente (`2026-09-30-evaluacion-y-metricas-design.md`, ADR 0020). Las evals podrán correr sobre casos de un dataset real, pero la fuente `dataset` está diseñada y desactivada. Falta decidir:
- **Origen:** BD transaccional o warehouse, y quién es el dueño de los datos.
- **Autorización:** el ADR firmado por el dueño que enmiende la regla 5 de CLAUDE.md y el registry §8.
- **Protección:** paso por las vistas tokenizadas de M7 (ADR 0008), sin PII hacia modelos, logs ni eventos.
- **Etiquetas:** cuándo el resultado histórico (por ejemplo, lo que hizo el asesor humano) sirve como referencia.

Los datos reales no viven en el repo ni en el registry; el registry solo guarda `dataset_id` y `dataset_hash`. Por qué es media: no bloquea la fase 1, porque la suite `scripted` es la base obligatoria del gate.
