# M9 — Acceso y API

- Estado: borrador · Fase 3
- Paquete: `agent_core.api`
- Origen: spec general §3, §4.1 (firma, vigencia, coincidencia, límites), §4.2, §4.7 (respuesta de step-up), §10, §13.4, §13.5, §13.12
- ADRs: 0006 (principal, delegación, `principal_mismatch`), 0010 (niveles, step-up, credenciales antes del turno), 0002 (`/v1`, contratos)
- Usa: M0, M4, M10, M11 · Lo usan: apps (chat, consola, constructor)

## 1. Propósito y límites

Es la puerta del motor: expone `/v1` con FastAPI, valida credenciales **antes** de tocar el estado, aplica límites por principal, autoriza agente y subject, maneja `Idempotency-Key` y traduce errores a `application/problem+json`.

**No hace:** lógica de conversación (M4), emitir identidades (servicio de identidad; en la demo, `TestIdentityIssuer`), ni la autorización por tool (unidad 3, dentro de `execute`).

## 2. Interfaz pública

Rutas (contrato en `contracts/openapi.json`):

| Método y ruta | Handler | Delegado |
|---|---|---|
| `POST /v1/runs` | `create_run` | M4 `start_run` |
| `POST /v1/sessions/{session_id}/turns` | `post_turn` | M4 `handle_turn` |
| `GET /v1/runs/{run_id}` | `get_run` | lectura de estado resumido, sujeta a política |
| `GET /v1/runs/{run_id}/transcript` | `get_transcript` | M11 + renderer de M7 |
| `GET /v1/handoffs/{handoff_ref}` | `get_handoff` | M10 |
| `POST /v1/handoffs/{handoff_ref}/resolution` | `post_resolution` | M10 |

Todas las respuestas llevan `trace_id`.

```python
class AccessGate:
    def admit(self, raw_auth: str, raw_delegation: str | None, run_snapshot: RunState | None) -> Admitted
    # Admitted: {principal, on_behalf_of}; lanza EngineError con el ProblemCode correspondiente
```

## 3. Comportamiento

### 3.1 Orden de chequeos (antes de cargar el run)

| # | Chequeo | Fallo | Registro |
|---|---|---|---|
| 0 | Límites por IP/canal (gateway, fuera del motor) | 429 | — |
| 1 | Firma del principal y de `on_behalf_of` (`IdentityVerifier`) | `401 credentials_invalid` | solo log de seguridad; **nada** en la cadena del run |
| 2 | Vigencia con el `Clock`: principal vencido | `401 principal_expired` | `access_denied` |
| 3 | Delegación vencida o `grant_ref` revocado | `403 delegation_expired` | `access_denied` |
| 3b | `on_behalf_of.grantee` distinto de `principal.key` (ADR 0006, M0 rev. 2) | `403 delegation_mismatch` | `access_denied` |
| 4 | Run existente: `(type, id)` distinto del snapshot | `403 principal_mismatch` | `access_denied` |
| 5 | Tasa y costo diario **por principal ya validado** (`CostCounters`) | `429 rate_limited` / `cost_budget_exceeded` | — |

Un rechazo en 1–5 **no procesa el turno**: sin Understand, modelos, tools ni transcript. La app renueva y reintenta con el mismo `client_turn_id`.

Para 2–4, `access_denied` se escribe en la cadena del run cuando existe; M9 pide a M11 un append mínimo fuera del turno.

### 3.2 Autorización (§4.2)

- `authorize_agent`: `principal.type ∈ invocable_by`, `subject.kind ∈ subject_kinds`, `auth.level ≥ min_auth_level`. Fallo → `403 agent_forbidden` + `access_denied`.
- `authorize_subject` según la tabla de ADR 0006:
  - `customer` con sesión: subject derivado de `principal.id` (se ignora el `subject` del body si difiere → `403`);
  - `customer` anónimo: sin subject de datos personales;
  - `advisor`: solo `on_behalf_of.subject` con grant vigente;
  - `service` y `builder`: por scopes.
- `bind_params` calcula los parámetros vinculados; nunca salen del body ni del modelo.
- Se evalúa al crear el run y en cada turno; la unidad 3 lo repite en cada tool.
- Fallo → `403 subject_forbidden` + `access_denied`.

### 3.3 Versión e idempotencia

