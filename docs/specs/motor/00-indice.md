# Motor de decisión — índice de módulos

- Estado: **borrador para revisión** (2026-09-28)
- Deriva de: `../2026-09-28-motor-de-decision-design.md` (rev. 12), en adelante "spec general", y de los ADR 0001–0015.
- Repo: `agent-core`
- Autor: Juan Zapata, con Claude
- Nombres: genéricos a propósito, porque el nombre de la app puede cambiar. Repos `agent-core` y `agent-registry`, paquete Python `agent_core`, paquete de telemetría `agent-telemetry`, CLI `agentcore` y prefijo OTel `agentcore.*`.

## 1. Para qué existe esta carpeta

La spec general describe el motor completo en un solo documento. Aquí se parte en **13 módulos** con fronteras explícitas, para que cada uno se pueda:

- **desarrollar** sin conocer los internos de los demás (solo sus interfaces y los tipos de M0);
- **probar** de forma aislada, con dobles en memoria de los puertos;
- **evaluar** con métricas propias que la unidad 6 consume;
- **iterar** cambiando su spec sin tocar las demás, salvo que cambie una interfaz.

**Regla de mantenimiento (propuesta):**

- A partir de ahora, el comportamiento de un módulo se cambia en **su** spec.
- Un cambio de interfaz entre módulos se hace primero en M0 (tipos y puertos) y después en los módulos afectados.
- La spec general queda como visión integrada y recibe una línea de changelog por cada cambio. Si un módulo y la spec general se contradicen, es un bug de documentación que se corrige en el mismo PR.

## 2. Mapa de módulos

| # | Módulo | Archivo | Paquete | Fase | Secciones de origen | ADRs |
|---|---|---|---|---|---|---|
| M0 | Dominio y contratos | `m00-dominio-y-contratos.md` | `agent_core.domain`, `agent_core.ports` | 1 | §2, §5 (esquemas de nodos), §8 (estado), §10 (códigos) | 0001, 0002, 0006, 0007, 0008 |
| M1 | Esquema de flows y validación estática | `m01-validacion-estatica.md` | `agent_core.flows` | 1 y 5 | §5, §6.1, §6.2 (chequeos por agente) | 0004, 0007, 0009, 0011, 0016 |
| M2 | Intérprete de nodos | `m02-interprete.md` | `agent_core.interpreter` | 1 | §4.7, §5 | 0004, 0010, 0011 |
| M3 | Protocolo de escritura | `m03-acciones.md` | `agent_core.actions` | 1 | §8.2, §4 (invalidación) | 0007 |
| M4 | Ciclo del turno | `m04-ciclo-del-turno.md` | `agent_core.turn` | 2 | §4.1, §4.4–4.6, §4.8–4.10 | 0004, 0007 |
| M5 | DecisionModel y Understand | `m05-decision-model.md` | `agent_core.decision` | 4 | §4.4, §7 | 0005 |
| M6 | Guardas de entrada | `m06-guardas.md` | `agent_core.guards` | 4 | §4.3, §4.5.6 | 0012 |
| M7 | Vistas y tokenización | `m07-vistas-y-tokenizacion.md` | `agent_core.views` | 3 | §8.1, §8.1.1 | 0008 |
| M8 | Validador de respuesta | `m08-validador-de-respuesta.md` | `agent_core.response` | 4 | §8.3, §5 (`respond`) | 0011, 0012 |
| M9 | Acceso y API | `m09-acceso-y-api.md` | `agent_core.api` | 3 | §3, §4.1–4.2, §4.7 (step-up) | 0006, 0010 |
| M10 | Escalamiento y handoff | `m10-escalamiento-y-handoff.md` | `agent_core.handoff` | 2 | §9 | 0013 |
| M11 | Auditoría, transcript y replay | `m11-auditoria-transcript-replay.md` | `agent_core.audit` | 2 y 5 | §8.4, §11 | 0003, 0008 |
| M12 | Conocimiento | `m12-conocimiento.md` | `agent_core.knowledge` | por decidir | ADR 0015 | 0015 |

