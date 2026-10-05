# `agentcore serve`: contrato de entorno

Configuración **solo por variables de entorno** (más cuatro rutas de archivos que se pasan por argumento o por
variable). `serve` valida todo antes de abrir el puerto; si algo requerido falta o es inválido, imprime cada
problema (nombra la variable, nunca su valor) y sale con código 2. Ningún valor se imprime en logs.

Leyenda: **R** requerida en producción · **C** requerida si se usa la función · **O** opcional ·
**S** secreto (inyectar como secreto, nunca en claro).

## 1. Núcleo

| Variable | Tipo | Defecto | Descripción |
|---|---|---|---|
| `AGENTCORE_REGISTRY_DSN` | R, S | — | DSN de la base principal (motor, auditoría y registry). `--dsn` existe pero deja la clave en la lista de procesos. |
| `AGENTCORE_KEYS_FINGERPRINT` | R, S | — | `kid:base64[,kid:base64]`; la primera es la vigente. ≥32 bytes por clave, base64 estricto. Huella de campos. |
| `AGENTCORE_KEYS_TOKEN_MAP` | R, S | — | Mismo formato. Mapa de tokens de vistas. Rotar = añadir la nueva delante y conservar la vieja. |
| `AGENTCORE_JEV_API_KEY` | R, S | — | Clave de JEV. En producción `serve` no arranca sin ella; con dobles (demo) no se exige. |
| `AGENTCORE_SERVE_AGENTS` | O | vacío | Agentes (coma) cuya release `prod` se revisa al arrancar; solo avisa. `--agents` equivale. |
| `AGENTCORE_GIT_SHA` | O | — | Commit de la imagen; lo informa `GET /version`. El Dockerfile lo fija con `--build-arg GIT_SHA`. |
| `AGENTCORE_DB_POOL_MAX` | O | `0` | Conexiones máximas por proceso (0 = una por operación). Con un proxy de BD: tareas × este valor < límite del proxy. |
| `AGENTCORE_LANG_THRESHOLDS` | O | — | Ruta de un JSON `{thresholds_from: {switch_threshold, unsupported_threshold, min_distance}}`. Sin él el idioma nunca cambia por detección. |
| `AGENTCORE_FX_RATES_FILE` | C | — | Ruta de un JSON `{"USD":"1","MXN":"0.055"}` para `convertir_moneda`. Sin él la tool falla cerrada. |

Archivos y API del registry (cada uno tiene su variable y su argumento equivalente; el argumento gana):

| Variable | Argumento | Tipo | Descripción |
|---|---|---|---|
| `AGENTCORE_IDENTITY_KEYS_FILE` | `--identity-keys` | R | Ruta del archivo de claves públicas de identidad (§6). |
| `AGENTCORE_REGISTRY_API` | `--registry-api` | O | `1` monta la API del registry (`/v1/registry`) y la exportación de runs. Exige las dos siguientes. |
| `AGENTCORE_STAFF_KEYS_FILE` | `--staff-keys` | C | Con la API del registry: claves públicas del emisor del staff (§6). |
| `AGENTCORE_EVAL_DSN` | `--eval-dsn` | C, S | Con la API del registry: base propia de las evaluaciones, distinta de la principal. |
| `AGENTCORE_KEYS_RELOAD_SECONDS` | `--keys-reload-seconds` | O | Relectura de los archivos de claves (defecto 5; 0 la apaga). |

## 2. Piezas reales

Sin `AGENTCORE_ALLOW_DOUBLES=1`, `serve` usa **por defecto** las siete fábricas reales de la tabla (no hace falta
pasar nada) y rechaza cualquier ruta bajo `testing.`. Cada fábrica se puede cambiar con su argumento
`modulo:atributo`; si le falta su entorno, el arranque dice qué variable.

| Argumento | Fábrica real | Variables que necesita |
|---|---|---|
| `--tools` | `agent_core.adapters.tools.http_executor:http_tool_executor` | `AGENTCORE_TOOL_SERVICE_URL` (C), `AGENTCORE_TOOL_SERVICE_TOKEN` (C, S), `AGENTCORE_TOOL_SERVICE_TIMEOUT_S` (O, 10) |
| `--authz` | `agent_core.adapters.policy_authz:policy_authz` | `AGENTCORE_AUTHZ_FIELD_GRANTS_FILE` (O; sin él nadie lee ningún campo), `AGENTCORE_AUTHZ_BIND_KEYS` (O, `subject_ref`) |
| `--transcript` | `agent_core.composition.transcript:transcript` | usa la base del motor |
| `--calibration` | `agent_core.composition.artifacts:calibration` | `AGENTCORE_CALIBRATION_DIR` (C) |
| `--classifier` | `agent_core.composition.artifacts:classifier_provider` | `AGENTCORE_CLASSIFIER_ARTIFACTS_DIR` (C) |
| `--field-classifier` | `agent_core.composition.classification:field_classifier` | `AGENTCORE_FIELD_CLASSIFICATION_FILES` (C, rutas separadas por coma; las últimas ganan) |
| `--grant-active` | `agent_core.adapters.grants:http_grant_active` | `AGENTCORE_GRANTS_URL` (C), `AGENTCORE_GRANTS_TOKEN` (C, S), `AGENTCORE_GRANTS_TIMEOUT_S` (O, 3), `AGENTCORE_GRANTS_CACHE_TTL_S` (O, 5) |

## 3. Dependencias externas

