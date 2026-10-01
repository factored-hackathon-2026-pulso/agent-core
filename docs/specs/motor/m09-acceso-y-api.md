# M9 — Acceso y API

- Estado: rev. 2 (2026-09-29) · Fase 3 · implementado con dobles y verificado sobre Postgres · formato de `raw_credential` decidido (JWS Ed25519)
- Paquete: `agent_core.api`
- Origen: spec general §3, §4.1 (firma, vigencia, coincidencia, límites), §4.2, §4.7 (respuesta de step-up), §10, §13.4, §13.5, §13.12
- ADRs: 0006 (principal, delegación, `principal_mismatch`), 0010 (niveles, step-up, credenciales antes del turno), 0002 (`/v1`, contratos), 0003 (OpenTelemetry), 0007 (idempotencia de escrituras; el `Idempotency-Key` de runs es de esta spec)
- Usa: M0, M4, M10, M11 · Lo usan: apps (chat, consola, constructor)

## 1. Propósito y límites

Es la puerta del motor: expone `/v1` con FastAPI, valida credenciales **antes** de tocar el estado, aplica límites por principal, autoriza agente y subject, maneja `Idempotency-Key` y traduce errores a `application/problem+json`.

**No hace:** lógica de conversación (M4), emitir identidades (servicio de identidad; en la demo, `TestIdentityIssuer`), la autorización por tool (unidad 3, dentro de `execute`), ni el límite por IP/canal (gateway, chequeo 0).

**Fronteras.** `agent_core.api` solo importa `domain`, `ports` y `agent_telemetry`. Lo que necesita de M4, M10 y M11 lo declara como `Protocol` en `api/protocols.py` (`TurnService`, `HandoffReader`, `TranscriptService`, `DenialRecorder`, `SecurityLog`); `TurnEngine`, `HandoffService`, `TranscriptReader` y `AuditLog` los cumplen estructuralmente y los conecta el cableado (`agent_core.composition`/`cli`). Así el contrato `api` de `.importlinter` no arrastra imports indirectos y no necesitó cambios.

## 2. Interfaz pública

Rutas (contrato generado en `contracts/openapi.json` por `agentcore contracts`):

| Método y ruta | Handler | Delegado |
|---|---|---|
| `POST /v1/runs` | `create_run` | M4 `start_run` (201) |
| `POST /v1/sessions/{session_id}/turns` | `post_turn` | M4 `handle_turn` |
| `GET /v1/runs/{run_id}` | `get_run` | lectura de estado resumido, sujeta a política (§3.6) |
| `GET /v1/sessions/{session_id}/lineage` | `get_session_lineage` | linaje de la sesión: runs en orden con release y origen de transferencia (§3.8, ADR 0021) |
| `GET /v1/runs/{run_id}/transcript` | `get_transcript` | M11 `TranscriptReader` (el renderer de M7 va dentro) |
| `GET /v1/handoffs/{handoff_ref}` | `get_handoff` | M10 `get` |
| `POST /v1/handoffs/{handoff_ref}/resolution` | `post_resolution` | M10 `record_resolution` |

Cabeceras: `Authorization` (principal firmado, con o sin el esquema `Bearer`), `X-On-Behalf-Of` (delegación firmada, solo asesores) e `Idempotency-Key` (obligatoria en `POST /v1/runs`, 1 a 255 caracteres imprimibles). Todas las respuestas llevan `trace_id`.

```python
class AccessGate:
    def admit(self, raw_auth: str | None, raw_delegation: str | None,
              load_run: Callable[[], RunState | None], *, trace_id: str) -> Admitted
    # Admitted: {principal, on_behalf_of, run}; lanza EngineError con el ProblemCode correspondiente

class RunAuthorizer:   # §3.2, §3.3
    def authorize_new_run(self, admitted, run_input, *, trace_id) -> RunInput   # con el subject derivado
    def authorize_existing_run(self, admitted, run, *, trace_id) -> None        # cada turno
    def authorize_read(self, admitted, run, *, trace_id) -> None                # GET run y transcript

class LimitGuard:      # §3.1 chequeo 5
    def check(self, principal) -> None

create_app(deps: ApiDeps) -> FastAPI   # ApiDeps agrupa puertos, servicios inyectados y RateLimitConfig
```

