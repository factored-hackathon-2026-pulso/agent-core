# Spec — Motor de decisión (unidad 1: orquestación + JEV)

- Estado: **borrador para revisión final**. Todos los hallazgos de la revisión externa quedaron resueltos (§16). Los temas de la auto-revisión posterior se siguen en `TEMAS-ABIERTOS-PENDIENTES.md`.
- Fecha: 2026-09-28
  - rev. 2: resoluciones C1, C2 y C7.
  - rev. 3: resoluciones de severidad alta y corte MVP.
  - rev. 4: resoluciones de severidad media.
  - rev. 5: resoluciones de severidad baja.
  - rev. 6: reclamos de éxito en `respond` (`claims`) y definición de `respond` seguro (auto-revisión #1).
  - rev. 7: credenciales rechazadas antes del turno, `principal_mismatch` y límites después de la firma (auto-revisión #2).
  - rev. 8: replay en dos modos, `fixture` y `audit` (auto-revisión #3).
  - rev. 9: detector de idioma (lingua-py) y reglas de decisión de `locale` (auto-revisión #5).
  - rev. 10: tope de `unclear` en `confirm`, reentrada idempotente y precedencia de manejadores con un `confirm` pendiente (auto-revisión #6).
  - rev. 11: huellas con clave (HMAC-SHA256 + JCS + `kid`) en la vista `audit` y el transcript (auto-revisión #4).
  - rev. 12: outcomes declarables por modo, `abandoned`/`escalated` solo del motor; renumeración del ADR de conocimiento a 0015 (auto-revisión #7, #8, #9).
  - rev. 14: métricas de eficiencia y tiempos en el log de auditoría: `latency_ms` en `tool_called`, `llm` en `response_emitted`, evento `turn_completed`, `Clock.monotonic_ns()` y campos de medición excluidos del replay (M0 rev. 3; §11, §12).
  - rev. 15: revisión de M1 (`specs/motor/m01-validacion-estatica.md` rev. 2): G0-04 por grafo sin nodos que esperan; G0-05 con paso por `confirm.yes`, `verify` enlazado por estructura, tools de escritura solo por `action_from` y reclamos también desde el `confirm`; reclamos derivados conservadores (toda tool, `decide`, plantilla de respaldo, `end.output_map`); reglas 15 (`model_profile` de prompts, ADR 0016) y 16 (flows task sin nodos que esperan); sintaxis de plantilla `{{ ruta }}` con `reads` derivado. El detalle manda en M1.
  - rev. 13: revisión de M0 (`specs/motor/m00-dominio-y-contratos.md` rev. 2): `ActionState` con `uncertain`/`denied`; `Awaiting.input`; `on_behalf_of.grantee` y `403 delegation_mismatch`/`agent_forbidden`; `execute` devuelve solo `result_full` (las vistas las calcula el núcleo); IDs por `IdSource` inyectado; `Decimal` y JCS en M0; referencias de autoría (`RefSpec`) frente a runtime (exactas). El detalle de tipos manda en M0.
- Repo: `agent-core`
- Autor: Juan Zapata, con Claude
- ADRs:
  - 0003 (enmendado), 0004 (enmendado), 0005 (enmendado), 0006 (enmendado), 0007 (enmendado);
  - 0008, 0009, 0010, 0011;
  - 0012 (enmendado);
  - 0013, 0014;
  - 0015 (conocimiento, antes numerado 0009): aceptado pero **aún no integrado en esta spec** (tema #10 de `TEMAS-ABIERTOS-PENDIENTES.md`).
- Depende de: ADR 0001–0003
- La usan:
  - unidad 2 (entidades, versionado y gate de release);
  - unidad 3 (tools y gobernanza);
  - unidad 4 (auditoría y eventos salientes);
  - unidad 6 (evaluación);
  - unidad 7 (transcripts).

## 0. Corte MVP (construcción del 30/09 al 02/10)

**Entra en el intérprete de la demo:**
- **Nodos:** `decide`, `rule`, `collect`, `tool`, `confirm`, `verify`, `respond`, `escalate` y `end`.
- **Flujo de conversación:**
  - Un solo flow activo con `pending_intents` ordenadas por prioridad (§4.6).
  - Interrupciones declaradas en la release; la demo trae una: fraude/robo (§4.5).
  - Escalamiento como evento saliente que cierra el run para el bot (§9, ADR 0013).
  - `outcome: abandoned` por inactividad.
  - Idioma de respuesta por turno: ES o PT (§4.3).
- **Autorización e identidad:**
  - Autorización del subject y vinculación de parámetros (§4.2).
  - Niveles de autenticación y step-up **simulado** y etiquetado (§4.7, ADR 0010).
- **Datos y escrituras:**
  - Vistas de datos y tokenización (§8.1).
  - Acción congelada, outbox, `idempotency_key = action_id`, resultados `ok`/`denied`/`uncertain` y readback por clave (§8.2).
- **Políticas y validación:**
  - `policy` protegida referenciada desde `rule` (ADR 0009).
  - Tools `compute` (incluida `seleccionar`) y validador numérico por locale (ADR 0011).
  - `untrusted_text` y modo degradado ante alerta de injection.
- **Releases:** `@prod` obligatorio para `customer` y `advisor`; una release revocada lleva a escalamiento.
- **Registro y replay:**
  - Transcript store mínimo con lectura renderizada por lector (§8.4).
  - Cadena de hash por run, `Clock` inyectado y guardas registradas (§11).
  - Replay en modo `fixture` (CI, recálculo completo) y en modo `audit` (runs reales, integridad y transiciones) (§11).
  - Atributos de reporte en `run_started` (§11).
- **API:** `Idempotency-Key` en `POST /v1/runs` y límites de tasa y costo por principal (§3).
- **Modelos de decisión:** `DecisionModel` con cadena JEV → classifier, calibración isotónica por idioma y `calibrated_fields` (§7).

**Queda como diseño de producción** (documentado, no se construye):
- Pila de flows y `subflow`.
- Nodo `agent`.
- `await_approval` (ADR 0014).
- `llm_structured` calibrado por auto-consistencia (en la demo solo se compara como baseline, sin umbral).
- Buzón para mensajes en ráfaga.
- Devolución del caso del asesor al bot.
- Worker asíncrono de escrituras.
- Step-up real con OTP.
- Servicio externo de elegibilidad de crédito.
- Streaming de texto generado.
- Almacén cifrado de entradas en vista `full` para recalcular por completo runs reales en el replay (§11).
- Clave de huellas por subject con borrado criptográfico (§8.1.1).

Ningún recorte toca los invariantes que exige el reto: permisos en la capa de tools, confirm → act → verify, handoff estructurado, políticas fuera del modelo y auditoría determinista.

**Estimación:** 1.500–2.000 LOC para el intérprete con sus pruebas. El plan de implementación prioriza el camino confirm → act → verify → handoff de punta a punta.

## 1. Propósito y alcance

El motor ejecuta **agentes** descritos como datos versionados. Implementa el ciclo Understand → Decide → Act → Verify → Escalate y no conoce el dominio: sirve igual a un agente de atención al cliente, al copiloto del asesor, al constructor de agentes o al constructor de tools. El núcleo no conoce el concepto de "caso": un caso de negocio es un `subject` más.

**Dentro del alcance:**
- Modelo de ejecución y catálogo de nodos.
- Contrato `DecisionModel`.
- Estado del run y vistas de datos.
- Protocolo de escritura.
- `HandoffPacket` y evento de escalamiento.
- Validación estática de flows.
- Manejo de fallas.
- Eventos que emite el motor.

**Fuera del alcance (otras unidades u otros sistemas):**
- **Unidad 2:** almacenamiento y resolución de versiones, políticas protegidas, revocación y gate de release.
- **Unidad 3:** contrato de tools, políticas de acceso, clasificación de campos y tokenización.
- **Unidad 4:** esquema completo del log de auditoría y entrega de eventos salientes.
- **Unidad 5:** gateway LLM y presupuestos.
- **Unidad 6:** harness, referencias de evaluación y métricas.
- **Unidad 7:** memoria, transcripts y conocimiento.
- **Otros sistemas:**
  - Generación de datos de evaluación (incluidos los sintéticos en PT).
  - La plataforma del asesor después del escalamiento.

**No objetivos:**
- Paralelismo dentro de un turno.
- Agentes que conversan entre sí.
- Flows que modifican flows en runtime.
- Esperas durables de días (en producción se resolvería con Temporal debajo del motor; ver ADR 0004).

## 2. Conceptos

| Concepto | Definición |
|---|---|
| **Principal** | Quien invoca: `{type: customer\|advisor\|service\|builder, id?, roles[], scopes[], attrs{}, auth: {level: anonymous\|session\|step_up, at}}`. Lo emite y firma un servicio de identidad externo; el motor solo valida firma y vigencia. `attrs` puede traer atributos de reporte (§11). |
| **Delegación** | `on_behalf_of: {subject: {kind, ref}, grant_ref, grantee: {type, id}, scopes[], exp}`, firmada por el emisor de asignaciones y atada al asesor `grantee` (ADR 0006). |
| **Session** | Canal conversacional entre un principal y el núcleo. Contiene un run conversacional. |
| **Run** | Una ejecución de un agente sobre un `subject {kind, ref}` autorizado. Es la unidad de estado, de auditoría y de cadena de hash. Un asunto retomado otro día es un **run nuevo sobre el mismo subject**; la continuidad la aporta la memoria (unidad 7). |
| **Turn** | Un mensaje del principal y la respuesta del motor dentro de un run conversacional. |
| **Agente** | Entidad versionada `{id, version, mode: conversational\|task, entry_flow: flow@v, invocable_by: [principal.type], min_auth_level, subject_kinds: [kind], supported_locales: [es, pt], default_locale, tools_allowed[], budgets, inactivity_ttl, understand: decision_model@v?, clarify_template, max_clarifications, on_clarify_exhausted: end\|escalate, default_target_queue}`. |
| **Flow** | Grafo dirigido de nodos tipados, con `priority` declarada. Es un dato versionado, no código. |
| **Interrupción** | Entrada de la release `{id, priority, action: escalate(queue, priority) \| start_flow(flow)}` que se antepone al flow activo (ADR 0004). |
| **Policy** | Regla de negocio versionada y protegida, con dueño y aprobación humana obligatoria (ADR 0009). |
| **DecisionModel** | Contrato para cualquier componente que elige un valor tipado con probabilidad calibrada. JEV es un proveedor. |
| **Release** | Versión compuesta del sistema de decisión, fijada en cada run al iniciar (unidad 2). Estado `active` o `revoked`. Incluye las interrupciones y la configuración de detección de idioma. |
| **Detección de idioma** | Entidad versionada de la release `{detector: lingua@<versión exacta>, candidates: [es, pt, <no soportados>], min_letters, min_letters_unsupported, thresholds_from: <calib_run_id>}` (§4.3). |
| **Referencias versionadas** | En autoría (registro), una referencia puede usar un rango semver (`tool@^1`). En runtime todas las referencias son **exactas**: la release las resuelve y fija al publicarse, y el motor nunca resuelve rangos. |
| **Hecho** | Valor devuelto por una tool (incluidas las `compute`), por la identidad o por el conocimiento, con su procedencia. Es lo único sobre lo que razonan las reglas y el validador. Se guarda en `facts.<nombre>`. |
| **Decisión** | Salida registrada de un `decide`, guardada en `decisions.<nombre>`. Las reglas no la leen directamente: para usar lo elegido, una tool `compute` lo materializa como hecho con procedencia (§5). |
| **Slot** | Valor que afirma el principal. Estado `claimed` o `validated`; un slot nunca se convierte en hecho por sí solo. |
| **Acción** | Escritura propuesta en un `confirm`, con argumentos congelados y un `action_id` estable (ADR 0007). |
| **Reclamo de éxito** | Afirmación, en un `respond`, de que una acción tuvo efecto. Se declara con `claims` o se deriva de los hechos que el `respond` lee (§5, §6.1). |
| **Vistas de datos** | `full`, `model` y `audit` (ADR 0008). |
| **Outcome** | El resultado que el sistema **afirma** al cerrar el run. Los declara un nodo `end` según el `mode` del agente (§5), salvo `abandoned` (inactividad) y `escalated` (escalamiento), que los asigna el motor. La corrección se mide contra referencias en la unidad 6. |

## 3. API del motor (contrato público, `/v1`)

```
POST /v1/runs                         # modo task, o inicio de un run conversacional
  headers: Authorization: <principal firmado>, Idempotency-Key: <uuid>
  body: {agent: "<id>[@alias|@version]", subject?: {kind, ref}, input?: {...}, lang?}
  → 201 {run_id, session_id?, release, output?, status, outcome?, handoff_ref?, trace_id}
  → 401 {code: credentials_invalid | principal_expired}
  → 403 {code: subject_forbidden | version_pin_forbidden | delegation_expired}
  → 429 {code: rate_limited | cost_budget_exceeded}

POST /v1/sessions/{session_id}/turns  # modo conversacional
  headers: Authorization: <principal firmado>
  body: {text, channel, lang?, client_turn_id, confirm?: {token, answer: yes|no}}
  → 200 {messages[], locale, awaiting: none|slot|confirmation|step_up|input,
         confirmation?: {action_summary, token, expires_at}, step_up?: {required_level, reason},
         status: open|closed|escalated, outcome?, handoff_ref?, trace_id}
  → 401 {code: credentials_invalid | principal_expired}   # el turno no se procesa; la app renueva y reintenta con el mismo client_turn_id
  → 403 {code: delegation_expired | delegation_mismatch | principal_mismatch | subject_forbidden | agent_forbidden}
  → 409 {code: turn_in_progress}
  → 410 {code: run_closed}            # p. ej. después de escalar: la conversación pasó a la plataforma del asesor
  → 429 {code: rate_limited | cost_budget_exceeded}

GET  /v1/runs/{run_id}                # estado resumido, sujeto a política
GET  /v1/runs/{run_id}/transcript     # conversación renderizada con los permisos del lector (§8.4)
GET  /v1/handoffs/{handoff_ref}       # HandoffPacket en la vista autorizada al lector
POST /v1/handoffs/{handoff_ref}/resolution
  body: {resolution_code, handoff_quality: useful|incomplete|unnecessary, notes?}
```

**Reglas de acceso:**
- **Credenciales** (§4.1): se validan antes de cargar el run. Un `401` o `403` de credenciales no procesa el turno: no hay Understand, tools, modelos ni transcript. La renovación de la sesión es responsabilidad de la app y del servicio de identidad, no del motor.
- **Principal de la sesión:** los turnos de una sesión deben venir del mismo principal (`type`, `id`) que inició el run; si no, `403 principal_mismatch`.
- **Subject** (§4.2):
  - `customer`: se deriva de `principal.id`.
  - `advisor`: debe coincidir con `on_behalf_of.subject`.
  - `service` y `builder`: se autorizan por scopes.
- **Versión:** solo `service` y `builder` pueden fijar `@version` o un alias distinto de `@prod`.
- **Idempotencia:** un `POST /v1/runs` repetido con la misma `Idempotency-Key` devuelve el run ya creado.
- **Límites:** tasa por principal y tope de costo diario por principal. Los aplica la API con los contadores del gateway (unidad 5).
- **Streaming:** no hay streaming de texto generado; toda respuesta generada sale completa después del validador. Las plantillas se envían de inmediato.

Todas las respuestas llevan `trace_id`. Los errores usan `application/problem+json` con un `code` estable.

## 4. Ciclo de un turno o de un run

Todo instante que usa el motor sale de un `Clock` inyectado. El instante de inicio del turno se registra en `turn_started` (§11).

1. **Cargar.** Los tres primeros pasos rechazan el turno **antes** de cargar estado, llamar modelos o ejecutar tools; el mensaje no se procesa ni va al transcript.
   - **Firma:** se valida la firma del principal y de `on_behalf_of`. Si falla, `401 credentials_invalid`. No se escribe en la cadena de ningún run (quien llama no está identificado); queda solo en el log de seguridad.
   - **Vigencia:** con el instante del `Clock`. Principal vencido → `401 principal_expired`; delegación vencida o con `grant_ref` revocado → `403 delegation_expired`. Se registra `access_denied` en la cadena del run con el motivo. La validez se evalúa una vez por turno, a su inicio.
   - **Coincidencia:** en un run existente, `(principal.type, principal.id)` debe coincidir con el snapshot de `principal` del run. Si no, `403 principal_mismatch` y `access_denied`.
   - **Límites:** tasa y costo por principal ya validado; si se exceden, `429`. Antes de validar la firma solo aplican límites por IP o canal en el gateway.
   - **Estado:** se carga el estado del run con bloqueo optimista por `state_version`. Si el run está cerrado, `410 run_closed`.
   - **Release:** en un run nuevo se resuelve y fija `release` (unidad 2).
   - **Revocación:** si la release fijada está `revoked`, se hace `escalate(release_revoked)` sin ejecutar nodos.
   - **Recuperación:** acciones en `executing` → `verify` de esa acción (ADR 0007).
   - **Abandono:** si pasó `inactivity_ttl` desde `last_activity_at`, el run se cierra con `outcome: abandoned`. Ver también el barrido de §4.10.
2. **Autorizar el agente y el subject.**
   - `authorize_agent`: `principal.type ∈ agent.invocable_by`, `subject.kind ∈ agent.subject_kinds` y `auth.level ≥ agent.min_auth_level`.
   - `authorize_subject` según la tabla. Si se niega, `403 subject_forbidden` y el evento `access_denied`. La autorización **se repite en cada llamada a tool**.

   | Principal | Subject permitido | Origen de los parámetros vinculados |
   |---|---|---|
   | `customer` (sesión) | El suyo, derivado de `principal.id` | `principal.id` |
   | `customer` (anónimo) | Ninguno de datos personales; solo agentes y tools públicas | — |
   | `advisor` | Solo `on_behalf_of.subject`, con `grant_ref` vigente | `on_behalf_of.subject.ref`, nunca del body ni del modelo |
   | `service` | Según scopes | Del subject autorizado por scope |
   | `builder` | Entidades del registro que su rol puede proponer | `principal.id` para sus borradores |

3. **Guardas deterministas e idioma.**
   - **Idioma.** Lo decide un detector local y determinista, fijado en la release (`lingua-py` a versión exacta, entidad de detección de idioma, §2). No se usa un modelo externo: la guarda corre antes de Understand, cuyos umbrales dependen del idioma (§7), y una llamada en serie duplicaría la latencia del turno (ADR 0005).
     1. **Limpieza:** se detecta sobre la vista `model` sin dígitos, montos, URLs, emojis ni tokens; se cuentan las letras restantes.
     2. **Corto:** con menos de `min_letters` letras, se conserva el `locale` (`short`).
     3. **Candidatos cerrados:** el detector elige solo entre `supported_locales` y la lista de no soportados de la release. Si no separa a los dos primeros con la distancia mínima, devuelve indeterminado y se conserva el `locale` (`undetermined`).
     4. **Histéresis:** si el primero es un idioma soportado distinto del `locale` vigente, se cambia solo si su confianza supera `switch_threshold` (`switched`); si no, se conserva (`kept`).
     5. **No soportado:** si el primero no es soportado, supera `unsupported_threshold` y el texto tiene al menos `min_letters_unsupported` letras, se responde con una plantilla en `default_locale` que indica los idiomas atendidos y no se ejecuta el flow en ese turno (`unsupported`). Si no, se conserva el `locale`.
     6. **Primer turno:** el `locale` inicial es el `lang` del request si es soportado; si no, `default_locale`.
     7. **Umbrales:** `switch_threshold` y `unsupported_threshold` salen de una corrida de calibración sobre el conjunto ES/PT (unidad 6) y no se editan a mano. Si falta la corrida, ambos valen 1.0: nunca se cambia de idioma ni se declara no soportado.
     - Con el `locale` resultante se resuelven **todas** las plantillas, prompts y el parseo del validador de ese turno. El `locale` se mantiene en los turnos siguientes hasta que la regla 4 lo cambie.
   - Límites de tamaño.
   - Detector de injection, que marca y cuenta.
   - Las salidas se registran en `turn_started` (§11).
4. **Understand** (modo conversacional con `understand` declarado).
   - El `DecisionModel` recibe la vista `model` y devuelve un struct:
   ```yaml
   command: start_flow|continue|affirm|deny|clarify|cancel|handoff|out_of_scope|interrupt   # calibrado
   flow: <enum de flows de la release>          # calibrado; solo con start_flow
   interrupt: <enum de interrupciones de la release>   # calibrado; solo con command = interrupt
   additional_flows: [<enum>]                   # no calibrado; va a pending_intents, nunca se ejecuta directo
   slots: {<nombre>: <valor>}                   # no calibrado; siempre entra como claimed
   ```
   - Solo `command`, `flow` e `interrupt` tienen umbral. Un campo calibrado bajo su umbral lleva a `clarify`, excepto las interrupciones, cuyo umbral se fija por **recall** (§4.5).
   - `affirm` y `deny` alimentan los resultados `yes` y `no` de un `confirm` pendiente. Si hay un `confirm` pendiente y el comando es otro, o `affirm`/`deny` queda bajo su umbral, el resultado es `unclear`, salvo los comandos que conservan su manejador global según §4.5.
   - Una respuesta por botón (`confirm: {token, answer}` en el request) no pasa por Understand y nunca da `unclear`.
5. **Manejadores globales** (prioridad sobre el flow activo, en este orden).

   **Con un `confirm` pendiente**, solo aplican las interrupciones, `cancel` y `handoff`. `out_of_scope`, `clarify` y cualquier campo calibrado bajo su umbral **no** activan su manejador: se convierten en el resultado `unclear` del `confirm`, que vuelve a preguntar sin cerrar el run. Una intención nueva va a `pending_intents` con su acuse (§4.6) y también da `unclear`.
   1. **Interrupciones de la release**, por `priority`. Disparan si `command = interrupt` supera su umbral **o** si la `policy` de señal opcional de esa interrupción (p. ej. palabras clave, protegida) da `true`. Ejecutan su `action`. En la demo hay una: fraude/robo → `escalate(priority: critical)`.
   2. `cancel` → cierra el flow activo y cancela sus acciones pendientes.
   3. `handoff` → `escalate(customer_request)`.
   4. `out_of_scope` → respuesta de abstención + `end(abstained)`.
   5. `clarify` → plantilla de aclaración. Hay un contador por run, `max_clarifications`; al agotarse, `end(clarify_exhausted)` o `escalate(low_confidence)`, según declare el agente.
      - **Tope global de reparación:** `max_repair_turns_per_run` (8 por defecto) suma los turnos de aclaración, los reintentos de `collect` y los `unclear` de `confirm` (ambos con `max_attempts`, que se cuenta por nodo). Al superarlo, `escalate(low_confidence)`, aunque ningún contador individual se haya agotado.
   6. `injection_flagged` → **modo degradado** en este turno: sin `respond(generate)` ni `agent`. Cada `respond(generate)` usa su `fallback_template_ref` y los nodos deterministas siguen normalmente.
6. **Flow activo e intenciones pendientes.**
   - Hay un solo flow activo.
   - Si no hay flow activo, arranca `flow`.
   - Si hay uno activo, `start_flow` y `additional_flows` van a `pending_intents`, ordenadas por `flow.priority` y, en empate, por orden de mención. El principal recibe un acuse.
   - Una intención pendiente **no** invalida una acción que espera confirmación.
   - Al terminar el flow activo, se ofrece la primera pendiente, y solo arranca con `affirm`.
7. **Avanzar.**
   - El motor ejecuta nodos hasta llegar a uno que espera al principal (`collect`, `confirm`, `respond` con `await`) o a uno terminal.
   - **Step-up:** si una tool devuelve `step_up_required`, se responde `awaiting: step_up` y el motor se detiene en ese nodo. El siguiente turno, con el nivel suficiente, lo reintenta. Si se agotan los intentos, `escalate(auth_insufficient)`.
   - **Presupuesto:** se descuenta en cada paso (`max_nodes_per_turn`, `max_model_calls_per_turn`, `max_tokens_per_run`, `max_cost_per_run`, `max_wall_ms_per_turn` medido con el `Clock`). Si se agota, `escalate(budget_exceeded)`.
   - Las escrituras siguen el protocolo de §8.2.
8. **Responder.**
   - El texto generado pasa el validador (§8.3) en la vista `model`, en el `locale` del turno.
   - Si aprueba, el renderer reemplaza los tokens para el destinatario autorizado.
   - El mensaje del usuario y la respuesta final se envían al transcript store (§8.4).
9. **Persistir.**
   - Estado nuevo + eventos de auditoría en una transacción al final del turno.
   - Las escrituras ya dejaron su intención y su resultado en transacciones propias (§8.2).
   - Los reintentos son seguros por `client_turn_id` y por `idempotency_key = action_id`.
10. **Barrido periódico.**
    - Cierra con `abandoned` los runs inactivos más allá de `inactivity_ttl` (30 min por defecto en modo conversacional).
    - Cancela sus acciones pendientes.
    - Cada decisión de vencimiento se registra como `expiry_evaluated`, con el instante usado.

**Invalidación de acciones pendientes.** Una acción `proposed` o `confirmed` sin ejecutar pasa a `cancelled`, y requiere un nuevo `confirm`, si ocurre cualquiera de estas cosas:
- `cancel`;
- abandono;
- una interrupción;
- escalamiento;
- el vencimiento de su token (`confirmation_ttl` de la tool, 5 min por defecto).

**Escalamiento (ADR 0013).** `escalate` hace tres cosas:
- construye el `HandoffPacket`;
- emite el evento `handoff_created` a un **outbox de eventos salientes** en la misma transacción del turno;
- cierra el run para el bot (`status: escalated`).

Desde ahí la conversación pertenece a la plataforma del asesor: nuevos turnos en esa sesión reciben `410 run_closed`, y la app enruta al asesor. El destino de entrega del outbox (webhook, cola o tabla) lo define la unidad 4.

## 5. Catálogo cerrado de nodos

Cada nodo tiene `id`, `type`, `config` y `next`, que mapea resultado → id de nodo. **Solo existen estos tipos.** Agregar un tipo es un cambio mayor del esquema de flows.

| Tipo | Config | Resultados |
|---|---|---|
| `decide` | `model: decision_model@v`, `input_view` (override opcional, siempre dentro de la vista `model`), `branch_on: <campo calibrado>`, `save_as`. Cada valor del enum de `branch_on` es un resultado y se cablea en `next` (M0 rev. 4) | un resultado por valor, más `low_confidence` |
| `rule` | `policy: policy_id@v` (regla de negocio protegida) **o** `expr` (JSON Logic, guardas estructurales sin literales de negocio), sobre `facts.<x>.value` y `slots.<x>` con `status: validated` | `true`, `false` |
| `collect` | `slot`, `prompt_ref` (plantilla por locale), `validator` (tipo/regex/enum o `decide`), `max_attempts` | `ok`, `max_attempts` |
| `tool` (lectura o `compute`) | `tool: tool_id@v`, `args` desde `slots`, `facts`, `decisions` (solo como argumento de una tool `compute`) o literales, `save_as` | `ok`, `error`, `timeout`, `denied` |
| `tool` (escritura `write_*`) | `action_from: <id de un confirm>`, `save_as` | `ok`, `denied`, `uncertain` |
| `confirm` | `action: {tool, args}`, `summary_template`, `reprompt_template?`, `max_attempts` (2 por defecto). Congela args, crea `action_id` y emite el token; al reentrar con la acción `proposed` y el token vigente, repite la misma acción (§8.2) | `yes`, `no`, `unclear`, `max_attempts` |
| `verify` | `readback: tool_id@v`, `by: idempotency_key \| fact:<ruta>`, `predicate`, `save_as` | `verified`, `failed` |
| `respond` | `template_ref` **o** `generate: {prompt_ref, allowed_facts[], knowledge_refs[], fallback_template_ref}`, `await: bool`, `claims: [<id de confirm>]` (opcional, `[]` por defecto) | `next` |
| `escalate` | `reason_code`, `target_queue`, `priority_expr?` | terminal |
| `end` | `outcome: resolved\|abstained\|cancelled\|clarify_exhausted` (modo task: `completed\|failed`), `output_map?`. `abandoned` y `escalated` no se declaran: los asigna el motor (§4.1, §4.10, §9) | terminal |
| `agent` *(producción)* | `tools_allowed[]` (clases `read` y `compute`), `max_steps`, `prompt_ref`, `goal`. Sus lecturas entran como hechos y su salida pasa por el validador | `answered`, `gave_up` |
| `subflow` *(producción)* | `flow: flow@v`, `map_in`, `map_out` | resultados declarados por el subflow |
| `await_approval` *(producción, ADR 0014)* | `approver: {principal_type, roles[]}`, `summary_template`, `timeout` | `approved`, `rejected`, `timeout` |

Notas sobre el catálogo:
- `step_up_required` no se cablea en el flow: lo maneja el motor (§4.7).
- Las plantillas y prompts se resuelven por el `locale` del turno. Un flow que declara un locale sin plantilla no se publica (§6.12).
- **Reclamos de éxito de un `respond`.** El conjunto de acciones cuyo éxito afirma un `respond` es la unión de:
  - **declarados:** los `confirm` listados en `claims`;
  - **derivados:** toda acción X tal que el `respond` lee un hecho que proviene de X, es decir, el `save_as` del nodo de escritura de X o el `save_as` del `verify` de X, o un hecho `compute` cuya procedencia (`inputs`) incluye alguno de ellos. Cuenta como lectura una variable de su plantilla o una entrada de `allowed_facts` en `generate`.

  Un `respond` cuyo conjunto de reclamos es vacío es un **`respond` seguro** (§6.1.6). El validador estático calcula el conjunto sobre el grafo del flow; el motor no lo recalcula en runtime.

**Ejemplo abreviado** (vive en `agent-registry`):
```yaml
id: disputa-cargo
version: 1.0.0
priority: 50
nodes:
  - {id: pedir_cargo, type: collect, config: {slot: descripcion_cargo, prompt_ref: t/pedir_cargo, max_attempts: 2},
     next: {ok: buscar_tx, max_attempts: esc_sin_datos}}
  - {id: buscar_tx, type: tool, config: {tool: buscar_transacciones@1, args: {texto: slots.descripcion_cargo}, save_as: candidatas},
     next: {ok: coincide, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: coincide, type: decide, config: {model: match-cargo@2, branch_on: match, save_as: coincide},   # output: {match: unica|ninguna|varias, transaction: <token>}
     next: {unica: elegir, ninguna: aclarar, varias: aclarar, low_confidence: aclarar}}
  - {id: elegir, type: tool, config: {tool: seleccionar@1, args: {lista: facts.candidatas, id: decisions.coincide.transaction}, save_as: transaccion_elegida},
     next: {ok: a_usd, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: a_usd, type: tool, config: {tool: convertir_moneda@1, args: {monto: facts.transaccion_elegida.value.amount, moneda: facts.transaccion_elegida.value.currency, destino: USD}, save_as: monto_usd},
     next: {ok: umbral, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: umbral, type: rule, config: {policy: escalamiento-disputa-monto@1}, next: {true: esc_monto, false: confirmar}}
  - {id: confirmar, type: confirm, config: {action: {tool: radicar_pqr@1, args: {transaction_id: facts.transaccion_elegida.value.transaction_id, descripcion: slots.descripcion_cargo}}, summary_template: t/resumen_pqr},
     next: {yes: radicar, no: fin_cancelado, unclear: confirmar, max_attempts: no_confirmado}}   # reentra con la misma acción; no_confirmado es un respond seguro
  - {id: radicar, type: tool, config: {action_from: confirmar, save_as: pqr}, next: {ok: verificar, uncertain: verificar, denied: esc_tool}}
  - {id: verificar, type: verify, config: {readback: obtener_pqr@1, by: idempotency_key, predicate: {"==": [{var: readback.status}, "Open"]}, save_as: pqr_verificada},
     next: {verified: responder_ok, failed: esc_verif}}
  - {id: responder_ok, type: respond, config: {template_ref: t/pqr_radicado, claims: [confirmar]}, next: {next: fin}}   # t/pqr_radicado lee facts.pqr_verificada: el reclamo también se deriva
  - {id: fin, type: end, config: {outcome: resolved}}
  # aclarar, esc_*, fin_cancelado, no_confirmado omitidos
```

```yaml
# policies/escalamiento-disputa-monto.yaml (entidad protegida, dueño: riesgo)
id: escalamiento-disputa-monto
version: 1.0.0
owner: riesgo
expr: {">": [{var: facts.monto_usd.value}, 500]}
rationale: "Disputas sobre montos altos requieren revisión humana."
```

## 6. Validación

### 6.1 Validación del flow aislado (gate G0 de la unidad 2, al proponer o publicar)

Un flow se rechaza si ocurre cualquiera de estas situaciones:
1. Algún nodo no pertenece al catálogo, o su `config` no valida contra el esquema de su tipo.
2. Alguna referencia (`tool@v`, `policy@v`, `decision_model@v`, `template_ref`, `prompt_ref`) no existe en el registro.
3. Hay nodos inalcanzables, o algún resultado declarado del nodo no tiene `next`.
4. Existe un ciclo sin un nodo que espere al principal, porque sería un bucle infinito dentro de un turno.
5. **Se viola el invariante de escritura.** Toda escritura debe cumplir estas cuatro condiciones:
   - usa `action_from` hacia un `confirm` de la misma tool que la domina;
   - sus ramas `ok` y `uncertain` llevan a un `verify` de esa acción;
   - la tool declara `readback_by: idempotency_key`;
   - para cada acción X en el conjunto de reclamos de un `respond` (declarados o derivados, §5), **todo camino** desde la entrada del flow hasta ese `respond` pasa por la rama `verified` del `verify` de X. En particular, ningún `respond` alcanzable desde `failed`, desde `denied` o antes del `verify` puede reclamar X.
6. Alguna rama de fallo (`low_confidence`, `error`, `timeout`, `denied`, `uncertain` sin `verify`, `failed`, `max_attempts`) termina en algo distinto de `collect`, `respond` seguro (conjunto de reclamos vacío, §5), `escalate`, `end(abstained|clarify_exhausted)` o, en flows de modo task, `end(failed)`.
7. Un nodo `agent` referencia una tool que no es de clase `read` o `compute`.
8. Un `rule` con `expr` contiene literales de negocio (números o textos que no sean `null`, booleanos o valores de un enum declarado).
9. Un `respond(generate)` no declara `fallback_template_ref`.
10. Un argumento lee `decisions.*` en una tool que no es de clase `compute`.
11. Un `decide` ramifica por un campo que no está en `calibrated_fields` de su modelo.
12. Falta una plantilla o prompt para algún locale de `supported_locales` de los agentes que declaran usar el flow. Esta regla se verifica en el gate de release.
13. `claims` de un `respond` lista un id que no es un `confirm` del mismo flow.
14. Un `end` declara un `outcome` fuera de los declarables (`resolved`, `abstained`, `cancelled`, `clarify_exhausted`, `completed`, `failed`); en particular, `abandoned` o `escalated`. Los `end` de un flow mezclan outcomes de modo conversacional y de modo task.
15. Un `prompt` referenciado no tiene un `model_profile` existente (ADR 0016).
16. Un flow de modo task contiene nodos que esperan al principal (`collect`, `confirm`, `respond` con `await`). En el MVP, por lo tanto, un agente task no escribe.

Los algoritmos exactos de cada regla (en particular 4, 5 y la derivación de reclamos) están en M1.

**Límite conocido de la regla 5.** Un texto fijo que afirma éxito sin leer hechos de la acción y sin declarar `claims`, o un texto generado que lo afirma sin citarlo, pasa esta validación. Lo primero se revisa al publicar el flow; lo segundo lo mide la unidad 6 como afirmación que contradice un hecho de referencia (§12).

### 6.2 Gate de release (unidad 2)

Valida el conjunto armado de agentes, flows, políticas e interrupciones:
- Cada intención (valor de `flow` en Understand) tiene un solo dueño dentro de la release.
- Toda tool de un flow está en `tools_allowed` de cada agente que lo usa.
- Las políticas referenciadas tienen la aprobación de su dueño (ADR 0009).
- Las interrupciones referencian acciones válidas y tienen umbral calibrado por recall.
- El grafo de subflows es acíclico (producción).
- Se cumple la regla 6.1.12 para cada agente.
- Los outcomes de los `end` de cada flow corresponden al `mode` de cada agente que lo usa: `resolved|abstained|cancelled|clarify_exhausted` para `conversational`, `completed|failed` para `task`. Un flow no puede servir a agentes de modos distintos.

En runtime, un **tope de profundidad** de flows actúa como defensa adicional.

## 7. Contrato `DecisionModel` (ADR 0005)

```yaml
id: <string>
version: <semver>
output_schema: <JSON Schema>        # struct cerrado
calibrated_fields: [<campos enum del esquema>]   # solo estos tienen probabilidad y umbral
input_view: [<rutas del estado>]    # siempre en la vista `model` (§8.1)
providers:                          # cadena de respaldo, en orden
  - {provider: jev, config: {model: <id exacto>, timeout_ms: 800}}
  - {provider: classifier, config: {artifact: <ref a modelo entrenado + hash de datos>}}
calibration: {method: none|isotonic|platt|temperature, run: <calib_run_id>}
thresholds_from: <calib_run_id>     # tabla (campo calibrado, valor, proveedor, idioma) → umbral + hash del split; no se edita a mano
```

- **Umbrales:**
  - La corrida de calibración produce una tabla con clave `(campo calibrado, valor, proveedor, idioma)`, además del hash del split usado.
  - Si falta una combinación en la tabla, su umbral es 1.0: esa rama nunca se toma y cae en `low_confidence`, que es la salida segura.
- **Proveedores:** `jev`, `classifier`, `llm_structured` (vía gateway, unidad 5) y `rule` (p ∈ {0,1}).
- **Salida de `decide()`:** `{value (struct), p_cal{campo: p}, p_raw{campo: p}, top_k{campo: [...]}, provider_used, model_version, fallback_depth, latency_ms, tokens, cost_usd}`. Se registra completa en `decision_made`.
- **Campos no calibrados** (p. ej. `slots`, `additional_flows`, un token elegido): nunca ramifican ni se ejecutan directamente. Los slots quedan `claimed`, las intenciones adicionales van a `pending_intents`, y los tokens solo se usan como argumento de una tool `compute`, que produce un hecho con procedencia.
- **Tokens en la salida:** se resuelven contra el `token_map` del run. Un token desconocido invalida la salida y lleva a `low_confidence`.
- **Calibración:**
  - Propia, por proveedor e idioma, en el split de desarrollo. La ECE se reporta.
  - Los datos en PT son conjuntos sintéticos etiquetados que **produce otro equipo**; el núcleo solo los consume (ADR 0012). Si la muestra PT no alcanza el mínimo de la unidad 6, se usa la calibración ES y se reporta como limitación.
- **`llm_structured`:** probabilidad desde logprobs, o por auto-consistencia (producción). Sin probabilidad estimable, `p_cal = null`, que se trata como bajo umbral. En el MVP solo se usa como baseline.
- **Fallas:** timeout o error → siguiente proveedor; salida fuera de esquema → 1 reintento; agotada la cadena → `low_confidence`.

## 8. Estado del run, vistas de datos, protocolo de escritura, validador y transcript

**Estado** (una fila por run en `run_state`, versionada con `state_version`):
- **Identidad y contexto:** `run_id`, `session_id?`, `release`, `agent@v`, `principal` (snapshot sin secretos), `on_behalf_of?`, `subject`, `mode`, `locale`.
- **Flujo:** `active_flow {flow@v, node_id, local_slots}` y `pending_intents[{flow, priority, mention_order}]`.
- **Slots:** `slots{nombre: {value, status: claimed|validated, source_turn}}`.
  - `validated` significa que el slot pasó el `validator` de su `collect` (formato, tipo o enum).
  - Un valor comprobado contra un registro no es un slot validado: es un hecho, producido por una tool.
- **Hechos:** `facts{nombre: {fact_id, value (vista full), source: {kind: tool|compute|identity|knowledge, ref, inputs?: [fact_id|decision_id]}, ts}}`.
- **Decisiones:** `decisions{nombre: {decision_id, value, p_cal, provider_used, model_version}}`.
- **Acciones:** `actions[]` con:
  - `action_id`, `tool@v`, `args` (congelados), `args_hash`;
  - `state: proposed|confirmed|executing|executed|uncertain|denied|verified|failed|cancelled`;
  - `confirmation_token_hash`, `token_exp`, `idempotency_key = action_id`.
- **Tokens:** `token_map`, cifrado, que nunca sale del núcleo.
- **Contadores y seguimiento:** `open_questions[]`, `budgets_used{}`, `turn_count`, `clarifications_used`, `node_attempts{node_id: n}` (`collect` y `confirm`), `repair_turns_used`, `last_activity_at`, `degraded_turns[]`.

### 8.1 Vistas de datos (ADR 0008)

Las clases de campo son `pii_direct`, `pii_quasi`, `financial`, `untrusted_text` y `public`. La clasificación la publica el equipo de datos como `FieldClassification`; mientras no exista, el núcleo usa un catálogo por defecto derivado del diccionario de datos:

| Clase | Campos por defecto |
|---|---|
| `pii_direct` | `first_name`, `last_name`, `document_number`, `email`, `mobile_phone`, `landline_phone`, `address`, `product_number`, `ip_address` |
| `pii_quasi` | `date_of_birth`, `postal_code`, `latitude`, `longitude` |
| `untrusted_text` | `complaints.description`, `complaints.resolution`, `call_transcripts.full_text`, `call_transcripts.customer_text`, `call_transcripts.agent_text`, `satisfaction_surveys.open_comments` |

Un campo sin clasificar se trata como `pii_direct`.

| Vista | Contenido | Destinos |
|---|---|---|
| `full` | Resultado completo | `facts`, `rule`/`policy`, `verify`, argumentos de acciones. Nunca sale del núcleo, salvo por el renderer |
| `model` | `pii_direct` → token del run; `pii_quasi` → generalizado o eliminado; `untrusted_text` → tokenizado y envuelto en `<datos_no_confiables fuente="…">…</datos_no_confiables>`; el resto pasa | JEV, LLM y classifier |
| `audit` | Enmascarada, sin tokens reversibles, + huella con clave de `full` (§8.1.1) | Log de auditoría, trazas y handoff por defecto |

El **renderer** reemplaza los tokens solo para destinatarios autorizados por la política para cada campo. En los demás casos los enmascara.

#### 8.1.1 Huellas con clave

Un `sha256` sin clave de datos con PII de baja entropía (documento, teléfono, fecha de nacimiento) se revierte probando valores, sobre todo cuando el resto del resultado es visible en la vista `audit`. Por eso toda huella de datos de cliente usa clave:

- **Formato:** `{alg: HMAC-SHA256, kid, value}`, con `value = HMAC-SHA256(k[kid], JCS(dato))`. JCS es la serialización canónica de JSON (RFC 8785); los textos se hashean como cadena UTF-8 en NFC.
- **Dónde aplica:** la huella de `full` en la vista `audit` y la huella de cada entrada del transcript (§8.4).
- **Dónde no aplica:** la cadena de hash entre eventos (§11) sigue en `sha256`, porque encadena registros que ya están en vista `audit`.
- **Clave:**
  - vive en un gestor de secretos, fuera del log de auditoría y de la base del núcleo; en la demo es un secreto de entorno etiquetado como tal;
  - rota por `kid`: los registros nuevos usan la clave vigente y las anteriores se conservan solo para verificar, durante la retención del log;
  - solo el servicio de verificación de evidencia puede usarla; los lectores del log no tienen acceso.
- **Verificación:** para probar qué devolvió una tool, se reconstruye el resultado desde el backend, se canoniza con JCS y se compara el HMAC con el `kid` registrado.
- **Producción:** clave por subject, que permite volver inutilizables las huellas de un cliente destruyendo su clave (borrado criptográfico), en línea con la supresión del transcript.

### 8.2 Protocolo de escritura (ADR 0007)

1. `confirm` congela `{tool, args}`, crea `action_id`, guarda `proposed` y emite el token con `expires_at`.
   - **Reentrada:** si el `confirm` ya tiene una acción `proposed` con el token vigente, no se crea otra: se repite la misma acción (mismo `action_id`, mismo token) con `reprompt_template` o, si no hay, `summary_template`. Si el token venció, esa acción pasa a `cancelled` y se congela una nueva. Un `confirm` nunca tiene más de una acción `proposed`.
   - **Agotamiento:** al llegar a `max_attempts`, la acción pasa a `cancelled` y el nodo sale por `max_attempts`.
2. Una respuesta afirmativa, sea `affirm` en Understand o `confirm.answer = yes` con el token, y con el token vigente, pasa la acción a `confirmed`.
3. El nodo con `action_from` **commitea en una transacción propia** `executing` + `action_dispatched`.
4. Se invoca la tool con `idempotency_key = action_id`. El backend garantiza la unicidad de la clave.
5. **Commitea en una transacción propia** el resultado (`executed`, `uncertain` o `denied`) con `tool_called`.
6. `verify` hace el readback por `idempotency_key` y lleva la acción a `verified` o `failed`.
7. Solo después de `verified` un `respond` puede afirmar el éxito de la acción (§5, §6.1.5).

- **Recuperación:** una acción en `executing` al cargar el run va a su `verify`.
- **Invalidación:** ver §4.

### 8.3 Validador de respuesta (ADR 0011)

Es determinista y corre sobre la vista `model`, en el `locale` del turno:
1. **Formato:** el modelo devuelve `{text, citations: [fact_id|page_ref]}`.
2. **Citas:** cada una existe en `facts` o en páginas recuperadas en este run, y está permitida por el nodo.
3. **Cifras:** se parsean por locale y se comparan numéricamente contra los hechos o páginas citados. Las cifras calculadas deben venir de hechos `compute`.
4. **Tokens e identificadores:** todo token existe en el `token_map`, y ningún identificador `pii_direct` aparece en claro.
5. **Idioma:** el mismo detector de §4.3, con los mismos candidatos, aplicado al texto generado, da el `locale` del turno. Un resultado `short` o `undetermined` no rechaza.

Si falla: 1 regeneración → `fallback_template_ref` → `escalate(validation_failed)`. El borrador rechazado y su motivo van al transcript store.

### 8.4 Transcript (ADR 0003, ADR 0013)

- **Qué se guarda:** por turno, se envían al transcript store (unidad 7), en vista `model`:
  - el mensaje del usuario;
  - la respuesta final;
  - los borradores rechazados con su motivo.
- **Auditoría:** guarda la huella con clave de cada entrada (§8.1.1), así que suprimir el transcript no rompe la cadena y la huella no permite confirmar qué decía un mensaje borrado sin la clave. El transcript store tiene retención y supresión propias.
- **Lectura:** `GET /v1/runs/{run_id}/transcript` devuelve la conversación **renderizada con los permisos del lector**. Un asesor con delegación vigente sobre el subject ve los campos que la política le permite, y el resto enmascarado. Es la vía por la que el asesor tiene la conversación a mano. El `HandoffPacket` sigue siendo estructurado y no incrusta el transcript.
- **Nunca** se guarda razonamiento intermedio ni chain-of-thought del modelo.

## 9. `HandoffPacket` y evento de escalamiento (ADR 0013)

```yaml
handoff_ref, run_id, release, agent@v, principal_type, subject {kind, ref (enmascarado)}
target_queue, priority, reason_code        # rule:<id> | policy:<id> | interrupt:<id> | low_confidence |
                                           # budget_exceeded | tool_failure | customer_request |
                                           # verification_failed | validation_failed | release_revoked |
                                           # auth_insufficient
language
request_summary: {text, citations[]}       # validado con §8.3; renderizado según el lector
verified_facts[]                           # en la vista autorizada al lector
claimed_not_verified[]
actions_taken[]                            # con estado de verificación
open_questions[]
evidence_refs[]                            # call_ids, policy@v evaluadas, decision_ids, page@v
transcript_ref                             # → GET /v1/runs/{run_id}/transcript
```

- **Evento de escalamiento:** `escalate` emite `handoff_created {handoff_ref, run_id, target_queue, priority, reason_code, language, reportable_attrs}` al outbox de eventos salientes, en la misma transacción del turno. El paquete completo se obtiene con `GET /v1/handoffs/{handoff_ref}`, sujeto a política.
- **Delegación:** cuando la plataforma del asesor asigna el caso, el emisor de asignaciones firma la delegación (`on_behalf_of`).
- **Resolución:** la resolución del receptor (`POST .../resolution`) se registra como evento y es etiqueta para la unidad 6 y señal para la auto-mejora.

## 10. Manejo de fallas

| Falla | Comportamiento |
|---|---|
| Proveedor de decisión caído | Cadena de respaldo → `low_confidence` |
| Tool de lectura o `compute`: timeout o error | Timeout por tool; ≤2 reintentos solo si es idempotente; circuit breaker; rama `error`/`timeout` |
| Tool `write_*`: cualquier fallo del backend | `uncertain` → `verify` por `idempotency_key` |
| Tool `write_*`: bloqueada por política antes de llamar | `denied` |
| Nivel de autenticación insuficiente | `awaiting: step_up` en el mismo nodo; al agotar intentos, `escalate(auth_insufficient)` |
| Validador de respuesta | 1 regeneración → plantilla → `escalate` |
| Alerta de injection | Modo degradado en ese turno |
| Idioma no soportado | Plantilla en `default_locale` con los idiomas atendidos; el flow no avanza en ese turno |
| Presupuesto del run agotado | `escalate(budget_exceeded)` |
| Límite de tasa o costo del principal | `429` |
| Release revocada | `escalate(release_revoked)` |
| Firma inválida | `401 credentials_invalid` antes de cargar el run; solo log de seguridad |
| Principal vencido | `401 principal_expired`; turno no procesado; `access_denied` |
| Delegación vencida o revocada | `403 delegation_expired`; turno no procesado; `access_denied` |
| Principal distinto al del run | `403 principal_mismatch`; `access_denied` |
| Token de confirmación vencido | Acción `cancelled`; nuevo `confirm` |
| Subject no autorizado | `403` o `denied`; evento `access_denied` |
| Caída entre intención y resultado de una escritura | `executing` → `verify` al cargar |
| Caída en otro punto del turno | Commit atómico; `client_turn_id` evita duplicados |
| Turnos concurrentes | `409 turn_in_progress` en el MVP (chat web). En producción, un buzón por sesión fusiona los mensajes que llegan durante un turno y los procesa en el siguiente (respuesta `202`) |
| Turno en un run cerrado o escalado | `410 run_closed` |

## 11. Observabilidad (ADR 0003)

- **Spans OTel:**
  - `invoke_agent` › `agentcore.decide`, `agentcore.rule`, `execute_tool`, `chat`.
  - Atributos: `agentcore.release`, `agentcore.agent`, `agentcore.flow`, `agentcore.node`, `agentcore.principal_type`, `agentcore.locale`, `gen_ai.*`.
  - El contenido capturado en trazas usa la vista `audit`.
- **Eventos de auditoría** (vista `audit`, **cadena de hash por run**):
  - **Inicio del run:** `run_started`, con los **atributos de reporte**. Son los de `principal.attrs` que aparecen en la lista `reportable_attrs` de la política: p. ej. `country`, `segment`, `channel`, `locale`. Son gruesos y no identifican a nadie.
  - **Inicio de turno:** `turn_started`, con el instante del `Clock`, la bandera de injection y la detección de idioma: `{detector@v, letters, top2: [{lang, score}], decision: kept|switched|short|undetermined|unsupported, locale_prior, locale}`.
  - **Decisiones:** `command_emitted`, `node_entered`, `decision_made`, `rule_evaluated`.
  - **Acciones:** `action_dispatched`, `tool_called`, `action_confirmed`, `action_verified`.
  - **Autenticación y vencimientos:** `step_up_requested`, `expiry_evaluated`.
  - **Respuesta:** `response_emitted`, con el resultado del validador, la huella con clave de la entrada del transcript (§8.1.1), el conjunto de reclamos del `respond` (`claims` declarados y derivados) y el uso del LLM (`llm`: llamadas, latencia, tokens, costo).
  - **Fin de turno:** `turn_completed`, con la duración del turno, el desglose por etapa (guardas, Understand, flow, respuesta), el modo degradado y en qué queda esperando el run.
  - **Campos de medición (rev. 14):** `decision_made.latency_ms`, `tool_called.latency_ms`, `response_emitted.llm` y los tiempos de `turn_completed` se miden con `Clock.monotonic_ns()` o los reporta el proveedor. No son deterministas: ninguna decisión depende de ellos y el replay los excluye de la comparación (M0 §2.10).
  - **Seguridad:** `injection_flagged`, `access_denied` (con motivo: `subject_forbidden`, `principal_expired`, `delegation_expired`, `principal_mismatch`, `tool_denied`). Una firma inválida no genera evento en la cadena de ningún run.
  - **Cierre:** `escalated`, `handoff_resolved`, `run_closed`.
- **Eventos salientes:** `handoff_created` va al outbox de eventos salientes (unidad 4). La auto-mejora y la plataforma del asesor consumen de ahí o del log.
- **Replay.** Reconstruye un run desde sus registros con la misma release y el mismo código. No re-ejecuta modelos ni tools externas: no se garantiza que un modelo dé la misma salida, y eso lo mide la variabilidad entre corridas de la unidad 6. Comparar releases distintas no es replay: lo hace el harness de la unidad 6.
  - **Integridad primero:** antes de consumir un evento, el replay verifica la cadena de hash del run. Una cadena rota aborta el replay con `chain_broken`.
  - **Clasificación de entradas:**

    | Se lee siempre de eventos (no determinista) | Se recalcula siempre | Se recalcula solo con vista `full` disponible |
    |---|---|---|
    | Salida de guardas (`turn_started`), salidas de modelos (`decision_made`), resultados de tools de lectura y escritura (`tool_called`), instantes del `Clock`, decisiones de vencimiento (`expiry_evaluated`) | Transiciones, orden de manejadores, `pending_intents`, actualización de `locale`, contadores de reparación, invalidación de acciones | `rule`/`policy`, tools `compute`, predicado de `verify`, validador de respuesta (§8.3), reclamos de éxito (§5) |

  - **Modo `fixture` (CI).**
    - Corre sobre runs grabados con **datos sintéticos**, cuyo fixture guarda los eventos y, además, las entradas en vista `full` (resultados de tools y salidas de modelos). Nunca se crean fixtures con datos de producción.
    - Recalcula las tres columnas. Garantía: **con los mismos registros, la misma release y el mismo código, la secuencia de `node_entered`, los resultados de `rule`/`verify`/`compute` y los veredictos del validador son idénticos.**
    - Es el que corre en CI y bloquea un merge si diverge.
  - **Modo `audit` (runs reales).**
    - Solo usa el log de auditoría (vista `audit`).
    - Recalcula las dos primeras columnas. Los resultados de la tercera se leen de `rule_evaluated`, `tool_called` (compute), `action_verified` y `response_emitted`, y se comprueba que las transiciones sean las que el flow dicta para esos resultados.
    - Garantía: **el camino registrado es el único consistente con el flow@v de la release y con las decisiones y resultados registrados, y el registro no fue alterado.** No re-verifica la evaluación de reglas, `compute` ni el validador sobre datos reales.
  - **Salida:** `{mode, run_id, release, verdict: match|diverged|chain_broken, first_divergence?: {event_seq, expected, actual}}`.
- **No se registra el razonamiento intermedio ni el chain-of-thought del modelo.**

## 12. Evaluación de las entidades de esta unidad (se detalla en la unidad 6)

- **Outcome frente a corrección:** `outcome` es lo que el sistema afirma. La corrección se mide contra referencias por caso: resultado, hechos clave y acción esperados. Un caso es **materialmente incorrecto** si el resultado difiere, si los argumentos de la acción no coinciden o si una afirmación contradice un hecho de referencia. Todo se comprueba con chequeos deterministas.
- **LLM juez:** solo para tono y completitud del handoff, con rúbrica y validado con una muestra humana.
- **Denominadores:** los runs `abandoned` cuentan como casos en alcance **no resueltos** y además se reportan como tasa propia.
- **Desgloses:** todas las métricas se desagregan por idioma (PT marcado como sintético) y por los atributos de reporte de `run_started`, mostrando el tamaño de muestra.
- **Eficiencia y tiempos (rev. 14):** se calculan desde el log de auditoría, no desde las trazas (que se muestrean), y se comparan por release:
  - **Conversación:** duración total (`run_started` → `run_closed`), tiempo activo del motor (Σ `turn_completed.duration_ms`), tiempo de espera del usuario, turnos por run, tiempo en esperas de `step_up` y `confirm`.
  - **Turno:** p50/p95 total y por etapa.
  - **Tools:** por `tool@v`, latencia p50/p95, tasa por `status`, reintentos y llamadas por run.
  - **Modelos:** por `DecisionModel` y por `prompt@v`, latencia, tokens y costo; tasa de respaldo y de regeneración.
  - **Costo por run:** Σ `decision_made.cost_usd` + Σ `response_emitted.llm.cost_usd`, también por outcome.

| Entidad | Métrica principal | Secundarias |
|---|---|---|
| Agente / flow | Tasa de resolución segura sobre los casos en alcance (con resultados inseguros = restricción dura) | Contención, precisión/recall de escalamiento, calidad del handoff, turnos por run, costo por run, p50/p95 del turno y por etapa, duración de la conversación, caída por nodo, tasa de `abandoned` |
| Tool | Tasa de `ok` por `tool@v` | Latencia p50/p95, reintentos, `uncertain` (escrituras), llamadas por run |
| `DecisionModel` | Precisión al umbral con cobertura, por campo calibrado | ECE, macro-F1, latencia, costo; por idioma y proveedor. Para interrupciones, la métrica principal es el recall |
| `Policy` | Acuerdo con la decisión de referencia | Escalamientos provocados frente a necesarios |
| Validador de respuesta | Afirmaciones sin fuente que se escapan (auditoría muestral) | Tasa de regeneración, falsos rechazos |
| Vistas de datos | Fugas de `pii_direct` en claro hacia proveedores externos (objetivo 0, con cota superior) | Tokens desconocidos en salidas |

## 13. Pruebas

1. **Validación:** un flow inválido por cada regla de §6.1 y una release inválida por cada chequeo de §6.2. En particular:
   - un `end(abandoned)` o `end(escalated)` no se publica;
   - un flow con `end(resolved)` y `end(completed)` no se publica;
   - un agente `task` que usa un flow con `end(resolved)` no pasa el gate;
   - en un flow task, una rama de fallo que termina en `end(failed)` se publica.
2. **Replay** (§11):
   - modo `fixture` en CI sobre al menos un fixture por camino del flow de demo (resuelto, cancelado, escalado por monto, `uncertain` → `verify`, step-up, interrupción);
   - un cambio en una regla, una `compute` o el validador que altera un resultado produce `diverged` con la primera divergencia;
   - un evento alterado en la cadena produce `chain_broken` en ambos modos;
   - modo `audit` sobre un run grabado da `match` sin acceder a la vista `full`;
   - un fixture que contiene un valor no sintético (fuera del catálogo de datos de prueba) no se acepta en el repo.
3. **Invariantes de escritura:**
   - sin `confirm` o sin `verify` no se publica;
   - 5xx o timeout → `verify` por clave;
   - una caída después de `action_dispatched` no produce segunda ejecución;
   - el reintento del turno produce el mismo `idempotency_key`.
   - **Reclamos de éxito:**
     - un `respond` con `claims: [X]` alcanzable antes del `verify` de X no se publica;
     - un `respond` sin `claims` cuya plantilla lee `facts.<save_as de la escritura>` antes de `verified` no se publica (reclamo derivado);
     - un `respond` que lee un hecho `compute` derivado del resultado de la escritura hereda el reclamo;
     - en un flow con dos escrituras, un `respond` que reclama X y está después de `verified` de X pero antes del `verify` de Y se publica;
     - un `respond` con reclamos en una rama `failed` o `denied` no se publica; uno sin reclamos sí (`respond` seguro);
     - `claims` con un id que no es un `confirm` del flow no se publica.
4. **Autorización (IDOR):**
   - un subject ajeno da `403`;
   - un asesor sin delegación, o con una vencida, recibe `403`/`denied`;
   - un argumento del modelo no sustituye un parámetro vinculado;
   - fijar `@version` siendo `customer` o `advisor` da `403`.
5. **Autenticación:**
   - un anónimo no accede a datos personales;
   - `step_up` espera y se retoma en el mismo nodo;
   - firma inválida → `401 credentials_invalid`, sin llamadas a modelos, tools ni transcript, y sin eventos en la cadena del run;
   - principal vencido → `401 principal_expired` sin Understand; el reintento con token renovado y el mismo `client_turn_id` se procesa una sola vez;
   - delegación vencida o revocada → `403 delegation_expired`;
   - turno con otro principal en una sesión existente → `403 principal_mismatch` (incluido otro asesor con delegación vigente sobre el mismo subject);
   - un exceso de tasa con firma inválida no consume la cuota del principal suplantado.
6. **Vistas:**
   - ningún request capturado hacia JEV o el LLM contiene `pii_direct` en claro;
   - el `untrusted_text` llega delimitado;
   - el transcript renderizado respeta los permisos del lector;
   - ninguna huella de `full` ni de transcript es un `sha256` sin clave;
   - el mismo resultado con claves en distinto orden produce la misma huella (JCS);
   - un registro con `kid` antiguo se verifica después de rotar la clave;
   - con la huella y los campos visibles en `audit`, probar todos los documentos de un rango no permite recuperar el valor sin la clave.
7. **Validador:**
   - montos en `1.234,56` y `1,234.56` según locale;
   - una conversión con hecho derivado pasa;
   - una cifra calculada sin hecho `compute` se rechaza;
   - una respuesta en un idioma distinto del `locale` se rechaza.
8. **Idioma:**
   - un turno en PT recibe respuesta en PT;
   - un cambio ES → PT a mitad del run cambia el `locale`;
   - un idioma no soportado recibe la plantilla en `default_locale`;
   - "sí", "ok", "não" o un mensaje con solo un monto conservan el `locale` (`short`);
   - un mensaje en portuñol sin distancia suficiente conserva el `locale` (`undetermined`);
   - una palabra suelta en inglés no activa la plantilla de no soportado;
   - sin corrida de calibración, nunca hay `switched` ni `unsupported`;
   - una respuesta generada corta no se rechaza por idioma.
9. **Intenciones:**
   - "bloquea mi tarjeta y disputa este cargo" arranca el flow de mayor prioridad y deja el otro pendiente;
   - una intención nueva durante un `confirm` no lo invalida;
   - la interrupción de fraude se antepone al flow activo.
10. **Ciclo de vida:**
    - inactividad → `abandoned` con acciones canceladas;
    - `escalate` emite `handoff_created` y los turnos siguientes reciben `410`;
    - una release revocada escala.
11. **Reparación y umbrales:**
    - al superar `max_repair_turns_per_run`, el caso escala;
    - dos `unclear` seguidos en un `confirm` con `max_attempts: 2` salen por `max_attempts` y la acción queda `cancelled`;
    - los `unclear` de `confirm` suman al tope global;
    - al reentrar a un `confirm` con el token vigente se reutilizan `action_id` y token, y nunca hay dos acciones `proposed` del mismo `confirm`;
    - con el token vencido, la reentrada cancela la acción anterior y crea una nueva; un `yes` con el token viejo no confirma nada;
    - con un `confirm` pendiente, un mensaje fuera de tema da `unclear` y no cierra el run; `cancel`, `handoff` y la interrupción de fraude sí aplican;
    - una respuesta por botón nunca da `unclear`;
    - una combinación de la tabla de umbrales ausente toma `low_confidence`;
    - una referencia con rango en runtime hace fallar la carga de la release.
12. **API:**
    - `Idempotency-Key` repetida devuelve el mismo run;
    - un exceso de tasa o costo da `429`;
    - turnos concurrentes dan `409`.
13. **Suite adversarial** (unidad 6): injection directa y en `untrusted_text`, acceso ajeno, principal vencido, tool caída, idioma ambiguo ES/PT.

## 14. Dependencias hacia otras unidades (interfaces que esta spec consume)

- **Unidad 2:**
  - `resolve_release(agent_ref, principal)`;
  - `release_status(release)`;
  - `get(entity@v)` para flows, agentes, decision models, políticas, interrupciones y plantillas;
  - política protegida (ADR 0009);
  - gate de release (§6.2).
  - Las plantillas exponen las variables de hecho que leen, para que el validador estático derive reclamos (§5).
  - Entidad de detección de idioma versionada en la release (§2, §4.3).
- **Unidad 3:**
  - Autorización y parámetros:
    - `authorize_agent`;
    - `authorize_subject`;
    - `bind_params`.
  - Ejecución: `execute(tool@v, args, bound_params, run_ctx, idempotency_key?) → {status, result_full, source, call_id, error?}`. Las vistas `model` y `audit` las calcula el núcleo (M7) con el `token_map` del run (rev. 13).
    - `status` en tools de lectura y `compute`: `ok|error|timeout|denied|step_up_required`.
    - `status` en escrituras: `ok|denied|uncertain|step_up_required`.
  - Definición de tool:
    - `risk_class: read|compute|write_reversible|write_irreversible|money_movement`;
    - `min_auth_level`, `max_auth_age?`;
    - `idempotent`;
    - `readback_by` (obligatorio en `write_*`);
    - `untrusted_fields[]`;
    - `confirmation_ttl`.
  - Tokens: `tokenize`, `render(text, token_map, reader)`.
  - Clasificación de campos: `FieldClassification`.
  - Atributos de reporte: la lista `reportable_attrs`.
- **Unidad 4:**
  - `append(events[])` en la transacción del turno y en las de escritura;
  - outbox de eventos salientes y su entrega (destino por definir en esa unidad);
  - esquema de `run_started` con los atributos de reporte.
- **Unidad 5:**
  - `generate(prompt_ref@v, inputs (vista model), schema?, locale)`;
  - contadores de costo por principal para los límites de §3.
- **Unidad 6:**
  - esquema de referencias por caso;
  - mínimo de muestra PT;
  - corrida de calibración de los umbrales de idioma, con subconjuntos de mensajes de 1 a 3 palabras y portuñol;
  - consume conjuntos sintéticos PT producidos por otro equipo.
- **Unidad 7:**
  - `recent_turns(run_id, n)`;
  - `transcript.append(...) → entry_id` (la huella con clave la calcula el núcleo, §8.1.1);
  - `transcript.read(run_id) → entradas en vista model`;
  - `knowledge.read/search`.
- **Emisor de asignaciones / plataforma del asesor** (fuera del núcleo):
  - consume `handoff_created`;
  - firma `on_behalf_of` al asignar;
  - es dueña de la conversación después del escalamiento.
- **Servicio de elegibilidad de crédito** (fuera del núcleo; producción): tool de decisión con veredicto firmado (ADR 0009).

## 15. Riesgos

- **JEV:** idiomas, latencia y procesamiento de datos no verificados. Mitigación: cadena de respaldo, vista `model` y prueba de humo con 50 casos ES/PT antes del miércoles. La misma prueba corre lingua sobre los mismos casos, más mensajes de 1 a 3 palabras y portuñol, midiendo precisión por largo, tasa de indeterminados y latencia p50/p95 desde nuestro entorno. Si JEV resulta claramente mejor en mensajes cortos, se evalúa como segunda opinión solo en turnos `undetermined`.
- **Detección de idioma en mensajes cortos:** según el reporte del autor (75 idiomas, sin restringir), lingua acierta 43,6 % (ES) y 59,0 % (PT) en palabras sueltas, con la confusión ES↔PT como error principal; en oraciones supera 96 %. Mitigación: las reglas `short` y `undetermined` conservan el `locale`.
- **PT sin datos reales:** toda cifra en PT es sobre conjuntos sintéticos etiquetados que produce otro equipo (ADR 0012).
- **Rigidez de flows:** se mitiga con abstención. El nodo `agent` queda para producción.
- **Intérprete propio:** 1.500–2.000 LOC. Mitigación: corte MVP, replay en CI y validación estática.
- **JSON Logic:** poco expresivo a propósito. Si se queda corto, se evalúa CEL en un ADR nuevo.
- **Tokenización:** los campos sin clasificar se tratan como `pii_direct`, lo que puede empobrecer respuestas.
- **Step-up simulado:** se etiqueta como tal en la demo.
- **Destino de eventos salientes sin definir:** el outbox desacopla, así que el destino puede cambiar sin tocar el motor.
- **Clave de huellas:** si se filtra, las huellas de PII vuelven a ser atacables por diccionario. Mitigación: gestor de secretos, acceso solo del servicio de verificación y rotación por `kid`. En la demo la clave es un secreto de entorno.
- **Replay de runs reales parcial:** el modo `audit` no re-verifica reglas, `compute` ni el validador sobre datos reales; un bug que solo aparece con datos de producción no lo detecta el replay. Mitigación: cobertura de fixtures por camino y la evolución de producción con almacén de entradas `full` (§0).
- **Reclamos de éxito no detectables estáticamente:** un texto fijo que afirma éxito sin leer hechos ni declarar `claims`, o un texto generado que lo afirma sin citarlo. Mitigación: revisión al publicar y medición en la unidad 6 (§6.1, §12).

## 16. Estado de la revisión externa (2026-09-28)

**Resueltos:**

| Hallazgo | Resolución | Dónde |
|---|---|---|
| C1 (A) | Autorización del subject y delegación firmada | §2–4, §9, §14; ADR 0006 |
| C2 (A) | Outbox e `idempotency_key = action_id` | §5, §6.1.5, §8.2; ADR 0007 |
| C3 (A) | Readback por clave | §5, §8.2; ADR 0007 |
| C4 (A) | Resultados de escritura `ok`/`denied`/`uncertain` | §5, §10; ADR 0007 |
| C5 (A + B para crédito) | `policy` protegida; crédito como servicio externo | §5, §6; ADR 0009 |
| C6 (A) | Niveles de autenticación y step-up | §2, §4.7; ADR 0010 |
| C7 (A) | Tres vistas y tokens reversibles | §8.1; ADR 0008 |
| U8 (A) | Tools `compute` y validador numérico | §8.3; ADR 0011 |
| U9 (A) | `@prod` obligatorio; revocación → escalamiento | §3, §4.1 |
| R1 (A) | PT sobre clientes MX/CO/AR | §7; ADR 0012 |
| R2 (A) | Transcript store separado | §8.4; ADR 0003 |
| R4 (A) | Outcome afirmado frente a referencias | §12 |
| R6 (A) | `untrusted_text` y modo degradado | §4.5, §8.1 |
| A1 | Corte MVP | §0 |
| I1 (A) | El núcleo no define caso; cadena por run | §1, §2, §11; ADR 0003 |
| I2 (A) | Understand con `calibrated_fields` | §4.4, §7; ADR 0005 |
| I3 + I4 (A) | Hechos como diccionario; `decide.save_as` + `compute seleccionar` | §2, §5, §8 |
| I5 + U10 | Gate de release y tope de profundidad | §6.2; ADR 0004 |
| U1 (A) | Prioridad declarada + orden de mención | §4.6; ADR 0004 |
| U2 (A) | Interrupciones declaradas en la release | §4.5; ADR 0004 |
| U3 + m1 | Invalidación de acciones; TTL y campo `confirm` | §3, §4, §8.2 |
| U5 (A) | Abandono cuenta como no resuelto | §4, §12 |
| U6 (decisión del usuario) | Escalamiento como evento saliente que cierra el run; transcript renderizado para el asesor | §4, §8.4, §9; ADR 0013 |
| U7 (A) | `await_approval` como diseño de producción | §5; ADR 0014 |
| R3 | Atributos de reporte en `run_started` | §11 |
| R5 | `Clock` inyectado y guardas registradas | §4, §11 |
| R7 (A) | `Idempotency-Key`, límites por principal, sin streaming | §3 |
| Idioma (decisión del usuario) | Respuesta en el idioma del principal, ES o PT | §4.3, §8.3; ADR 0012 |
| I6 | Tabla de umbrales por campo, valor, proveedor e idioma; combinación ausente = 1.0 | §7; ADR 0005 |
| m2 | `validated` = pasó el validator de su `collect`; lo comprobado contra un registro es un hecho | §8 |
| m3 | Contadores independientes + tope global `max_repair_turns_per_run` | §4.5 |
| m4 | Rangos solo en autoría; referencias exactas en runtime | §2; unidad 2 |
| U4 | Buzón por sesión como diseño de producción; `409` en el MVP | §10 |

**Pendientes:** ninguno de la revisión externa.

**Auto-revisión (2026-09-28):** se sigue en `TEMAS-ABIERTOS-PENDIENTES.md`.

| Tema | Resolución | Dónde |
|---|---|---|
| #7 `end(abandoned)` y outcome por modo | Outcomes declarables por modo; `abandoned`/`escalated` solo del motor; regla 6.1.14; chequeo de modo en el gate; `end(failed)` como salida de fallo en modo task | §2, §5, §6.1, §6.2, §13.1 |
| #8 Dos ADR 0009 | El ADR de conocimiento pasa a 0015; 0009 queda para políticas protegidas | cabecera; ADR 0015 |
| #9 `untrusted_text` fuera de ADR 0008 | ADR 0008 enmendado con la clase y su envoltura | ADR 0008 |
| #4 `sha256` sin clave sobre PII (decisión del usuario) | HMAC-SHA256 con clave global rotada por `kid`, serialización JCS; aplica a vista `audit` y transcript; cadena de eventos sigue en `sha256`; clave por subject como diseño de producción | §0, §8.1, §8.1.1, §8.4, §11, §13.6, §14, §15; ADR 0008, ADR 0003 |
| #6 `unclear` de `confirm` sin tope (decisión del usuario) | `max_attempts` en `confirm` (2) con resultado propio y suma al tope global; reentrada idempotente (una sola acción `proposed` por `confirm`); con `confirm` pendiente, `out_of_scope`/`clarify`/bajo umbral dan `unclear`; botón nunca da `unclear` | §4.4, §4.5, §5, §8, §8.2, §13.11, §16; ADR 0007 |
| #5 Detector de idioma sin especificar (decisión del usuario) | lingua-py local fijado en la release; limpieza, `short`, candidatos cerrados, `undetermined`, histéresis, no soportado con umbral y largo mínimo, umbrales por calibración (1.0 si faltan); mismo detector en el validador; comparación con JEV en la prueba de humo | §2, §4.3, §8.3, §11, §13.8, §14, §15; ADR 0012, ADR 0005 |
| #3 Alcance del replay (decisión del usuario) | Dos modos: `fixture` (CI, datos sintéticos, recálculo completo) y `audit` (runs reales, integridad + transiciones); almacén `full` como diseño de producción | §0, §11, §13.2, §15; ADR 0003 |
| #2 `reauth` después de Understand (decisión del usuario) | Credenciales rechazadas con `401`/`403` antes del turno; `reauth` deja de ser manejador; `principal_mismatch`; límites por principal después de la firma | §3, §4.1, §4.5, §10, §11, §13.5, §16; ADR 0006, ADR 0010 |
| #1 `claims_success` sin declarar (decisión del usuario) | `claims: [confirm]` en `respond`, más reclamos derivados de los hechos que lee; invariante por camino hasta `verified`; definición de `respond` seguro | §2, §5, §6.1.5, §6.1.6, §6.1.13, §8.2, §11, §13.3, §14, §15; ADR 0007 |
