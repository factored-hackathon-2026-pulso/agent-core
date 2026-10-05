# `agentcore serve`: qué es real y qué no (inventario)

- Fecha: 2026-10-05 · Base: `main` en `356c837` (incluye los PRs #62 a #75).
- Método: lectura del código con archivo:línea **y** ejecución de la imagen en el compose de referencia
  (`deploy/compose`, `mode=production`, JEV, tool-service y llm-gateway reales, modelo `xiaomi/mimo-v2.6-flash`,
  Postgres 16). La columna **Evidencia en vivo** dice qué se ejercitó de verdad.
- La CI de GitHub no corre (facturación): las puertas locales son las que valen.

## 1. Cómo decide `serve` qué es real

Fuera de `AGENTCORE_ALLOW_DOUBLES=1` (alias heredado `AGENTCORE_ALLOW_DEMO=1`), `serve` usa **por defecto** las
siete fábricas reales y rechaza cualquier ruta bajo `testing.` (`composition/serve_ports.py`, `_REAL`, `_DOUBLES`).
Al arrancar registra `serve mode=production` o `serve mode=demo doubles=a,b` (`composition/serve.py`, `mode_line`);
solo se listan las piezas que realmente son dobles. Variables y formatos: `docs/serve-env.md`.

## 2. Inventario por componente

| Componente | Estado | Dónde | Evidencia en vivo | Qué falta |
|---|---|---|---|---|
| Calibración | **real, artefacto sintético** | `composition/artifacts.py:30` | umbrales de `cal-demo`/`cal-transfer-demo` (con `pt`) cargados desde `/state/calibration` | el artefacto real del equipo de datos |
| Classifier (`classifier`) | **real, artefacto sintético** | `composition/artifacts.py:47`, `decision/providers/classifier.py` (`text_from`) | elige `disputas`/`consultas` en es y pt (transferencia) | artefacto entrenado real |
| `match-cargo` | **real** (`rule` con `count`/`narrow`/`value_from`) | `decision/providers/rule.py` | `unica` → `seleccionar` → radicación verificada; acotado a las últimas 50 transacciones (máximo del tool-service) | emparejamiento más rico (ver §4) |
| JEV | **real** | `serve_ports.py` (`JevProvider`) | decide `continue`/`out_of_scope` en cada turno; la clave es obligatoria al arrancar | la clave por el gateway (`/v1/jev`) si se prefiere |
| Jueces (métricas `judge`) | **ausente** | `composition/serve_registry.py:26` (`ScenarioEvaluator` sin `judge`) | el reporte trae `judge_notes: []` | implementar `Judge` sobre llm-gateway (mimo-v2.6-pro) |
| llm-gateway | **real** | `adapters/llm/http_gateway.py` | respuesta generada en disputas es/pt; `/readyz` lo sondea | — |
| Cliente de tool-service | **real** | `adapters/tools/http_executor.py` | `buscar_transacciones`, `radicar_pqr` y `obtener_pqr` (relectura) sobre el dataset | `obtener_pqr` por radicado no existe (§4) |
| Tools del motor | **real** | `composition/engine_tools.py` | `seleccionar`, `convertir_moneda` (con `AGENTCORE_FX_RATES_FILE`), `directory/list` | — |
| Tools del constructor | **real** | `composition/builder_tools.py` | — | — |
| Tools en **evaluación** | **doble por diseño** | `registry/evaluation/local_sandbox.py` | las suites siembran respuestas por tool | decisión de diseño; la deriva la cubre `test_tool_contract_drift` |
| Transcript | **real** | `composition/transcript.py` | transcript de los runs leído por la API | separar el transcript de evaluación del de producción |
| Authz | **real, falla cerrado** | `adapters/policy_authz.py` | sin concesiones nadie lee campos; el local usa `--grant-synthetic-fields` | concesiones reales de gobierno de datos |
| Clasificador de campos | **real** | `composition/classification.py` | catálogo publicado por data-pipeline + overlay | — |
| Grants (`grant_active`) | **real** | `adapters/grants.py` | contra un **doble** de la plataforma (`grants-stub`); la plataforma real no estaba | probar contra la plataforma |
| Identidad (cliente y staff) | **real** | `adapters/identity_keys.py` | claves de PRUEBA del repo; rotación sin reinicio cubierta por tests | claves de Terraform |
| Registry / run store / outbox | **real** | `registry/postgres`, `adapters/postgres_uow.py` | 10 releases importadas; propuesta → evaluar → aprobar → publicar → promover | — |
| Esquema y migraciones | **real** | `composition/schema_version.py`, `migrate.py` | `migrate` en el contenedor; migración al arrancar con candado; 6 instancias a la vez, una sola aplica (Postgres real) | — |
| `/healthz` | **real** | `api/app.py` | 200 con la base caída; HEALTHCHECK de la imagen `healthy` | — |
| `/readyz` | **real** | `api/app.py`, `api/readiness.py`, `composition/readiness.py` | `postgres`, `keys`, `schema`, `llm_gateway`, `tool_service` por dependencia; 503 con la base caída y 200 al volver (~2 s) | plazo 1 s cuando una dependencia está caída (§4) |
| Telemetría / OTLP / Langfuse | **real, opcional** | `composition/observability.py` | dentro de la imagen: `AGENTCORE_TRACE_CONTENT`, `AGENTCORE_TRACE_LANGFUSE` y `OTEL_*` configuran el SDK; sin exportador no exporta | probar contra Langfuse desde la imagen |
| Topes de carga | **real** | `api/inflight.py`, `composition/serve.py` | 20 runs simultáneos 20/20 con pool de 40 | — |
| Apagado ordenado | **real** | `timeout_graceful_shutdown` | `docker restart` con un run abierto: el run sigue `open/awaiting confirmation` y continúa | prueba con un turno en vuelo |
| Tareas en segundo plano | **fuera de `serve`** | `agentcore sweep --once`, `agentcore relay` | no se ejecutaron | infra debe programarlos |
| Replay de auditoría | **roto, excluido** | `audit/replay/` | no se re-ejecutó; sin cambios desde 10-02 | ver §4 |

## 3. Qué se ejercitó de punta a punta (conteos)

- **Disputas** (es y pt): flujo completo con cargo real del dataset: JEV → búsqueda → `match-cargo` unica →
  `seleccionar` → `convertir_moneda` → confirmación → `radicar_pqr` → `obtener_pqr` verificado → respuesta
  generada. Resultado `resolved` en es y en pt.
- **Transferencia**: recepción (es y pt) → `directory/list` → clasificador → `run_transferred`; el linaje de la
  sesión enlaza los dos runs con su release y `transfer_id`.
- **Copiloto del asesor**: con delegación firmada llama `leer_productos` y `leer_movimientos` reales; el paso
  final falla con flash (`invalid_output`: omite `kind`) y con pro responde pero el validador lo rechaza.
- **Consultas**: escala por `tool_failure` (ver §4).
- **Ciclo del registry**: propuesta → congelar → evaluar (`recepcion-suite`, pass) → el principal del motor
  intenta aprobar y recibe **403** → el supervisor aprueba → publica → promueve a `prod` → un run nuevo corre con la
  release nueva (linaje). Exportación de runs y de eventos por `/v1/export`.
- **Evaluación nativa de las suites base (104 casos)**: recepción 28/28 pass; disputas y consultas
  `gate_failed`; copiloto `failed_infra` (§4).
- **Caos**: reiniciar Postgres con `serve` arriba (se recupera solo, sin reiniciar); Postgres detenido (`/healthz`
  200, `/readyz` 503, crear run da 500); reiniciar `serve` a mitad de un run (el run continúa); arrancar con
  Postgres caído (**fallaba**, corregido en #74: `/healthz` 200 y `/readyz` 503 hasta que vuelve); arrancar sin
  variables (sale con código 2 nombrando cada una).
- **Concurrencia**: 20 runs simultáneos (recepción, 10 es y 10 pt, clientes distintos). Con pool de 10: 5 fallos
  `PoolTimeout` y turno p50 44 s (**corregido en #75**). Con pool de 40: 20/20, inicio p50 2,5 s / p95 5,0 s,
  turno p50 10,3 s / p95 11,2 s, total p50 13,6 s / p95 13,9 s, 48 conexiones a la base como máximo.
- **Imagen**: `linux/amd64` 321 MB y `linux/arm64` 351 MB (QEMU: construye y arranca; no se probó en vivo),
  usuario no root (uid 10001), sin dependencias de desarrollo, bases fijadas por digest.

## 4. Lo que sigue abierto

| Hallazgo | Evidencia | Dueño | Esfuerzo |
|---|---|---|---|
| `consulta-pqr` llama `obtener_pqr(radicado)`; el `obtener_pqr` real exige `idempotency_key` (relectura tras radicar) y no hay tool por radicado | escala `tool_failure` en vivo | tool-service / producto | S–M |
| Copiloto: con flash omite el envoltorio `kind` del paso; con pro responde pero M8 rechaza la respuesta | `failed_infra` en la evaluación; 3/3 en vivo | motor de mejora (prompt `p/copiloto`) | M |
| Evaluación ruidosa: base y candidata idénticas discrepan en escenarios que dependen del LLM (`happy-es-cargo-duplicado` escala en 2 de 3 corridas) | `gate_failed` con diff vacío | motor de mejora (repeticiones ≥ 3, margen de ruido) | S |
| `consultas`: `negative-*-transferencia-ajena/alheia` esperan `abstained` | falla en base y candidata | agentes | S |
| Jueces sin implementar | `judge_notes` vacío | agent-core | M |
| Replay `audit` de runs reales diverge: un instante por turno frente a varias lecturas de reloj, `tool_called.result_fp` re-proyectado, `response_emitted` sin borrador con citas (`docs/informes/2026-10-02-flujo-real-y-replay-audit.md` §4). No soporta `replay <run_id>` ni sesiones con transferencia | sin cambios en `audit/replay/` desde 10-02 | agent-core (M11) | M–L |
| Con la base caída, los endpoints dan 500 `internal_error` (`PoolTimeout`) en vez de 503 | caos | agent-core | S |
| `/readyz` tarda ~1,2 s si una dependencia no responde (plazo de 1 s); sano responde rápido | caos | agent-core | S |
| `match-cargo` solo ve las últimas 50 transacciones; el slot es la frase completa | `buscar_transacciones` máximo 50 | agent-core / producto | M |
| Artefactos reales de calibración y classifier; concesiones de campos reales; plataforma real para grants | el local usa sintéticos y un doble | datos / plataforma | bloqueado |
| Infra debe prohibir `AGENTCORE_ALLOW_DOUBLES` (hoy solo prohíbe `ALLOW_DEMO`) y programar `sweep` y `relay` | `terraform/modules/workload` | infra | S |
| Reconstruir la imagen con la caché de Docker Desktop devolvió código viejo una vez | `--no-cache` lo resolvió | quien construya | S |