`admit` recibe `load_run` perezoso (en vez del snapshot ya cargado): el estado no se toca hasta que la firma es válida, y `access_denied` sí puede escribirse en la cadena cuando el run existe.

## 3. Comportamiento

### 3.1 Orden de chequeos (antes de cargar el run)

| # | Chequeo | Fallo | Registro |
|---|---|---|---|
| 0 | Límites por IP/canal (gateway, fuera del motor) | 429 | — |
| 1 | Firma del principal y de `on_behalf_of` (`IdentityVerifier`) | `401 credentials_invalid` | solo log de seguridad; **nada** en la cadena del run |
| 2 | Vigencia con el `Clock`: `principal.exp <= now` | `401 principal_expired` | `access_denied` |
| 3 | Delegación vencida (`exp <= now`) o `grant_ref` revocado | `403 delegation_expired` | `access_denied` |
| 3b | `on_behalf_of.grantee` distinto de `principal.key` (ADR 0006, M0 rev. 2) | `403 delegation_mismatch` | `access_denied` |
| 4 | Run existente: identidad distinta del snapshot | `403 principal_mismatch` | `access_denied` |
| 5 | Tasa y costo diario **por principal ya validado** (`CostCounters`) | `429 rate_limited` / `cost_budget_exceeded` | — |

Un rechazo en 1–5 **no procesa el turno**: sin Understand, modelos, tools ni transcript. La app renueva y reintenta con el mismo `client_turn_id`.

- **Credencial ausente, vacía (también `Bearer` sin token) o sin firma válida**, y un anónimo sin sesión firmada: `credentials_invalid`. Falla cerrado.
- **Registro.** Todo rechazo va al log de seguridad (`SecurityLog`; implementación `OtelSecurityLog`: evento en el span activo y línea de log, con motivo, tipo de principal y `trace_id`; nunca la credencial, el `principal.id` ni el body). Para 2–4 (y `subject_forbidden`/`agent_forbidden` de §3.2), si el run existe, M9 pide a M11 un append mínimo fuera del turno (`AuditLog.append_standalone`) con `access_denied`. Sin run (p. ej. `POST /v1/runs`) o con firma inválida, solo log de seguridad. Si la escritura en la cadena falla, la denegación se mantiene y se registra `audit_write_failed`.
- **Identidad de un principal anónimo** (decisión 2026-09-29). Un anónimo no tiene `id`, así que `(type, id)` no lo distingue: su identidad es `attrs["anon_session"]`, un id de sesión firmado en la credencial. El chequeo 4 lo compara además de `(type, id)`. Sin él, cualquier anónimo pasaría el chequeo sobre el run de otro.
- **`principal_mismatch` con lectura previa.** Comparar con el snapshot exige leer el run. Se lee (solo lectura, sin lease) después de validar la firma y antes de cargar el turno en M4; ninguna otra lectura ni escritura precede a la firma.
- **Límites** (`RateLimitConfig`, valores de demo ajustables): 30 turnos por ventana de 60 s y USD 5.00 por día UTC, por principal. Solo cuenta lo que M4 registra con `add_usage`: un rechazo no consume cuota, así que un exceso con firma inválida no toca la del principal suplantado. Un principal anónimo no se cuenta aquí (sus contadores serían los de todos los anónimos); su límite es el del gateway.

### 3.2 Autorización (§4.2)

- El subject **nunca sale del body ni del modelo**: lo deriva el servidor.
  - `customer` con sesión: `SubjectRef(customer, principal.id)` (si el agente declara `subject_kinds`); un `subject` del body distinto → `403 subject_forbidden`.
  - `customer` anónimo: sin subject; uno en el body → `403 subject_forbidden`.
  - `advisor`: `on_behalf_of.subject`; sin delegación (con un agente que pide subject) o con otro subject en el body → `403 subject_forbidden`.
  - `service` y `builder`: el del body, autorizado por scopes.
