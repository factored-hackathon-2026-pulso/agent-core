# agent-core

Motor de decisión que ejecuta **agentes descritos como datos versionados**. Recibe un mensaje, entiende qué quiere la persona, sigue un guion validado (un *flow*), actúa con red de seguridad y deja un rastro auditable y reproducible. Sigue el ciclo Understand → Decide → Act → Verify → Escalate.

Es agnóstico al negocio: aquí no hay conceptos bancarios. Un agente, un flow, una política o una plantilla son datos; el motor solo los interpreta. El diseño contempla agentes de cara al cliente y también internos (copiloto del asesor, constructor de agentes). La demo del repo trae un agente de atención, `atencion`, con el flow `disputa-cargo`.

Proyecto del equipo de la Factored AI & Data Hackathon 2026.

## Qué garantiza el motor

- **Permisos en la capa de tools**, no en el prompt: el subject sale de la credencial, nunca del body ni del modelo.
- **Toda escritura pasa por confirmar → actuar → verificar**, con clave de idempotencia y recuperación tras una caída.
- **Las reglas de negocio son políticas protegidas**, fuera del modelo.
- **Datos personales protegidos:** ningún dato en vista `full` sale a un modelo, un log o un evento.
- **Respuestas verificadas:** el texto generado se valida contra hechos (citas, cifras, PII, idioma) antes de salir.
- **Escalar entrega un paquete estructurado** a una persona, no un volcado de la conversación.
- **Auditoría determinista:** cada run deja una cadena de eventos con hash que se puede reproducir (replay).

## Requisitos

