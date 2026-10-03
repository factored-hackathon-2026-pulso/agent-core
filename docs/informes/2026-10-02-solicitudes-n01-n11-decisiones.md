# Solicitudes N-01 a N-11 (§31.12): decisiones tomadas sin revisión

Cada decisión la tomó Claude trabajando de noche, sin poder preguntar. **Revísalas antes de fusionar.** Rama de partida: `main` en `86a7674`.

| # | Decisión | Alternativa descartada | Dónde |
|---|---|---|---|
| D-01 | N-01: los esquemas del registry salen en `contracts/registry/` (entradas en modo `validation`, respuestas en `serialization`), no dentro de `schemas/` ni como rutas en `openapi.json`. Los cuerpos de request se publican con nombre `…Body` desde `REQUEST_BODIES` de `registry/http.py`. | Montar `/v1/registry` en `openapi.json` (obliga a que `create_app` conozca el registry, que es una extensión opcional). | `agent_core/contracts.py` |
| D-02 | N-04: `GET /version` sin autenticación, fuera de `/v1` y fuera del OpenAPI, con `{package, contract, sha}`. `info.version` del OpenAPI pasa a `SCHEMA_VERSION` (antes 1.0.0 fijo). El SHA llega por `AGENTCORE_GIT_SHA` (`--build-arg GIT_SHA` en el Dockerfile). | Cabecera `X-Agentcore-Version` en cada respuesta. | `api/app.py`, `Dockerfile` |
| D-03 | N-10: el `eval_run_id` se agrega como clave del cuerpo de `gate_failed` junto al reporte (aditivo, no cambia las claves existentes). | Envolver el reporte en `{report, eval_run_id}` (rompería a quien ya lee el cuerpo). | `registry/service.py` |
| D-04 | N-03: `ReleaseDetail` gana los cuatro campos; `language_detection` es obligatorio y los demás tienen valor por defecto en el modelo. | Devolver el `Release` completo. | `registry/models.py` |
| D-05 | N-02: `GET /v1/registry/aliases/{agent_id}/{alias}` devuelve `AliasState` (release y estado) o 404; `GET /v1/registry/versions/{kind}/{eid:path}` devuelve la lista de versiones. Se usó el prefijo `/versions/` porque `/entities/{kind}/{eid:path}` se tragaría un sufijo `/versions`. Mismo permiso que el resto de las lecturas (`builder`). | `/entities/{kind}/{eid}/versions`. | `registry/http.py` |

Hallazgo previo (no es de esta tarea): `tests/composition/test_transfer_spans.py` y `test_turn_telemetry.py` fallan de forma intermitente también en `main` sin estos cambios (estado global de OpenTelemetry entre tests).

## Scope B (ADR 0022)

| # | Decisión | Alternativa descartada | Dónde |
|---|---|---|---|
| D-06 | N-06: la superficie estable es la listada en el ADR 0022, fijada por firmas en una prueba; compatibilidad dentro de la misma versión mayor de `SCHEMA_VERSION`. | Declararla solo en prosa. | `tests/test_stable_surface.py` |
| D-07 | N-05: se confirma que `{id, spec}` e `"id@spec"` son equivalentes y que el hash va sobre el modelo normalizado; se recomienda la forma de objeto. | Rechazar una de las dos formas. | ADR 0022 §2 |
| D-08 | N-11: la garantía es una política expand-only más una prueba estática de los `.sql`; las limpiezas (*contract*) necesitan dos versiones y un ADR. No hay migraciones versionadas ni se construyó una prueba con Postgres contra el binario anterior. | Un runner de migraciones versionadas con `down`. | ADR 0022 §3 |

## Scope C

| # | Decisión | Alternativa descartada | Dónde |
|---|---|---|---|
| D-09 | N-09: recarga perezosa al verificar (a lo sumo cada `--keys-reload-seconds`, 5 s por defecto, 0 la apaga), sin hilo de fondo ni señal. Compara el contenido del archivo, no el mtime. Una recarga rota conserva las últimas claves buenas (`last_reload_error` guarda solo el tipo). El arranque sigue fallando cerrado. Aplica a `--identity-keys` y `--staff-keys`. | Recargar con SIGHUP (no existe en Windows) o con un hilo de vigilancia. Fallar cerrado ante una recarga rota (un archivo a medio escribir tumbaría todo el servicio). | `adapters/identity_keys.py` |

### N-08 (exportación de runs y eventos)

| # | Decisión | Alternativa descartada | Dónde |
|---|---|---|---|
| D-10 | Tres rutas de solo lectura, paginadas por cursor, bajo `/v1/export`: `runs` (cursor `run_seq`), `runs/{id}/events` (cursor `seq`, continuo dentro del run, la cadena de hashes detecta huecos) y `registry-events` (cursor = posición). Un consumidor se pone al día con dos cursores sin perder eventos aunque los commits lleguen desordenados entre runs. | Un cursor global de eventos (exige una columna de secuencia y se pierden filas por commits fuera de orden); exportar a archivos. | `composition/export_http.py`, `ports/export.py` |
| D-11 | Rol nuevo `exporter` (solo lectura, sin persona ni step-up; `admin` también sirve). **El emisor del staff (infra) tiene que acuñar credenciales con ese rol.** Se monta solo con `--registry-api` y verificador del staff. | Reutilizar `admin` (demasiado poder para una ingesta). | `registry/roles.py` |
| D-12 | El run exportado es un resumen sin datos de cliente (sin slots, hechos ni id del principal; solo su tipo). `RunSummary` es un modelo nuevo en `ports` y por eso aparece `contracts/schemas/RunSummary.json` (cambio de interfaz: avisar). | Exportar el `RunState` completo. | `ports/export.py` |
| D-13 | **No** se exporta el outbox de la unidad 4: el contrato de eventos salientes (`agent_core.outbound`, `contracts/events/`) ya es el canal público de ese flujo; el `Outbox` es una cola interna. Si hace falta, es una tarea aparte. | Listar `outbox` pendientes por HTTP. | — |
| D-14 | Las consultas de Postgres (`list_runs`, `read_after`) no se ejecutaron contra una base real: no hay daemon de Docker en la máquina. Hay un test de integración para `read_after`; falta uno para `list_runs`. | — | `adapters/postgres_uow.py` |