- `authorize_agent`: `principal.type ∈ invocable_by`, `subject.kind ∈ subject_kinds` (un agente con `subject_kinds` no acepta un subject ausente) y `auth.level ≥ min_auth_level`. Fallo → `403 agent_forbidden` con la razón del `AuthzPort` en `detail`.
- `authorize_subject` según la tabla de ADR 0006 (vive en el `AuthzPort`). Fallo → `403 subject_forbidden`.
- Orden: agente y luego subject. Se evalúa al crear el run y en cada turno (contra `run.agent` y `run.subject`); la unidad 3 lo repite en cada tool. En un run existente, el rechazo queda como `access_denied`.
- Un agente o alias inexistente es `404 not_found` (el puerto de registro no define su excepción: se captura `LookupError`).
- `bind_params` **no tiene consumidor en M9**: ni `RunInput` ni `RunState` llevan parámetros vinculados; los usa M2/unidad 3 al ejecutar tools. Lo que sí garantiza M9 (T-M9-03) es que nada del body sustituye al principal: el body rechaza campos desconocidos (`customer_id` → `422`).
- Un agente cuyo `subject_kinds` esté vacío no recibe subject.
- **`customer` ↔ subject `customer` es por diseño**, no un residuo del dominio: `PrincipalType.customer` significa "el titular de su propio registro" (ADR 0006, tabla de subjects). Un agente sobre otro tipo de sujeto usa `advisor`, `service` o `builder`, cuyo subject sale de la delegación o del scope.
- **Contrato de `AuthzPort` (ADR 0019).** Toda implementación, la de la unidad 3 incluida, pasa `tests/contracts/test_authz_contract.py`:
  - un `builder` no obtiene subject de tipo `customer`, ni campos (`can_read_field` es siempre falso, con o sin concesión), ni un `subject_ref` en `bind_params`;
  - un `advisor` solo actúa sobre el subject de su delegación y solo si es el `grantee`; sus parámetros vinculados salen de la delegación, no del subject pedido;
  - un rechazo nunca incluye la referencia del subject en su motivo.
- **Copiloto del asesor (ADR 0019).** Es un agente `conversational` con `invocable_by: [advisor]`. Cada run es propio del asesor y arranca con su delegación; el transcript del cliente lo lee por `authorize_read` (delegación vigente sobre el subject), con los campos que la política permita por `purpose`.

### 3.3 Versión e idempotencia

- Solo `service` y `builder` pueden pedir `@version` o un alias distinto de `@prod`; si no, `403 version_pin_forbidden` (sin `access_denied`: no hay run).
- `POST /v1/runs` con `Idempotency-Key` ya vista (mismo principal) devuelve el run creado, con el mismo cuerpo. Misma clave con otro body → `409 idempotency_conflict`. Recursos inexistentes → `404 not_found`; body inválido → `422 invalid_request`; error inesperado → `500 internal_error` sin detalle interno.
- **Dónde vive.** En M4 (`start_run`, cambio pedido por M9 el 2026-09-29; ver m04): busca `(principal.key, key)` y compara el hash JCS del `RunInput` sin la clave; el registro (`put_run_idempotency`) va en la **misma transacción** que el run, así que no hay run sin clave ni clave sin run.
- **Anónimos.** Comparten `PrincipalKey(customer, None)`: M9 antepone `"{anon_session}:"` a la clave para que la de un anónimo nunca devuelva el run de otro.
- **Límite conocido.** Dos requests concurrentes con la misma clave pueden crear dos runs antes de que exista el registro (gana el primero en commitear; el otro run queda huérfano). Cerrarlo exige un candado por clave en el adaptador de Postgres.
- El resultado guardado incluye el `confirmation.token` en claro si el primer turno pide confirmación (M3 rev. 2 solo guarda su hash): riesgo aceptado para la demo, a revisar en producción.

### 3.4 Step-up en la respuesta

Cuando M4 devuelve `awaiting: step_up`, M9 responde `200` con `step_up: {required_level, reason, simulated}`. `simulated` es `true` mientras `ApiDeps.step_up_simulated` lo sea (por defecto, la demo). En la demo, `TestIdentityIssuer` emite un principal elevado tras un OTP **simulado**, marcado como tal en la credencial (`auth.simulated`), en la respuesta y en la UI. La respuesta publica `confirmation` como `{action_summary, token, expires_at}` (sin `action_id`) y agrega `run_id` y `turn_id`.

### 3.5 Errores

`EngineError(code)` → `application/problem+json {type, title, status, code, detail, trace_id}`. Los códigos son los de M0 §2.7; `type` es `urn:agentcore:problem:<code>`.

