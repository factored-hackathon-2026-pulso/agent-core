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