- Solo `service` y `builder` pueden pedir `@version` o un alias distinto de `@prod`; si no, `403 version_pin_forbidden`.
- `POST /v1/runs` con `Idempotency-Key` ya vista (mismo principal) devuelve el run creado, con el mismo cuerpo. Tabla `run_idempotency(principal, key, body_hash, response)`. Misma clave con otro body → `409 idempotency_conflict`.
- Recursos inexistentes → `404 not_found`; body inválido → `422 invalid_request`; `DomainError` inesperado → `500 internal_error` sin detalle interno.

### 3.4 Step-up en la respuesta

Cuando M4 devuelve `awaiting: step_up`, M9 responde `200` con `step_up: {required_level, reason}`. En la demo, `TestIdentityIssuer` emite un principal elevado tras un OTP **simulado**, marcado como tal en la respuesta y en la UI.

### 3.5 Errores

`EngineError(code)` → `application/problem+json {type, title, status, code, detail, trace_id}`. Los códigos son los de M0 §2.7.

## 4. Invariantes

- Ningún byte del mensaje de un turno rechazado en 3.1 llega a un modelo, una tool o el transcript.
- Un exceso de tasa con firma inválida no consume la cuota del principal suplantado.
- Ningún parámetro sensible sale del body o del modelo.

## 5. Fallas

Todas las filas de credenciales, subject, `409`, `410` y `429` de §10 de la spec general. `409` y `410` los detecta M4 y M9 solo los traduce.

## 6. Eventos que emite

`access_denied {reason: principal_expired | delegation_expired | delegation_mismatch | principal_mismatch | subject_forbidden | agent_forbidden}`. Las firmas inválidas, y cualquier rechazo sin run existente, van solo al log de seguridad.

## 7. Pruebas

Con `TestClient` de FastAPI, `TestIdentityIssuer` y `FakeClock`.

| ID | Caso | §13 |
|---|---|---|
| T-M9-01 | Subject ajeno → `403 subject_forbidden` | 4 |
| T-M9-02 | Asesor sin delegación o con una vencida → `403` | 4 |
| T-M9-03 | Un `customer_id` en el body no sustituye al de `principal.id` | 4 |
| T-M9-04 | `customer`/`advisor` fijando `@version` → `403 version_pin_forbidden` | 4 |
| T-M9-05 | Anónimo no accede a datos personales | 5 |
| T-M9-06 | Firma inválida → `401`, sin llamadas a modelos, tools ni transcript, sin eventos en la cadena | 5 |
| T-M9-07 | Principal vencido → `401`; reintento renovado con el mismo `client_turn_id` se procesa una vez | 5 |
| T-M9-08 | Delegación revocada → `403 delegation_expired` | 5 |
| T-M9-09 | Otro asesor con delegación vigente sobre el mismo subject → `403 principal_mismatch` | 5 |
| T-M9-10 | Exceso de tasa con firma inválida no consume la cuota del suplantado | 5 |
| T-M9-11 | `Idempotency-Key` repetida devuelve el mismo run | 12 |
| T-M9-12 | Exceso de tasa o costo → `429` | 12 |
| T-M9-13 | Todas las respuestas llevan `trace_id` y los errores son `problem+json` | — |
| T-M9-14 | `awaiting: step_up` devuelve `step_up` en el cuerpo | 5 |

## 8. Evaluación

Intentos de acceso no autorizado (`access_denied` por motivo), tasa de `401`/`403`/`429`, latencia de la puerta (p95).

## 9. Puntos de iteración

- OTP real o biometría: cambia `IdentityVerifier`, no el motor.
- Buzón de ráfagas: `202` en vez de `409`, detrás de la misma ruta.
- Streaming: fuera de alcance; cambiaría el contrato (versión mayor).

## 10. Definición de terminado

- Rutas y `AccessGate` con T-M9-01…14 en verde.
- `openapi.json` generado en `contracts/` y usado por las apps.
- `TestIdentityIssuer` con script para emitir principales de la demo (cliente, asesor con delegación, anónimo, vencido).

## 11. Abiertos

- `GET /v1/runs/{run_id}`: la spec no define qué campos devuelve ni la política de lectura. Propuesta: `{status, outcome, locale, awaiting, handoff_ref}` para el propio principal y para un asesor con delegación.
