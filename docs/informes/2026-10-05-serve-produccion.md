# Informe: `agentcore serve` como el Core de producción

- Fecha: 2026-10-05 · Destinatario: equipo del motor de mejora · Entrega: PRs en `pulso-factored/agent-core`
- Detalle por componente y hallazgos abiertos: `docs/serve-readiness.md`. Variables: `docs/serve-env.md`.

## PRs (todos en `main`)

| Ítem | PR | Qué entrega |
|---|---|---|
| A2.3 | #62 | `AGENTCORE_ALLOW_DOUBLES` (alias `ALLOW_DEMO`), línea `serve mode=...` al arrancar, lista exacta de dobles |
| A3 | #63 | `/readyz` por dependencia (`postgres`, `keys`, `llm_gateway`, `tool_service`), requeridas u opcionales, con plazo |
| A3 | #64 | versión del esquema, migración con candado asesor, migración automática al arrancar (reintenta), chequeo `schema` |
| A3 | #65 | topes de carga, plazo de apagado, clave de JEV obligatoria, `docs/serve-env.md` |
| A3/A4 | #66 | configuración solo por entorno con piezas reales por defecto, Dockerfile por digest (amd64+arm64), `deploy/compose` |
| A5 | #67 | prueba en modo `serve` del principal `builder` del motor (permite escribir/congelar/evaluar/leer; niega aprobar/publicar/promover/revocar), formato de claves y rotación |
| A6 | #68 | fixtures alineadas al contrato del tool-service, test de deriva, `docs/tool-grants.md` |
| A2.1 | #71, #72 | proveedores reales (`rule` con `count`/`narrow`/`value_from`, `classifier` con `text_from`) para transferencia y disputas |
| A2.4 | #73 | suites base de Codex (104 casos) como fixtures; el evaluador sobrevive a un run cerrado |
| caos | #74 | arrancar con Postgres caído |
| concurrencia | #75 | el tope sigue al pool; pool de 40 en el compose |
| local | #70 | corrección de `AGENTCORE_WORKER_THREADS` (la corrutina no se esperaba) y kit local |
| A2.2/A3 | este PR | inventario final e informe |

Cada PR escribió primero sus pruebas (RED) salvo A5, que documenta un comportamiento que ya se cumplía. Pruebas de lo
tocado: **196 pasan** (modos, readiness, bootstrap, esquema + 4 de integración con Postgres 16, topes y entorno,
principal del motor, arranque con la base caída, proveedores, suites base, deriva de tools, evaluador). Puertas
locales en cada PR: `ruff`, `mypy`, `lint-imports`, `agentcore contracts --check` y las suites afectadas (la
suite completa pasó en #73). La CI de GitHub no corre (facturación).

## Corrida en vivo (compose, sin secretos en el informe)

Imagen `agent-core:local` en `mode=production`, Postgres 16, llm-gateway (`xiaomi/mimo-v2.6-flash`), tool-service
sobre el dataset publicado, JEV real. Lo sintético: artefactos de calibración y classifier, claves de prueba,
concesiones de campo del dataset sintético y un doble de la plataforma para `grant_active`.

- Disputas es y pt: `resolved` con PQR radicada y verificada. Transferencia recepción → disputas es y pt:
  `run_transferred` con linaje.
- Ciclo del registry: evaluar (pass) → aprobación del motor **403** → aprueba el supervisor → publica → promueve
  → run nuevo con la release nueva y su linaje. Exportación de runs y eventos.
- Evaluación nativa: recepción 28/28; disputas y consultas `gate_failed`; copiloto `failed_infra`.
- Caos: 5 escenarios; 1 defecto real encontrado y corregido (#74).
- Concurrencia: 20 simultáneos, 20/20 con pool de 40; inicio p50 2,5 s / p95 5,0 s; turno p50 10,3 s / p95 11,2 s.
  Con pool de 10 había 5 fallos (corregido en #75).
- Imagen: amd64 321 MB, arm64 351 MB, no root, `HEALTHCHECK` `healthy`.

## Credenciales usadas

`OPENROUTER_API_KEY` y `AGENTCORE_JEV_API_KEY`, tomadas del `.env.e2e` local del usuario como variables de proceso
(no se escriben en el repo ni aquí). Para la corrida en su entorno basta con las mismas dos.

## Lo que sigue abierto (dueño y esfuerzo)

Ver la tabla de `docs/serve-readiness.md` §4. Lo más importante: `consultas` no tiene una tool real por radicado
(tool-service/producto), el copiloto no cierra su paso con flash (prompt del motor de mejora), la evaluación necesita
repeticiones y margen de ruido porque discrepa entre base y candidata idénticas, los jueces no están cableados, el
replay `audit` queda excluido con sus tres causas, y los artefactos reales de datos siguen pendientes. A infra:
prohibir `AGENTCORE_ALLOW_DOUBLES` y programar `sweep` y `relay`.