**Fuera de esta tabla:** el registry (unidad 2) tiene su propia spec, `../2026-09-29-registry-design.md`. Añade `EntityKind.knowledge_snapshot` y `Release.knowledge_snapshot` a M0, y M1 le aporta las funciones puras de validación y `pin_release` (ver §15 de esa spec).

## 3. Dependencias

Un módulo solo importa `agent_core.domain`, `agent_core.ports` y la **interfaz pública** (`__init__.py`) de los módulos de su columna "usa". Se hace cumplir en CI con `import-linter`.

| Módulo | Usa | Lo usan |
|---|---|---|
| M0 | — | todos |
| M1 | M0 | M2 (`derive_claims`, `release_view`, `JSONLOGIC_OPS`, rutas y plantillas), unidad 2 (gate), CI de `agent-registry` |
| M7 | M0 | M2, M5, M8, M10, M11 |
| M6 | M0 | M4, M8 |
| M5 | M0, M7 | M2 (`decide`), M4 (Understand) |
| M3 | M0 | M2, M4 |
| M8 | M0, M6, M7 | M2 (`respond`), M10 (resumen) |
| M2 | M0, M1 (análisis compartido), M3, M5, M7, M8 | M4 |
| M10 | M0, M7 | M4, M9 |
| M11 | M0, M7 | M4, M9 |
| M4 | M0, M2, M3, M5, M6, M10, M11 | M9 |
| M9 | M0, M4, M10, M11 | apps |
| M12 | M0, M7 | M2 (nodo `knowledge`), M8 |

**Raíces de composición (2026-09-29):** `agent_core.cli` y `agent_core.composition` no son módulos: cablean M2–M11 con adaptadores y pueden importarlo todo; ningún módulo puede importarlas (`.importlinter` las prohíbe en todos los contratos y `test_lint_rules` lo exige). `composition` contiene `build_turn_engine`, el `RuntimeFactory` real sobre M7 y los adaptadores M5 → `DecisionPort` y M8 → `ResponderPort`.

Orden de construcción sin dependencias rotas: M0 → M1 → M7 → M3 → M2 → M11 → M10 → M4 → M9 → M6 → M5 → M8 → M12.

## 4. Puertos (M0) y sus dobles de prueba

Las unidades 2–7 aún no existen. El motor habla con ellas solo por estos puertos; en la demo y en las pruebas se usan dobles en `testing/fakes/`. Cada doble pasa la misma **suite de contrato** que después correrá el adaptador real.