- El `detail` nunca trae el texto de una excepción inesperada, el de `CredentialsInvalid` ni el valor de un campo inválido (`422` lista solo dónde y qué regla falló).
- Los errores del propio framework (ruta inexistente, método no permitido) conservan su status HTTP; una ruta inexistente es `not_found`.
- El body entra por `agent_core.domain.loads` (los decimales llegan como `Decimal`; `NaN`, claves duplicadas y JSON roto son `422`) y sale por `dumps`.
- `TurnInProgress` y `VersionConflict` de la UoW no tienen mapeo propio: M4 ya traduce el primero a `EngineError(turn_in_progress)`; cualquier otro `DomainError` es `500`.
- El límite de tamaño de un mensaje lo aplica M4 con la plantilla `input_too_large` (`Release.max_input_chars`), no M9.

### 3.6 Lecturas: `GET /v1/runs/{run_id}` y transcript

- `GET /v1/runs/{run_id}` devuelve `{run_id, status, outcome, locale, awaiting, handoff_ref, trace_id}` (propuesta del "Abierto" original, aprobada).
- Política de lectura (`authorize_read`): el dueño del run (misma identidad que en el chequeo 4), o quien el `AuthzPort` autorice sobre `run.subject` (asesor con delegación vigente, `service` por scope). **Un run sin subject solo lo lee su dueño** (`authorize_subject(…, None)` permite a cualquiera, así que no se consulta). Un rechazo es `403 subject_forbidden` y queda como `access_denied` en la cadena del run.
- El transcript autoriza el run primero y solo entonces llama al lector, que autoriza por campo (`can_read_field`). Un run inexistente es `404`.
- Los handoffs no pasan por `principal_mismatch` (el asesor no es el dueño del run): M10 autoriza el subject.

### 3.8 Linaje de la sesión: `GET /v1/sessions/{session_id}/lineage` (ADR 0021, T-TR-10)

- Respuesta: `{session_id, runs: [{run_id, agent, release, status, outcome, origin}], trace_id}`, con los runs en orden de creación (`list_runs_by_session`). `origin` es `null` o `{transfer_id, from_run_id, from_agent, from_release_id}`.
- **No se publica `from_event_hash`** (dato de integridad del enlace, lo usa `verify_transfer_link`) ni principal, subject, slots ni hechos: no hay PII en la respuesta.
- Admisión como `post_turn`: pasa por la puerta con la sesión, así que una sesión inexistente es `404 not_found` solo después de una firma válida, y otro principal (incluido un asesor) recibe `403 principal_mismatch`. Además se aplica `authorize_read` a cada run de la sesión; un rechazo es `403 subject_forbidden` y queda como `access_denied`. Las lecturas por run (`GET /v1/runs/{id}`) siguen disponibles para asesores con delegación.
- Funciona también con la sesión cerrada (`find_run_by_session` devuelve el más reciente si no hay uno abierto).
- Cambia `contracts/openapi.json` (regenerado); no cambia `SCHEMA_VERSION`.

### 3.7 Observabilidad (ADR 0003)

Un span `agentcore.api.request` por request (OpenTelemetry, provider de `agent_telemetry`), con una lista cerrada de atributos (método y status). El `trace_id` de la respuesta es el de la traza si hay una activa; si no, uno del `IdSource`. `agent_telemetry.span()` exige `run_id` y `agentcore.release`, que no existen en un 401, por eso la puerta usa la API de OTel directamente. Nada de credenciales, `principal.id` ni body en atributos.

### 3.8 Formato de la credencial (`raw_credential`, decisión 2026-09-29)

JWS compacto `header.payload.firma` (base64url sin relleno), firmado con Ed25519. Lo verifica `JwsIdentityVerifier` (`agent_core/adapters/jws_identity.py`), que implementa `IdentityVerifier`; lo emite el servicio de identidad y, en la demo, `TestIdentityIssuer`.

