# ADR 0022 — Superficies estables, forma de las referencias y migraciones compatibles

- Estado: propuesto (decisiones de Claude sin revisión, ver `docs/informes/2026-10-02-solicitudes-n01-n11-decisiones.md`)
- Responde a: N-05, N-06 y N-11 de las solicitudes del repo `infra` (§31.12)
- Amplía: ADR 0002 (límites y contratos), ADR 0017 (registry en Postgres)

## Contexto
El repo de infraestructura compone agent-core desde fuera (el nivel de pruebas a2 y el servicio desplegado) y fija una imagen por digest. Necesita saber qué puede usar sin que se rompa en el siguiente SHA, que una referencia tiene dos formas equivalentes, y que migrar la base nunca deja sin funcionar al binario anterior durante un despliegue gradual o un rollback.

## Decisión

### 1. Superficie de composición estable (N-06)
Se declara estable, con compatibilidad hacia atrás dentro de la misma versión mayor del contrato (`SCHEMA_VERSION`):

| Símbolo | Qué se garantiza |
|---|---|
| `composition.serve_ports.resolve_ports(args, env, clock, ids, *, tracer)` y `ServePorts` | Los parámetros y campos actuales no se renombran ni se quitan; se pueden agregar campos con valor por defecto |
| `composition.serve.build_api_deps(ports, *, registry_service, telemetry, build_sha)` y `api.app.ApiDeps` | Idem; los parámetros nuevos son solo por palabra clave y opcionales |
| `registry.http.registry_extension(service, verifier, clock)` | Idem |
| `registry.service.RegistryService(store, evaluator, clock, ids, runs, limits, quotas)` | `quotas=` y `limits=` quedan; el orden posicional no cambia |
| `registry.evaluation.evaluator.ScenarioEvaluator`, `registry.postgres.store.PgRegistryStore`, `registry.memory.InMemoryRegistryStore` | Existen con esos nombres y cumplen `RegistryStore` / `EvalPort` |

Lo no listado es interno. `tests/test_stable_surface.py` fija las firmas: si falla, el cambio es una ruptura y se anuncia, no se ajusta la prueba. Un campo que se quite o se renombre exige subir la versión mayor.

### 2. Forma de las referencias y del hash (N-05)
`{id, spec}` y `"id@spec"` son **equivalentes de contrato** (`RefSpec`, `EntityRef`). El `content_hash` y el `release_hash` se calculan sobre el **modelo normalizado** (`encode_entity` = JCS del `model_dump`), nunca sobre el texto recibido. Quien emita contenido debería preferir la forma de objeto; ambas dan el mismo hash (`tests/registry/test_requests_scope_a.py::test_n05_…`).

### 3. Migraciones expand/contract (N-11)
- `agentcore migrate` se aplica **antes** de arrancar el binario nuevo. El binario anterior debe seguir funcionando con el esquema nuevo (*expand*).
- Un script solo puede: crear tablas, índices y columnas con `IF NOT EXISTS`; agregar columnas `NOT NULL` solo con `DEFAULT`; recrear triggers.
- Quedan vetados `DROP TABLE|COLUMN|INDEX|SCHEMA|CONSTRAINT`, `RENAME`, `ALTER COLUMN … TYPE | SET NOT NULL` y `TRUNCATE`. Lo hace cumplir `tests/test_stable_surface.py::test_sql_scripts_only_expand`.
- Una limpieza (*contract*) solo se publica **una versión después** de que ningún binario desplegado use lo que se retira, con su propio ADR.
- Rollback: se vuelve al digest anterior sin deshacer migraciones.

## Consecuencias
- El repo de infra puede fijar un SHA y un job `contract-drift` contra estas firmas.
- Los cambios de esquema que no sean aditivos necesitan más pasos (dos versiones).
- Pendiente fuera de este repo: la prueba de «esquema nuevo con binario anterior» en el bump de SHA (CAP-57) y un rol de solo lectura para el exporter (N-08).
