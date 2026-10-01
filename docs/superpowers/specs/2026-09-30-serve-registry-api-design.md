# API HTTP del registry en `agentcore serve` — diseño breve

- Fecha: 2026-09-30
- Estado: **aprobada e implementada (2026-09-30)**
- Gobierna: `2026-09-30-servidor-arrancable-design.md` §8, registry spec §6.2, §7.4 y §8, tema #13 y #14

## 1. Intención

Hoy `serve` no expone `/v1/registry/*`: `build_api_deps` acepta un `registry_service`, pero nadie lo construye. Se cablea un `RegistryService` con **evaluación real** (decidido con el usuario el 2026-09-30): el gateway de LLM y JEV son los mismos de `serve`; las tools de la evaluación van a un sandbox.

**Decidido con el usuario:** evaluación real; montaje **opt-in**; §3 a §5 aprobados tal como se proponían.

## 2. Alcance

Dentro: bandera `--registry-api`, servicio y evaluador reales, verificador de staff, almacenamiento de las corridas de evaluación. Fuera: `AuthzPort`/`ToolExecutor` reales, sandbox de la unidad 3, evaluación asíncrona, límites por principal del staff.

## 3. Verificador de staff (propuesta)

La spec del registry §8 exige que la API verifique solo claves del staff, pero `registry_extension` usa hoy el `authenticate` de M9 (claves de clientes). Con una sola app y un solo verificador, fusionar las claves permitiría que el emisor de clientes firme una credencial `builder`. Propuesta:

- `registry_extension(service, verifier=None)`: con `verifier`, `who()` extrae el bearer y verifica con ese verificador (credencial inválida → `CredentialsInvalid`, 401 con el manejador existente); sin él conserva el comportamiento actual (las pruebas existentes no cambian). `require_builder` y los roles siguen aplicándose igual.
- `--staff-keys` (archivo con el mismo formato que `--identity-keys`): `load_identity_verifier` gana `delegation=True`; el staff no usa delegación, así que `delegation_keys` es opcional (vacío = rechaza toda delegación) y `grant_active` es `lambda ref, now: False`.
- En demo y sin `--staff-keys`, el verificador es `testing.registry_demo:demo_verifier` y `staff-identity` entra en la lista de dobles.
- El staff no pasa por los límites por principal de M9 (fuera de alcance).

## 4. Servicio y evaluador

- `RegistryService(PgRegistryStore(dsn), ScenarioEvaluator(harness, LocalSandbox(ids), max_workers=1), clock, ids, runs=UowRunReleases(uow_factory))`, igual que el CLI `agentcore registry`. `max_workers=1`: el harness comparte reloj e ids; la concurrencia queda para después.
- `harness = EngineScenarioHarness(...)` con las piezas ya resueltas de `serve`: `gateway`, `keys`, `calibrations`, `authz`, `classifier`, `clock`, `ids`; `providers` por escenario = `ports.providers` (JEV real, `classifier` de `serve`).
- Sandbox: `LocalSandbox` (respuestas sembradas, aisladas por corrida), el mismo respaldo del CLI hasta que la unidad 3 aporte el real.
- Código nuevo en `agent_core/composition/serve_registry.py` (`RegistryApiPorts`, `build_registry_service_for_serve`); `ServePorts` gana `registry_api: RegistryApiPorts | None = None`.

## 5. Almacenamiento de las evaluaciones (propuesta)

Cada escenario corre un motor completo y escribe runs, eventos y auditoría. Contra la base de producción contaminaría `runs`, la analítica (#11) y los contadores de costo. Propuesta: base de datos propia, `--eval-dsn` (o `AGENTCORE_EVAL_DSN`), con el mismo esquema; `EvalStorage.uow_factory` y `audit` salen de un `PostgresStore(eval_dsn)`. El `TranscriptStore` es el de `serve` (los `run_id` son únicos; con el transcript persistente de la unidad 7 las corridas de evaluación quedarían ahí, anotado en §7).

## 6. Opciones y validación

- `--registry-api` (sin argumento). Con la bandera, `resolve_ports` suma a la lista única de problemas: falta `--eval-dsn`; falta `--staff-keys` (fuera de demo); archivo de claves inválido.
- Sin la bandera, `serve` no cambia: no hay rutas `/v1/registry/*` (404).
- `--eval-dsn` sigue la regla del DSN: preferible por entorno; nunca se imprime.

## 7. Abiertos

- **Fallas de JEV no marcan `failed_infra`:** la sonda del harness solo detecta fallas del gateway; si JEV cae, el motor degrada y el escenario falla por su `expect`, no como infraestructura. Registry §6.2 punto 6 solo nombra gateway y sandbox; decidir si se extiende la sonda a los proveedores.
- Evaluación síncrona: una evaluación (2 releases × N escenarios × k) bloquea la petición HTTP.
- Transcripts de evaluación en el almacén real (unidad 7).
- Autenticación reforzada y límites del staff en la API.

## 8. Pruebas

1. Con `--registry-api` y credencial de staff, `POST /v1/registry/proposals` responde 201; con credencial de cliente, 401; con credencial de cliente aunque traiga `type: builder` firmada por la clave de clientes, 401.
2. Sin la bandera, `/v1/registry/proposals` responde 404.
3. `resolve_ports` con `--registry-api` junta en una lista: falta `--eval-dsn`, falta `--staff-keys` fuera de demo, archivo inválido; el DSN de evaluación no aparece en stderr.
4. El servicio compuesto usa `ScenarioEvaluator` + `EngineScenarioHarness` con el gateway y los proveedores de `serve` (prueba de cableado con dobles guionados; sin red).
5. `registry_extension` sin `verifier` conserva el comportamiento (suite `tests/registry` intacta); `lint-imports`, `mypy`, `ruff` y la suite en verde; `contracts/` no cambia.
