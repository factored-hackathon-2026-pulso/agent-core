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
| `uv run agentcore record <camino> --out <archivo> --registry <dir>` | Graba un camino |
| `uv run agentcore sweep --registry <dir> --once` | Cierra como `abandoned` los runs inactivos (necesita Postgres: `--dsn` o `AGENTCORE_DATABASE_URL`) |

Lo mismo corre el CI en `.github/workflows/ci.yml`.

## Telemetría

`agentcore serve` exporta trazas por OTLP y escribe logs JSON a stderr. Todo se configura con las variables estándar `OTEL_*`; el diseño está en [`docs/adr/0003-observabilidad-dos-planos.md`](docs/adr/0003-observabilidad-dos-planos.md) y en M11 §3.2.

**Qué sale**

- Spans, solo si hay un endpoint OTLP:
  - `agentcore.api.request`: uno por request HTTP (método, status y tipo de error);
  - `invoke_agent`: uno por turno, hijo del request, con `run_id`, `turn_id`, `session_id`, release y agente;
  - `agentcore.decide`, `agentcore.rule` y `execute_tool`: hijos del turno, derivados de los eventos de auditoría con los tiempos de su `ts` y `latency_ms`;
  - `chat`: una llamada al LLM, desde el gateway;
  - `agentcore.transfer`: la validación de una transferencia entre agentes. El `invoke_agent` del agente destino lleva un *span link* a este span.
- Logs: una línea JSON por registro, a stderr, nivel `INFO`, con `timestamp`, `level`, `logger`, `message`, `exc_type` (solo el tipo de una excepción, nunca su texto ni su stack) y, cuando aplica, los campos de correlación `run_id`, `turn_id`, `session_id`, `agentcore.release`, `agentcore.agent` y `trace_id`. El `trace_id` es el mismo que el de la traza.
- El `trace_id` de cada respuesta de la API (y de los errores `problem+json`) es el de la traza del request: con él se busca la traza en el backend. Sin endpoint OTLP sigue habiendo un `trace_id` (generado por el servidor) y los logs siguen correlacionados, pero no hay trazas.

**Qué no sale**

- El contenido: ni textos del cliente, ni `args`/`result` de las tools, ni prompts o respuestas del modelo. Los atributos de span son una lista cerrada de ids, enums y contadores (`ALLOWED_ATTRIBUTES`), y la captura de contenido está apagada.
- La telemetría propia de FastAPI: está apagada, así que el framework no exporta nada por su cuenta.
- Logs y métricas por OTLP: solo se exportan trazas.
- El log de acceso de uvicorn (`access_log=False`) ni el mensaje o el stack de una excepción no controlada.

**Variables admitidas** (el soporte es un subconjunto del estándar; un valor inválido hace salir al servidor con código 2 y nombra la variable, nunca el valor de las cabeceras):

| Variable | Valores |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | URL base; se le agrega `/v1/traces`. Sin ella (ni la variable de la señal) no hay trazas |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | URL completa; gana sobre la anterior |
| `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | solo `http/protobuf` (no hay exportador gRPC) |
| `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | `clave=valor` separados por coma |
| `OTEL_TRACES_EXPORTER` | `otlp` (por defecto) o `none` |
| `OTEL_SDK_DISABLED` | `true` apaga las trazas |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | recurso; `service.name` es `agentcore` por defecto |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | los seis samplers estándar (por defecto `parentbased_always_on`); el argumento es la razón de `traceidratio`, entre 0 y 1 |

El certificado, la compresión y el timeout del exportador los lee el SDK del entorno del proceso. El nivel de log (`INFO`) es fijo y `openai`, `httpx` y `httpcore` se silencian por debajo de `WARNING`.

**Con Phoenix (local)**

```bash
docker run -p 6006:6006 arizephoenix/phoenix
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006 uv run agentcore serve …   # abre http://localhost:6006
```

Cualquier otro backend OTLP/HTTP sirve con el mismo endpoint y cabeceras (`OTEL_EXPORTER_OTLP_HEADERS`).

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
agent_telemetry/      trazas OpenTelemetry y logs JSON correlacionados
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