| Puerto | Dueño real | Doble de la demo | Lo entrega | Lo usan |
|---|---|---|---|---|
| `Clock` | M0 | `FakeClock` (avanzable) / `SystemClock` | M0 | todos |
| `IdSource` | M0 | `FakeIds` (secuencial, sembrable) / `SystemIds` | M0 | todos los que crean IDs |
| `RegistryPort` | unidad 2 | `InMemoryRegistry` (objetos Python en M0; carga desde `agent-registry/` en M1) | M0 (+ M1) | M1, M2, M4 |
| `ToolExecutor` | unidad 3 | `FakeToolExecutor` (guionable: `ok`, `error`, `uncertain`, `step_up_required`…) | M0 | M2, M3 |
| `AuthzPort` | unidad 3 | `TableAuthz` (tabla de §4.2) | M9 | M9, M7, M10, M11 |
| `IdentityVerifier` | servicio de identidad | `TestIdentityIssuer` (claves de prueba, etiquetado) | M9 | M9 |
| `UnitOfWork` | M4 (Postgres) | `InMemoryUoW` con inyección de fallas | M0 | M3, M4, M9, M10 |
| `AuditSink` | unidad 4 | `InMemoryAuditSink` | M0 | M9, M11 |
| `Outbox` | unidad 4 | `InMemoryOutbox` | M0 | unidad 4 |
| `LLMGateway` | unidad 5: `OpenAICompatGateway` (`agent_core.adapters.llm`) | `ScriptedGateway` | M8 | M8, M5 (`llm_structured`) |
| `AgentPort` | unidad 5: `LLMAgentPort` (sobre `LLMGateway`, `prompted`) | `ScriptedAgent` | M2 | M2 (nodo `agent`) |
| `TranscriptStore` | unidad 7 | `InMemoryTranscript` | M11 | M11, M5 (`recent_turns`) |
| `KnowledgeSource` | unidad 7 | `FileKnowledgeSource` | M12 (provisional, tema #10) | M12 |
| `KeyProvider` | gestor de secretos | `FakeKeyProvider` / `EnvKeyProvider` (etiquetado) | M0 | M7 |
| `CostCounters` | unidad 5 | `InMemoryCostCounters` | M4 (`UnitOfWork.add_usage`, ADR 0016 punto 4) | M9 |

`DecisionProvider` no es un puerto de M0: es la interfaz de adaptadores internos de M5 (`ScriptedProvider` lo entrega M5).

Todo JSON que entra al núcleo se lee con `agent_core.domain.loads` (números con decimales como `Decimal`), y toda canonización para hashes y huellas usa `canonical_bytes` de M0 (JCS).

## 5. Dueños del estado del run

`RunState` es un solo registro (§8), pero cada parte tiene **un** módulo que la escribe. Los demás la leen.

| Parte de `RunState` | Escribe |
|---|---|
| `run_id`, `session_id`, `release`, `agent`, `principal`, `on_behalf_of`, `subject`, `mode` | M4 (al crear el run; en cada turno refresca `principal.auth` con el de la credencial presentada si la clave coincide, ADR 0010) |
| `locale` | M4, con la decisión de M6 |
| `status`, `outcome`, `created_at`, `last_activity_at`, `closed_at`, `turn_count`, `handoff_ref` | M4 (M10 prepara el cierre por escalamiento; M4 lo aplica) |
| `awaiting`, `awaiting_node_id`, `pending_offer` | M4 (con el `Stop` de M2) |
| `state_version` | `UnitOfWork` (`save_run`) |
| `pending_intents`, `clarifications_used`, `degraded_turns` | M4 |
| `repair_turns_used` | M2 (collect) / M4 (confirm; lee) |
| `active_flow`, `slots`, `facts`, `decisions`, `node_attempts`, `budgets_used`, `open_questions` | M2 |
| `actions` | M3 |
| `token_map` | M7 |
| `pages` (si se aprueba M12) | M12 |

`open_questions`: ningún módulo define todavía cómo se llena; queda como abierto de M2/M10.

## 6. Eventos y su emisor

M0 define el esquema de cada evento; M11 los encadena y persiste. El módulo emisor es el único que los construye.

| Evento | Emisor |
|---|---|
| `run_started`, `turn_started`, `command_emitted`, `expiry_evaluated`, `turn_completed`, `run_closed` | M4 |
| `node_entered`, `rule_evaluated`, `tool_called` (lectura y `compute`), `agent_step`, `step_up_requested` | M2 |
| `decision_made` | M5 |
| `action_confirmed`, `action_cancelled`, `action_dispatched`, `tool_called` (escritura), `action_verified` | M3 |
| `response_failed` | M8 (cuando `respond(generate)` escala) |
| `response_emitted` | M8 desde `generate` (con la huella que calcula M11); M2 desde `respond(template_ref)` y desde el modo degradado (`kind=template`, `llm=None`; D6, 2026-09-30) |
| `injection_flagged` | M6 (M4 lo registra) |
| `access_denied` | M9 (y M2 con motivo `tool_denied`) |
| `escalated`, `handoff_created` (outbox), `handoff_resolved` | M10 |

`run_closed` lo emite **solo M4**, también en el cierre por escalamiento. Los payloads de cada evento están en M0 §2.10.

## 7. Fases de construcción

| Fase | Fecha | Módulos | Resultado demostrable |
|---|---|---|---|
| 1 · Esqueleto | 30/09 | M0, M1 (carga y reglas G0-01 a G0-06), M2, M3 | `disputa-cargo` (flow conversacional) corre con un arnés que maneja M2 con `Resume` guionados, sin M4 y con dobles, incluido `uncertain → verify` y la recuperación tras una caída |
| 2 · Conversación | 01/10 | M4, M10, M11 (cadena y transcript) | turnos, `confirm` por botón y por texto, interrupción de fraude, escalamiento con `410` |
| 3 · Seguridad | 01–02/10 | M9, M7 | IDOR, credenciales, vistas, renderer y huellas |
| 4 · Inteligencia | 02/10 | M5 (classifier primero, JEV después), M6, M8 | Understand calibrado, idioma ES/PT, validador numérico |
| 5 · Cierre | 02/10 | M1 (reglas G0-07 a G0-16), M11 (replay) | replay `fixture` en CI; replay `audit` si alcanza |
| — | por decidir | M12 | depende del tema #10 |

**Recortes en orden** si falta tiempo (no tocan los invariantes del reto):

1. replay en modo `audit`;
2. rotación de `kid` (queda una sola clave);
3. histéresis de idioma (quedan `short` y `undetermined`);
4. modo `navigate` de M12.

## 8. Convenciones comunes

**Plantilla de cada spec de módulo:**

1. Propósito y límites (qué hace, qué no hace).
2. Interfaz pública (firmas en Python con tipos de M0).
3. Comportamiento.
4. Invariantes.
5. Fallas.
6. Eventos que emite.
7. Pruebas (IDs `T-Mx-NN`, con la referencia a §13 de la spec general).
8. Evaluación (métricas que la unidad 6 lee).
9. Puntos de iteración (qué se puede cambiar sin romper la interfaz).
10. Definición de terminado.
11. Abiertos.

**Pruebas:**

- `tests/mXX/` por módulo, `pytest`, sin red ni Postgres. Solo M4, M9 y M11 tienen además pruebas de integración con Postgres (`tests/integration/`, `docker-compose`).
- Cada doble de puerto tiene su suite de contrato en `tests/contracts/`.
- Los fixtures usan solo datos sintéticos del catálogo de prueba (§13.2).

**Definición de terminado común** (se suma a la de cada módulo):

- interfaz pública exportada y con tipos;
- todas las pruebas `T-Mx-*` en verde;
- `import-linter` en verde;
- eventos que emite validados contra el esquema de M0;
- sin TODO sin issue.

## 9. Trazabilidad desde la spec general

| Sección | Módulo |
|---|---|
| §0 Corte MVP | este índice (§7) |
| §1 Propósito | este índice |
| §2 Conceptos | M0 |
| §3 API | M9 |
| §4.1 Cargar | M9 (credenciales, límites), M4 (estado, release, revocación, recuperación, abandono) |
| §4.2 Autorizar | M9 |
| §4.3 Guardas e idioma | M6 |
| §4.4 Understand | M5 (modelo), M4 (uso del resultado) |
| §4.5 Manejadores globales | M4 |
| §4.6 Intenciones pendientes | M4 |
| §4.7 Avanzar | M2 |
| §4.8 Responder | M8 (validador), M7 (renderer), M11 (transcript) |
| §4.9 Persistir | M4 |
| §4.10 Barrido | M4 |
| Invalidación de acciones | M3 (mecanismo), M4 (disparadores) |
| Escalamiento | M10 |
| §5 Catálogo de nodos | M1 (esquema), M2 (ejecución), M3 (`confirm`, escritura, `verify`) |
| §6.1 Validación del flow | M1 |
| §6.2 Gate de release | unidad 2; M1 aporta los chequeos por agente |
| §7 DecisionModel | M5 |
| §8 Estado | M0 |
| §8.1 Vistas | M7 |
| §8.2 Escritura | M3 |
| §8.3 Validador | M8 |
| §8.4 Transcript | M11 |
| §9 Handoff | M10 |
| §10 Fallas | repartidas en la sección 5 de cada módulo |
| §11 Observabilidad y replay | M11 |
| §12 Evaluación | repartida en la sección 8 de cada módulo |
| §13 Pruebas | repartidas en la sección 7 de cada módulo |
| §14 Dependencias | este índice (§4) y M0 |
| §15 Riesgos | este índice (§10) y "Abiertos" de cada módulo |

## 10. Temas abiertos por módulo

| Tema | Módulo | Estado |
|---|---|---|
| #10 Integración de ADR 0015 (conocimiento) | M12 (propuesta de integración), M1, M8 | abierto; M12 trae una propuesta |
| Formato numérico por país: el `locale` del run es `es`/`pt`, pero ADR 0011 parsea por `es-CO`/`es-MX`/`es-AR`/`pt` | M8 | **resuelto** en M8 rev. 2 (parcial): `number_format` es dato opcional del contexto producido fuera de M8; sin él, solo lecturas inequívocas |
| Detector de injection: la spec dice "marca y cuenta" pero no define el método | M6 | **resuelto** en M6 rev. 2: reglas regex/frase versionadas en la release, texto normalizado; reemplazable por un clasificador detrás de `scan_injection` |
| Generalización de `pii_quasi` sin definir (qué hace con la fecha de nacimiento o el código postal) | M7 | **resuelto** en M7 rev. 2: regla como dato (`QuasiRule`: `drop`, `age_bucket`), por defecto `drop` |
| Formato del token de PII sin definir | M7 | **resuelto** en M7 rev. 2: `⟦tag:n⟧`, contador por tag dentro del run |
| `request_summary` del handoff: generado o por plantilla | M10 | **resuelto** en M10 rev. 2: plantilla determinista en el MVP; generado (vía M8) queda para producción |
| Prueba de humo de JEV (bloqueante antes del miércoles 30/09) | M5 | **resuelto (2026-09-29), con salvedades:** contrato real (P0b) cerrado; humo con `jev-1.13.0`: `command` 96% ES / 94% PT, `flow` 100%, p50/p95 375/453 ms, 0 errores, ≈ 0.00002 USD por llamada (n pequeño, datos sintéticos). Decisión: `choice`/`noul` para Understand (`command`/`flow`/`interrupt`), slots por `llm_structured` en una segunda llamada solo con `start_flow` (ADR 0005 **enmendado** el 2026-09-29; implementada en `UnderstandService` (`decision/understand.py`; solo corre si el agente declara `slots_model`)); `jev-1.13.0` fijado; JEV no entra como segunda opinión de idioma. Falta calibrar con datos reales (P9). Informe: `docs/informes/2026-09-29-m5-jev-humo.md` |
| Mínimo de muestra PT para calibrar | M5 | pendiente (lo fija la unidad 6); `calibrate` lo exige como `min_samples` sin defecto |
| Regla de umbral por recall, formato del clasificador, `rule.config`, comando del informe, `EventScope`, `UnderstandContext` | M5 | **resuelto** en M5 rev. 2 (2026-09-29) |
| Adaptador `DecisionOutput → DecisionResult` (`DecisionPort` de M2) | M2, M4 | **nuevo**, fuera de M5 |
| Revisión de M0 (rev. 2): nodos en M0, `IdSource`, `Decimal`/JCS, `RefSpec`, vistas en M7, `grantee`, lease de turno, `action_cancelled`, `Awaiting.input` | M0, M1, M2, M3, M4, M5, M9, M10 | **resuelto** en M0 rev. 2 (2026-09-28) |
| Quién llena `open_questions` del `RunState` | M2, M10 | **nuevo**, detectado en la revisión de M0 |
| Formato de la credencial (`raw_credential`) | M9 | **Resuelto (2026-09-29)**: JWS compacto Ed25519 con `kid` (m09 §3.8); no cambia el puerto |
| G0-15 reclamada por el gateway (ADR 0016) y por M12 | M1, M12 | **resuelto** en M1 rev. 2: G0-15 = `model_profile` de prompts, G0-16 = flows task sin nodos que esperan; M12 propone G0-17…G0-21 |
| Fase 1 "en modo task" con un flow conversacional | M1, M2, M4 | **resuelto** en M1 rev. 2: arnés sobre M2 en la fase 1; G0-16 impide nodos que esperan en flows task |
| Agentes internos (copiloto del asesor y constructor): nodo `agent` de solo lectura, clase `write_draft`, G0-22, G0-23, AG-02, identidad acotada del constructor | M0, M1, M2, M3, M9; registry (§18) | **nodo `agent` implementado** (M0, M1 con G0-22, M2; ADR 0019). **Pendiente:** clase `write_draft` (G0-23, AG-02, ruta de M3), el registry. El adaptador real de `AgentPort` (`LLMAgentPort`) quedó implementado en la unidad 5. `AuthzPort` ya tiene su prueba de contrato |
| Sintaxis de plantilla y `Template.reads` sin definir | M1, M2, M8 | **resuelto** en M1 rev. 2: `{{ ruta }}` y `reads` derivado al cargar |