- **Header exacto** `{"alg": "EdDSA", "kid", "typ"}`; cualquier otro campo o `alg` (incluidos `none` y `HS256`) se rechaza. `typ` separa `principal+jws` de `delegation+jws`, y cada uno se verifica con su propio juego de claves: la delegación la firma el emisor de asignaciones, no el de principales (ADR 0006).
- **Payload:** el `Principal` o el `OnBehalfOf` serializado con `dumps`, leído con `loads` y validado por el modelo (campos extra, fechas sin zona o un anónimo con `id` se rechazan). Sin secretos. Un anónimo lleva `attrs["anon_session"]`.
- **Rotación:** las claves se indexan por `kid`; rotar es publicar la nueva y retirar la vieja.
- **Solo firma:** el verificador no mira `exp` (el puerto lo dice): la vigencia es de M9 con el `Clock`, nunca de la librería.
- **Fallo cerrado:** cualquier duda es `CredentialsInvalid("credencial inválida")`, sin la credencial en el mensaje. Tope de 8192 caracteres. `grant_active` devuelve `False` si el servicio de asignaciones falla.
- **Cabecera:** `Authorization: Bearer <jws>` (también se acepta sin `Bearer`); la delegación va sin esquema en `X-On-Behalf-Of`.
- **Demo:** `TestIdentityIssuer` (`testing/fakes/identity.py`) firma con claves de PRUEBA derivadas de una semilla fija y pública (`kid` `test-*`), nunca para producción; con el mismo `Clock` emite los mismos tokens. `uv run python -m testing.demo_identities` imprime el cliente, el asesor con su delegación, el anónimo, el vencido y el elevado (OTP simulado, `auth.simulated`).

## 4. Invariantes

- Ningún byte del mensaje de un turno rechazado en 3.1 llega a un modelo, una tool o el transcript.
- Una firma inválida no lee ni escribe el estado, y no deja nada en la cadena de ningún run.
- Un exceso de tasa con firma inválida no consume la cuota del principal suplantado.
- Ningún parámetro sensible sale del body o del modelo.
- La credencial cruda nunca se registra, ni aparece en errores, logs, spans ni eventos.

## 5. Fallas

Todas las filas de credenciales, subject, `409`, `410` y `429` de §10 de la spec general. `409 turn_in_progress` y `410 run_closed` los detecta M4 y M9 solo los traduce; `409 idempotency_conflict` también viene de M4.

## 6. Eventos que emite

`access_denied {reason: principal_expired | delegation_expired | delegation_mismatch | principal_mismatch | subject_forbidden | agent_forbidden}`. Las firmas inválidas, `version_pin_forbidden` y cualquier rechazo sin run existente van solo al log de seguridad.

## 7. Pruebas

Con `TestClient` de FastAPI, `StubVerifier` (tokens opacos sintéticos), `TableAuthz` y `FakeClock`; las de idempotencia y las de integración usan el `TurnEngine` real.

| ID | Caso | §13 | Pruebas |
|---|---|---|---|
| T-M9-01 | Subject ajeno → `403 subject_forbidden` | 4 | `test_api`, `test_authorization`, `test_m9_postgres` |
| T-M9-02 | Asesor sin delegación o con una vencida → `403` | 4 | `test_api`, `test_gate`, `test_authorization` |
| T-M9-03 | Un `customer_id` en el body no sustituye al de `principal.id` | 4 | `test_api` |
| T-M9-04 | `customer`/`advisor` fijando `@version` → `403 version_pin_forbidden` | 4 | `test_api`, `test_authorization` |
| T-M9-05 | Anónimo no accede a datos personales | 5 | `test_api`, `test_authorization` |
| T-M9-06 | Firma inválida → `401`, sin llamadas a modelos, tools ni transcript, sin eventos en la cadena | 5 | `test_api` (las 6 rutas), `test_gate` |
| T-M9-07 | Principal vencido → `401`; reintento renovado con el mismo `client_turn_id` se procesa una vez | 5 | `test_api`, `test_gate` |
| T-M9-08 | Delegación revocada → `403 delegation_expired` | 5 | `test_api`, `test_gate` |
| T-M9-09 | Otro asesor con delegación vigente sobre el mismo subject → `403 principal_mismatch` | 5 | `test_api`, `test_gate`, `test_m9_postgres` |
| T-M9-10 | Exceso de tasa con firma inválida no consume la cuota del suplantado | 5 | `test_api` |
| T-M9-11 | `Idempotency-Key` repetida devuelve el mismo run | 12 | `test_idempotency`, `test_m9_postgres`, `tests/m04/test_start_run_idempotency` |
| T-M9-12 | Exceso de tasa o costo → `429` | 12 | `test_api`, `test_limits`, `test_m9_postgres` |
| T-M9-13 | Todas las respuestas llevan `trace_id` y los errores son `problem+json` | — | `test_problems`, `test_api` |
| T-M9-14 | `awaiting: step_up` devuelve `step_up` en el cuerpo | 5 | `test_api` |
| T-TR-10 | `GET /v1/sessions/{id}/lineage` devuelve la cadena con releases y `transfer_id`; otro cliente recibe `403`; no expone `from_event_hash` | — | `test_session_lineage` |