| Variable | Tipo | Defecto | Descripción |
|---|---|---|---|
| `AGENTCORE_LLM_GATEWAY_URL` | O | — | URL del llm-gateway. Va con el token o ninguna de las dos; sin él toda generación cae a plantilla (aviso al arrancar). |
| `AGENTCORE_LLM_GATEWAY_TOKEN` | O, S | — | Token del llm-gateway. |
| `AGENTCORE_BLOB_BUCKET` / `_PREFIX` / `_KMS_KEY_ARN` | O | — | Blobs del registry en S3. Con el bucket, `migrate` suelta la FK a `reg_blobs`: corre `agentcore blobs-backfill` antes. |
| `AGENTCORE_EVENTS_TOPIC_ARN` | C | — | Solo para `agentcore relay` (outbox → SNS), no para `serve`. |

## 4. Operación

| Variable | Tipo | Defecto | Descripción |
|---|---|---|---|
| `AGENTCORE_ALLOW_DOUBLES` | O | — | `1` permite dobles de demo (`testing.*`). **Prohibida en despliegues.** Alias heredado: `AGENTCORE_ALLOW_DEMO`. |
| `AGENTCORE_AUTO_MIGRATE` | O | `1` | `0`: `serve` no migra al arrancar (rol sin DDL). Entonces corre `agentcore migrate` con el rol dueño antes. |
| `AGENTCORE_READY_REQUIRE_LLM_GATEWAY` | O | `1` | `0`: el llm-gateway solo se informa en `/readyz` (`degraded`). |
| `AGENTCORE_READY_REQUIRE_TOOL_SERVICE` | O | `0` | `1`: el tool-service bloquea `/readyz`. |
| `AGENTCORE_MAX_INFLIGHT` | O | `0` | Tope de peticiones `/v1` simultáneas del proceso; pasado el tope, 503 con `Retry-After: 1`. 0 = sin tope. Las sondas nunca cuentan. |
| `AGENTCORE_WORKER_THREADS` | O | `40` | Hilos para las rutas síncronas; acota la concurrencia real del proceso. |
| `AGENTCORE_SHUTDOWN_GRACE_SECONDS` | O | `25` | Plazo de apagado ordenado tras SIGTERM (uvicorn espera las peticiones en curso). Debe ser menor que el `stop_grace_period` del orquestador. |
| `AGENTCORE_RATE_MAX_HITS` / `_RATE_WINDOW_SECONDS` / `_RATE_SERVICE_MULTIPLIER` | O | demo | Límite por principal (ventana deslizante). |
| `AGENTCORE_DAILY_BUDGET_USD` | O | demo | Presupuesto diario por principal. |

## 5. Observabilidad

| Variable | Tipo | Descripción |
|---|---|---|
| `OTEL_TRACES_EXPORTER` | O | `otlp` (defecto) o `none`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `_TRACES_ENDPOINT` | O | Destino OTLP. Sin endpoint no se exporta nada. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` / `_TRACES_PROTOCOL` | O | Protocolo OTLP. |
| `OTEL_EXPORTER_OTLP_HEADERS` / `_TRACES_HEADERS` | O, S | Cabeceras (p. ej. la autorización de Langfuse). |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | O | Nombre y atributos del recurso. |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | O | Muestreo. |
| `OTEL_SDK_DISABLED` | O | `true` apaga el SDK. |
| `AGENTCORE_TRACE_CONTENT` | O | `1` admite prompt y respuesta como atributos de span (vista `audit`); apagado por defecto. |
| `AGENTCORE_TRACE_LANGFUSE` | O | `1` deriva los atributos `langfuse.*`; apagado por defecto. |

Los logs son JSON por stderr (`timestamp`, `level`, `logger`, `message`, campos de correlación y `trace_id`).
El log de acceso de uvicorn está apagado a propósito: llevaría IPs e ids en la ruta.

## 6. Formatos de archivo

**Claves de identidad** (`--identity-keys`, `--staff-keys`), YAML o JSON:

```yaml
principal_keys: {kid1: <base64url de 32 bytes Ed25519 público>}
delegation_keys: {kid1: <base64url>}   # opcional solo en el archivo del staff
```

Claves repetidas, vacías o con longitud distinta de 32 bytes: `serve` no arranca (falla cerrado). Se vuelven a
leer cada `--keys-reload-seconds` (5 s): para rotar se publica la clave nueva con su `kid` junto a la vieja y se
retira la vieja después; **no hace falta reiniciar `serve` ni otros servicios**. Una recarga que falla conserva
las últimas claves buenas y `/readyz` marca `keys: fail` hasta que el archivo vuelva a ser válido.

**Concesiones de campos** (`AGENTCORE_AUTHZ_FIELD_GRANTS_FILE`): `[["campo","purpose"], ...]`.

**Catálogo de clasificación** (`AGENTCORE_FIELD_CLASSIFICATION_FILES`): `{"<tabla>.<campo>" | "<campo>":
{"field_class","tag","quasi":{"op","width"}}}`. Un campo sin clasificar queda `pii_direct`.

## 7. Procesos aparte

`serve` no ejecuta tareas en segundo plano salvo la migración del esquema. Estos comandos son procesos que debe
programar quien despliega, con las mismas variables:

- `agentcore migrate`: aplica el esquema con el rol dueño (siempre re-aplica; incluye los permisos de
  `--app-role`).
- `agentcore sweep --once`: cierra como `abandoned` los runs vencidos. Programarlo cada pocos minutos.
- `agentcore relay`: publica el outbox en SNS (servicio, un líder por candado asesor).