- Python 3.12 (`>=3.12,<3.13`)
- [`uv`](https://docs.astral.sh/uv/)
- Docker, solo para las pruebas de integración con Postgres

## Empezar

```bash
uv sync --locked
uv run pytest
```

Correr la demo `disputa-cargo` (sin red ni Postgres):

```bash
# validar el registro de demo
uv run agentcore validate tests/fixtures/registry-demo

# reproducir un camino grabado con el motor completo
uv run agentcore replay tests/fixtures/runs/resuelto.yaml --mode fixture \
  --registry tests/fixtures/registry-demo \
  --catalog tests/fixtures/catalogo-datos-prueba.yaml

# grabar un camino nuevo
uv run agentcore record cancelado --out cancelado.yaml --registry tests/fixtures/registry-demo
```

Caminos grabados en `tests/fixtures/runs/`: `resuelto`, `cancelado`, `escalado_por_monto`, `uncertain_verify`, `step_up` e `interrupcion`.

Demo de transferencia entre agentes (ADR 0021; sin red ni Postgres). Recepción entiende el problema, lee el directorio `atencion-cliente`, elige al especialista y le transfiere la conversación; el especialista responde en el mismo turno.

```bash
# validar el registro de la demo: recepcion, disputas y consultas, una release por agente
uv run agentcore validate tests/fixtures/registry-transfer-demo

# la sesión completa en proceso y por HTTP (linaje con los dos runs y enlace por hash)
uv run pytest tests/composition/test_transfer_demo.py

# reproducir la sesión grabada (las dos cadenas)
uv run agentcore replay tests/fixtures/runs-transfer/transferencia.yaml --mode fixture \
  --registry tests/fixtures/registry-transfer-demo \
  --catalog tests/fixtures/catalogo-datos-prueba.yaml

# volver a grabarla
uv run agentcore record transferencia --out transferencia.yaml --registry tests/fixtures/registry-transfer-demo \
  --catalog tests/fixtures/catalogo-datos-prueba.yaml
```

Qué tener en cuenta:
- El umbral con que recepción acepta la elección sale de `calibrations/cal-transfer-demo.json`, un artefacto **hecho a mano** con el umbral comodín `"*"`; no es una calibración (spec §12.1). Sin él, recepción pide aclarar y no transfiere.
- El modelo que elige especialista usa un proveedor guionado. Con `agentcore serve` el directorio está cableado (`RegistryDirectory` sobre el registry de Postgres), pero el proveedor real de esa elección sigue abierto, así que la demo por `serve` todavía no transfiere.
- Los flows de los especialistas no leen el slot `problema` transferido: vuelven a preguntar al usuario.
- La base impone un run abierto por sesión (índice `runs_one_open_per_session`); la ruta de Postgres no se ha verificado (no hubo docker en la ejecución).

## Comandos

| Comando | Para qué |
|---|---|
| `uv run pytest tests/mXX` | Pruebas de un módulo (la carpeta lleva el número de su spec) |
| `uv run pytest tests/contracts` | Suites de contrato de los puertos |
| `uv run pytest` | Todas las pruebas (las de integración se omiten sin Postgres) |
| `docker compose up -d postgres` | Postgres local para `tests/integration` |
| `uv run lint-imports` | Fronteras entre módulos (`.importlinter`) |
| `uv run mypy` | Tipos en modo estricto |
| `uv run ruff check .` | Lint, incluido el veto a `datetime.now()`, `uuid4()` y `random` |
| `uv run agentcore contracts` | Regenera `contracts/` (con `--check` solo verifica) |
| `uv run agentcore validate <registro>` | Valida un registro de autoría |
| `uv run agentcore replay <fixture> --mode fixture\|audit` | Reproduce un run grabado |
| `uv run agentcore record <camino> --out <archivo> --registry <dir>` | Graba un camino (`transferencia` con `--registry tests/fixtures/registry-transfer-demo`) |
| `uv run agentcore sweep --registry <dir> --once` | Cierra como `abandoned` los runs inactivos (necesita Postgres: `--dsn` o `AGENTCORE_DATABASE_URL`) |
| `uv run agentcore migrate` | Aplica los esquemas de Postgres (idempotente): motor, auditoría y registry en `--dsn`/`AGENTCORE_REGISTRY_DSN`; evaluaciones en `--eval-dsn`/`AGENTCORE_EVAL_DSN`; `--app-role` da permisos mínimos |

Lo mismo corre el CI en `.github/workflows/ci.yml`.

## Estructura

```
agent_core/
  domain/  ports/     tipos, nodos, eventos y puertos compartidos
  flows/              esquema de flows y validación estática
  interpreter/        intérprete de nodos
  actions/            protocolo de escritura confirmar → actuar → verificar
  turn/               ciclo del turno
  decision/  guards/  modelos de decisión (Understand) y guardas de entrada
  views/  response/   vistas de datos y tokenización; validador de respuesta
  api/                API HTTP /v1
  handoff/  audit/    escalamiento y traspaso; auditoría, transcript y replay
  adapters/           adaptadores reales (reloj, IDs, claves, identidad, Postgres)
  composition/        raíz de composición que cablea los módulos
  registry/           registry de entidades: propuestas, gate de evaluación, publicación y linaje (unidad 2)
  knowledge/          pendiente: solo spec (M12)
  adapters/llm/       gateway de LLM compatible con OpenAI y adaptador del nodo `agent` (unidad 5)
agent_telemetry/      trazas OpenTelemetry
testing/fakes/        dobles en memoria de cada puerto
tests/                pruebas por módulo, contratos e integración
contracts/            JSON Schema y OpenAPI generados (no editar a mano)
docs/                 ADR, specs y planes
```

## Diseño

- [`docs/specs/motor/00-indice.md`](docs/specs/motor/00-indice.md): **empieza aquí**. Mapa de módulos, dependencias, puertos, dueños del estado y eventos.
- [`docs/specs/motor/`](docs/specs/motor/): un spec por módulo, con interfaz, comportamiento, invariantes y pruebas.
- [`docs/specs/2026-09-28-motor-de-decision-design.md`](docs/specs/2026-09-28-motor-de-decision-design.md): visión integrada del motor.
- [`docs/adr/`](docs/adr/): decisiones de arquitectura.
- [`docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`](docs/specs/TEMAS-ABIERTOS-PENDIENTES.md): decisiones pendientes.
- [`docs/00-descomposicion-y-repos.md`](docs/00-descomposicion-y-repos.md): cómo se reparte el sistema completo y sus contratos.

## Estado

El motor y sus módulos están implementados y probados con dobles y, donde aplica, sobre Postgres. Lo que **todavía no existe**:

- **Servidor HTTP arrancable:** la API está implementada (`agent_core/api`), pero falta un comando `agentcore serve`.
- **Composición del servidor con `PostgresRegistry`:** el registry (unidad 2) está implementado (`agent_core/registry`, `agentcore registry …`), pero ningún proceso lo compone aún con el motor real.
- **Conocimiento:** solo hay propuesta de spec (`docs/specs/motor/m12-conocimiento.md`).
- **Adaptadores reales** para tools, autorización y transcript: hoy solo hay dobles en `testing/fakes/`. El gateway de LLM sí es real (`agent_core/adapters/llm`); falta correr `agentcore llm-smoke` contra OpenRouter.

## Cómo contribuir

1. Lee el spec del módulo, el índice y los ADR que cita.
2. El comportamiento de un módulo se cambia primero en su spec. Una interfaz se cambia primero en el dominio y los puertos, y luego se regenera `contracts/`.
3. Escribe las pruebas del spec y confirma que fallan; implementa hasta que pasen.
4. Antes de abrir un PR deben pasar `pytest`, `lint-imports`, `mypy`, `ruff` y `agentcore contracts --check`.

Reglas duras (detalle en [`CLAUDE.md`](CLAUDE.md)):

- Un módulo solo importa `domain`, `ports` y la interfaz pública de los módulos que `.importlinter` le permite.
- Nada de `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`: usa `Clock` e `IdSource`.
- Dinero con `Decimal`, nunca `float`.
- Fixtures y pruebas solo con datos sintéticos. **Nunca** copies datos reales del dataset ni las credenciales del diccionario de datos del banco.