Además: IDOR de lecturas (dueño, asesor con delegación, run sin subject, anónimos entre sí), decimales en el body, JSON ambiguo, `openapi.json` al día, fixture `TableAuthz`.

## 8. Evaluación

Intentos de acceso no autorizado (`access_denied` por motivo), tasa de `401`/`403`/`429`, latencia de la puerta (p95).

## 9. Puntos de iteración

- OTP real o biometría: cambia `IdentityVerifier`, no el motor.
- Buzón de ráfagas: `202` en vez de `409`, detrás de la misma ruta.
- Streaming: fuera de alcance; cambiaría el contrato (versión mayor).
- Candado por clave para la idempotencia concurrente (§3.3) y `token` de confirmación fuera del resultado guardado.
- Límite por sesión anónima (hoy, del gateway).

## 10. Definición de terminado

- [x] Rutas y `AccessGate` con T-M9-01…14 en verde (`tests/m09`, 176 pruebas; integración con Postgres en `tests/integration/test_m9_postgres.py`).
- [x] `openapi.json` generado en `contracts/` por `agentcore contracts` (con `--check`). «Usado por las apps»: pendiente de las apps.
- [x] `TestIdentityIssuer` con script para emitir principales de la demo (cliente, asesor con delegación, anónimo, vencido, elevado): `python -m testing.demo_identities`. Las pruebas de la API usan `StubVerifier` (tokens opacos) y, de punta a punta, el verificador real (`test_identity_e2e`).

Fuera de la definición pero hecho: `TableAuthz` (`testing/fakes/authz.py`) como `AuthzPort` de prueba; idempotencia de `start_run` en M4.

## 11. Abiertos

- **Contrato `api` de `.importlinter`** (decisión del usuario). Con los `Protocol` de `api/protocols.py` el contrato pasa sin `allow_indirect_imports`; importar `TurnEngine`, `HandoffService` o `TranscriptReader` directamente sí lo exigiría.
- ~~**Cableado real.**~~ **Resuelto 2026-09-30 (tema #13):** `agentcore serve` (`agent_core/composition/serve.py`, `serve_ports.py`) arma `ApiDeps` con el motor real (`build_engine`: `TurnEngine`, `HandoffService`, `TranscriptReader`), `AuditLog` y `OtelSecurityLog`. Claves públicas de identidad: archivo YAML/JSON `{principal_keys: {kid: b64url}, delegation_keys: {kid: b64url}}` (32 bytes Ed25519 por clave; `--identity-keys`). Las piezas de las unidades 3, 6 y 7 son dobles de demo tras `AGENTCORE_ALLOW_DEMO=1`.
- **Copiloto y constructor (ADR 0019):** el vocabulario de `purpose` para el copiloto y los scopes del `builder` siguen sin definir; la prueba de contrato fija solo las negaciones.
- **`AuthzPort` sin especificar:** qué significa `subject=None` en `authorize_agent`, las claves de `bind_params`, el vocabulario de `purpose` y los scopes de `service`/`builder` (`TableAuthz` usa `subject:<kind>`/`subject:*` como convención propia del doble).
- **Anónimos y contadores:** sin límite por sesión anónima en el motor.
- **Resolución de handoff:** la respuesta `{handoff_ref, resolution_code, handoff_quality, trace_id}` es propuesta de M9; la spec general no define su cuerpo.
- **Hash del directorio en el linaje (pendiente, ADR 0021):** la spec de transferencia (§5.3, T-TR-10) pide que el linaje incluya el hash del directorio, pero `RunState`/`RunOrigin` no lo guardan (vive en el evento `run_transferred` de la cadena del run origen) y la respuesta acordada en el plan no lo incluye. Opciones: (a) leer el evento `run_transferred` de la cadena de auditoría del run origen mediante un puerto de lectura inyectado en `ApiDeps` (no existe hoy uno para eventos); (b) añadir `directory_hash` opcional a `RunOrigin` (cambio de M0: regenerar `contracts/`); (c) retirar el hash de §5.3/T-TR-10 y dejarlo solo en el replay. Decisión del usuario.
