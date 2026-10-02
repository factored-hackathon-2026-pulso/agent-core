# Observabilidad (refactor del plano operativo) y spans OTel de la transferencia: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el plano operativo de ADR 0003 (trazas OTel y logs) deje de estar "a medio cablear" y cumpla su contrato sin tocar el plano de auditoría:
- la telemetría nativa de FastAPI queda apagada y ninguna excepción deja su mensaje ni su stack en un span o en un exportador (C1, C2);
- `agentcore serve` configura trazas y logs JSON con las variables `OTEL_*` estándar y vacía el exportador al salir;
- cada turno abre en vivo un `invoke_agent`, con hijos `agentcore.decide`, `agentcore.rule` y `execute_tool` derivados de sus eventos;
- `TurnResult.trace_id` es el trace id real del request;
- la transferencia emite `agentcore.transfer` y el `invoke_agent` del run destino lleva un *span link* hacia él (ADR 0021 D9, spec de transferencia §8).

Con los mismos puertos, el motor sigue produciendo **los mismos eventos y los mismos hashes** con telemetría o sin ella, y en el replay la telemetría es no-op.

**Architecture:**
- **M9 (`agent_core/api`):** `FastAPI(telemetry=FASTAPI_TELEMETRY_OFF)`. El span `agentcore.api.request` pasa a `record_exception=False` y `set_status_on_exception=False`; en el camino de error marca `error.type` y `http.response.status_code`. El middleware publica en un `ContextVar` de `agent_telemetry` el trace id del request (el de OTel o, sin traza, el de respaldo del `IdSource`).
- **`agent_telemetry`:**
  - provider propio, sin instalar el global de OTel;
  - `tracer(name)` con `schema_url` y `shutdown_tracing()`;
  - `span()`:
    - no lanza por falta de `bind` (no-op con un aviso; modo estricto para pruebas);
    - nunca registra excepciones;
    - filtra con una lista cerrada de atributos;
    - acepta `links`, `context` y `attributes`;
  - `record_span()` para hijos con tiempos explícitos;
  - `bind(agent=…)`, `bind_trace_id()` y `current_trace_id()` con respaldo;
  - `JsonLogFormatter` con `timestamp` y `exc_type`.
- **Composition (`agent_core/composition`):**
  - `observability.py`: `setup_observability(env)` lee las `OTEL_*` estándar del `env` inyectado, configura las trazas (`Resource`, sampler, OTLP/HTTP), instala el formatter JSON en root, silencia `openai`, `httpx` y `httpcore`, y devuelve un `Observability` con `shutdown()`;
  - `telemetry.py`: `OtelTurnTelemetry`, la implementación real de `TurnTelemetry`, más `derived_spans(events)` y `TransferLink`;
  - `RequestTraceIds` reemplaza a `DerivedTrace`.
- **M4 (`agent_core/turn`):**
  - puerto local `TurnTelemetry` (`TurnScope`, `TurnSpan`, `TransferSpan`, `TransferOutcome`) en `turn/ports.py`; no-op por defecto (`turn/telemetry.py`);
  - `handle_turn`, `start_run` y `_continue_in_target` abren un turno de telemetría;
  - cada `EventChain.append` del turno (`_finish` y `TurnEventSink`) se informa con `span.record(events)`;
  - `_resolve_transfer` abre `span.transfer(transfer_id)` y `_continue_in_target` pasa su `link`.
  - M4 no importa `agent_telemetry` ni OTel; `.importlinter` lo prohíbe (F11).
- **Serve:** `run_serve` llama a `setup_observability`, inyecta el tracer en el gateway y `OtelTurnTelemetry` en el motor, arranca uvicorn con `log_config=None` y `access_log=False`, y en `finally` llama a `observability.shutdown()`.

**Tech Stack:** Python 3.12, FastAPI 0.142.1 (lock), Starlette 1.7.0, uvicorn 0.54.0, `opentelemetry-api`/`-sdk` 1.45.0, `opentelemetry-exporter-otlp-proto-http` 0.66b0 (todo en `uv.lock`), pytest con `InMemorySpanExporter`, FastAPI `TestClient`, `uv`.

**Insumos:**
- Auditoría: `.superpowers/sdd/2026-09-30-transferencia-entre-agentes/audit-observability-report.md` (C1, C2, I1 a I6, M1 a M5 y la tabla de 7 pasos).
- Intento anterior: `.../task-otel-report.md` (NEEDS_CONTEXT, opciones A1, A2, B y C).
- ADR 0003 (#1, #4 y la enmienda de métricas), ADR 0021 D9.
- `docs/specs/motor/m11-auditoria-transcript-replay.md` §3.2, `m04-ciclo-del-turno.md` (§2, §3.7, §3.8, §16), `m09-acceso-y-api.md` §3.7.
- Spec de transferencia §8 y §12.15; `2026-09-28-llm-gateway-design.md` §3.5; `TEMAS-ABIERTOS-PENDIENTES.md` §11 (línea 64) y #19; `00-indice.md:226`.

## Global Constraints

- Código en inglés: identificadores, comentarios y docstrings. Los mensajes de log y de error que ya están en español siguen en español. `docs/` y `README.md` en español.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`. **La telemetría no consume `Clock` ni `IdSource`:** los ids y tiempos de los spans en vivo salen del SDK de OTel, dentro de `agent_telemetry` o de composition; los de los hijos derivados salen de `ts` y `latency_ms` de los eventos. `datetime.fromtimestamp(record.created, tz=UTC)` en el formatter de logs no viola la regla: no decide nada y no entra a eventos.
- **Determinismo (regla 3):** con los mismos puertos, el motor produce los mismos eventos y hashes con telemetría o sin ella. La telemetría solo **lee** eventos ya construidos, nunca los modifica, y no cambia el orden de las llamadas al `IdSource`.
- **PII (regla 6):**
  - ningún atributo de span sale de un valor de payload: ni `args`, `result`, `error`, `inputs`, `value`, `p_cal` o `top_k`, ni texto del cliente;
  - los atributos son de una **lista cerrada** (`agent_telemetry.ALLOWED_ATTRIBUTES`);
  - nunca se registra el mensaje ni el stack de una excepción, solo su tipo.
- Fronteras de `.importlinter`: `agent_telemetry` no importa `agent_core`; M4 no importa `agent_telemetry` (F11); composition importa todo.
- **No se toca M0** (`agent_core/domain`, `agent_core/ports`): ni `SCHEMA_VERSION` ni `contracts/`. Si una tarea parece necesitarlo, se detiene y pregunta. `TurnScope` y `TransferOutcome` son tipos locales de M4, no de M0.
- **Los seis fixtures de `tests/fixtures/runs/` no cambian ni un byte.** `tests/composition/test_replay_fixtures.py::test_el_fixture_committeado_esta_vigente` lo vigila, y la tarea 4 agrega la misma comprobación **con** telemetría real activa.
- **Aislamiento de pruebas:** ninguna prueba deja un provider configurado (`agent_telemetry.setup._PROVIDER`), handlers nuevos en el root logger ni niveles cambiados en `openai`/`httpx`. Se usan las fixtures `otel` y `root_logging` de `tests/support/otel.py` (tarea 2).
- Cada tarea termina con estos comandos en verde:
  - `uv run pytest <carpetas tocadas>`
  - `uv run lint-imports`
  - `uv run mypy`
  - `uv run ruff check .`
  - `uv run agentcore contracts --check`
- Cada tarea actualiza en el mismo commit el spec del módulo que toca. La tarea 7 reconcilia el resto de la documentación.
- **Sin docker:** `tests/integration` se omite. Ninguna tarea cambia SQL.
- Commits con estas líneas al final (usar `git commit -F msg.txt` con el archivo en el scratchpad):
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Jnvu6ofA2xGYztmAgtpKMs
  ```
  Si la tarea la implementa otro modelo, va su nombre en `Co-Authored-By`.
- Rama `feat/transferencia-otel` (= `origin/main` `ebbeaf7`). No se cambia de rama, no se hace push y no se abre PR.
- Esta rama **no tiene la fase 7 de la transferencia** (`EngineDeps.directory` vive en `feat/transferencia-demo`). Las pruebas de transferencia usan el harness de M4 (`tests/m04/harness.py`, `World.reception`), que ya monta `directory/list`.

## Decisiones

### Del usuario (fijas, no se reabren)

| # | Decisión |
|---|---|
| U1 | Se **apaga la telemetría propia de FastAPI**, con una prueba de que, con `OTEL_EXPORTER_OTLP_ENDPOINT` definido, no se instala ningún provider global y su plano de logs y métricas queda apagado. |
| U2 | Variables **`OTEL_*` estándar** (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER`, …); ninguna `AGENTCORE_*` propia para telemetría. |
| U3 | `trace_id` **por request**: `TurnResult.trace_id` es el trace id OTel real del request (reemplaza a `DerivedTrace` y su `trace-{turn_id}`). Sin traza activa hay un respaldo determinista (`IdSource`), el mismo que en `problem+json`. |
| U4 | **Spans híbridos:**<br>• Un `invoke_agent` en vivo por turno, abierto por M4 a través de un `Protocol` local `TurnTelemetry`: no-op por defecto en M4, la implementación real en composition sobre `agent_telemetry`.<br>• Al cerrar, los hijos `decide`, `rule` y `execute_tool` se derivan de los eventos del turno, con inicio y fin explícitos sacados de `ts` y `latency_ms`.<br>• Sin ids ni tiempos del `Clock` o del `IdSource` del motor; los de OTel quedan en la capa de telemetría.<br>• La telemetría no cambia eventos ni hashes y es no-op en el replay.<br>• `invoke_agent` enlaza `run_id`, `turn_id`, `session_id`, release y agente, con una lista cerrada de atributos y sin PII. |
| U5 | Atributos de la transferencia: `agentcore.transfer.id`, `.from_agent`, `.to_agent`, `.to_release_id` y `.outcome` (`transferred` \| `rejected`), más `.reason_code` en el rechazo.<br>• En un rechazo, `to_agent` va solo si `transfer_rejected` lo repite, y nunca hay `to_release_id`.<br>• Un *span link* va del `invoke_agent` del run destino al span `agentcore.transfer`. Ese span se abre al resolver la transferencia; el puerto devuelve un handle opaco y el `invoke_agent` del destino se crea en `_continue_in_target` con `links=[handle]`.<br>• No se persiste nada, no se sube `SCHEMA_VERSION` y M0 no cambia. |

### Que fija este plan (derivadas del código; cada una tiene su pregunta abajo si es una elección de diseño)

| # | Decisión | Por qué / dónde se verificó |
|---|---|---|
| F1 | `FASTAPI_TELEMETRY_OFF = {"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False}` en `agent_core/api/app.py` y `FastAPI(..., telemetry=FASTAPI_TELEMETRY_OFF)`. | **Verificado en el paquete instalado** (fastapi 0.142.1):<br>• `applications.py:1033-1045`: los valores por defecto son `True` y se mezclan con el dict;<br>• `telemetry/_runtime.py:93-97`: `_configure_from_environment` sale enseguida con `auto_configure=False`;<br>• `telemetry/_asgi.py:172-195`: `NativeTelemetry.enabled()` es `False` con los tres planos apagados y `__call__` pasa de largo (`applications.py:1210-1215`).<br>**Comprobado en el scratchpad (2026-10-02):** con `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:9`, `enabled() is False`, los tres providers globales son los mismos objetos antes y después del lifespan y `_runtime._owned == []`. Con `FastAPI()` sin config quedan `TracerProvider`, `MeterProvider` y `LoggerProvider` SDK y tres componentes propios, más reintentos ruidosos a `/v1/metrics`. Se apagan los cinco y no solo `logs`/`metrics`: U1 dice "apagar" y así `agentcore.api.request` es el único span de servidor, sin `url.path` ni `url.query` (C1). |
| F2 | El middleware de `api/tracing.py` abre su span con `record_exception=False` y `set_status_on_exception=False`. En una excepción pone `error.type = type(exc).__name__`, `http.response.status_code = 500` y `Status(ERROR)` sin descripción, y relanza. Con una respuesta `>= 500`, `Status(ERROR)`. | C2. Starlette ejecuta el handler de `Exception` en `ServerErrorMiddleware`, que está **por fuera** del middleware `http` (`starlette/middleware/errors.py:176-186`): la excepción cruza `call_next` y llega al span. Es el mismo patrón que `gateway.py:60-74`. |
| F3 | `agent_telemetry.setup_tracing` **deja de instalar el provider global de OTel**. `get_provider()` devuelve el provider propio o un `NoOpTracerProvider`. Todo el código de agentcore pide sus tracers con `agent_telemetry.tracer(name)`. `shutdown_tracing()` vacía, cierra y vuelve a no-op. | I5 e higiene de pruebas. Hoy el primer `setup_tracing` del proceso fija el global para siempre (`setup.py:32-33`), y el `chat` del gateway queda en un provider cerrado tras reconfigurar. Con FastAPI apagado, nadie necesita el global: `trace.get_current_span()` (security log, formatter, `current_trace_id`) depende del contexto, no del provider. **Pregunta 2.** |
| F4 | `setup_observability(env)` lee del `env` **inyectado** este subconjunto de la especificación del SDK de OTel:<br>• `OTEL_SDK_DISABLED`;<br>• `OTEL_TRACES_EXPORTER` (`otlp` \| `none`);<br>• `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` o `OTEL_EXPORTER_OTLP_ENDPOINT` (+ `/v1/traces`);<br>• `OTEL_EXPORTER_OTLP[_TRACES]_PROTOCOL` (solo `http/protobuf`);<br>• `OTEL_EXPORTER_OTLP[_TRACES]_HEADERS`, que nunca se imprimen;<br>• `OTEL_SERVICE_NAME` y `OTEL_RESOURCE_ATTRIBUTES`;<br>• `OTEL_TRACES_SAMPLER` y `OTEL_TRACES_SAMPLER_ARG` (`always_on`, `always_off`, `traceidratio` y sus `parentbased_*`).<br>Sin endpoint, las trazas quedan apagadas y los logs JSON siguen. Un valor inválido es un error de arranque (exit 2). | U2. Se pasan valores explícitos al SDK (`sampler=`, `Resource(attrs)`, `OTLPSpanExporter(endpoint=, headers=)`) para no depender de `os.environ` en las pruebas. **Excepción:** certificados, compresión y timeout del exportador los sigue leyendo el SDK de `os.environ`; en producción `env is os.environ`. Valores por defecto del `Resource`:<br>• `service.name = "agentcore"`;<br>• `service.version` = versión del paquete `agent-core`;<br>• `OTEL_SERVICE_NAME` gana sobre `service.name` de `OTEL_RESOURCE_ATTRIBUTES`, como en la especificación.<br>**Pregunta 12.** |
| F5 | uvicorn arranca con `log_config=None` y `access_log=False`. | **Hallazgo nuevo, fuera de la auditoría:**<br>• Starlette **relanza** la excepción después de responder el 500 (`errors.py:186`), y uvicorn la registra con `exc_info` en `uvicorn.error`: mensaje y stack (`uvicorn/protocols/http/h11_impl.py:415-416`, `httptools_impl.py:426-427`).<br>• Con la configuración de logging propia de uvicorn, ese texto sale a stderr sin pasar por nuestro formatter. Con `log_config=None`, los registros de uvicorn propagan al root, y `JsonLogFormatter` emite solo `exc_type`.<br>• El access log de uvicorn escribe la IP del cliente y la ruta con `session_id`/`run_id`; el span `agentcore.api.request` ya cubre método y status.<br>**Pregunta 7.** |
| F6 | `JsonLogFormatter` emite un conjunto cerrado de campos:<br>• `timestamp` (ISO 8601 UTC con milisegundos, de `record.created`);<br>• `level`, `logger` y `message`;<br>• `exc_type`, solo si hay `exc_info`;<br>• los de `bind` (`run_id`, `turn_id`, `session_id`, `agentcore.release`, `agentcore.agent`);<br>• `trace_id`.<br>Nunca `exc_text`, el stack ni campos `extra`. Nivel del root: `INFO`. | I3 y regla 6. **Preguntas 8 y 14.** |
| F7 | `agent_telemetry.span()`:<br>• **sin `run_id` o `agentcore.release`** y fuera del modo estricto, entrega `trace.INVALID_SPAN` (no-op) y avisa **una vez por nombre de span** en el logger `agent_telemetry`; con `configure(strict=True)` lanza `MissingTelemetryContext`, como hoy;<br>• **nunca registra la excepción** (`record_exception=False`, `set_status_on_exception=False`); marca `error.type` y `Status(ERROR)` sin descripción;<br>• **descarta los atributos fuera de `ALLOWED_ATTRIBUTES`**, o lanza `ValueError` en modo estricto. | I4 (el usuario pidió degradar a no-op), C2 aplicado al motor y regla 6. **Preguntas 9 y 16.** |
| F8 | El trace id del turno se resuelve por capas:<br>(1) el trace id OTel válido del contexto;<br>(2) si no hay, el respaldo que el middleware de M9 sacó del `IdSource` (`ids.new_id(IdKind.event)`, como hoy) y publicó con `bind_trace_id`;<br>(3) fuera de un request (replay, `record`, evaluación del registry, pruebas en proceso), `trace-{turn_id}`, sin consumir el `IdSource`.<br>`agent_telemetry.current_trace_id()` implementa (1) y (2); `RequestTraceIds.current(turn_id)` (composition) agrega (3). `problem+json`, `GET /runs/{id}` y el `TurnResult` del mismo request dan el mismo valor. | U3. Si M4 pidiera un id al `IdSource` para el respaldo, correría la secuencia de ids, y `RecordedIds` y los seis fixtures (`event_id` va en el YAML) cambiarían de bytes. **Pregunta 1.** |
| F9 | Forma del puerto (`agent_core/turn/ports.py`):<br>• `TurnTelemetry.turn(scope, links=()) -> AbstractContextManager[TurnSpan]`;<br>• `TurnSpan.record(events)` y `TurnSpan.transfer(transfer_id) -> AbstractContextManager[TransferSpan]`;<br>• `TransferSpan.link: object \| None` y `TransferSpan.finish(outcome)`.<br>Dónde se abre:<br>• `handle_turn`: después del lease y de releer el run, y cubre `_process` y el `commit`;<br>• `start_run`: cubre el alta, `advance` y el `commit`;<br>• `_continue_in_target`: cubre el `_process` del destino, porque el `commit` es el del turno.<br>Los turnos deduplicados, los `409` y los `410` no abren span (igual que no emiten `turn_completed`, m04 §3.7). `record` se llama tras **cada** `EventChain.append` del turno: `_finish` y `TurnEventSink.record`, que es por donde pasan los `tool_called` de M3. `sweep` no abre spans. | U4 y el código: `turn/engine.py:306` y `buffer.py:84-87` son los dos únicos `append` dentro de un turno. **Pregunta 6.** |
| F10 | El `invoke_agent` del destino es **hermano** del del origen: su padre es el contexto que estaba activo al abrir el `invoke_agent` del origen, que viaja en el handle. Lleva el link al span `agentcore.transfer`, que es hijo del `invoke_agent` del origen. | Así la duración del `invoke_agent` de recepción no incluye el trabajo del especialista. Origen y destino corren en el mismo request y la misma traza (ADR 0021), así que el trace id del `TurnResult` sigue siendo el del request. **Pregunta 3.** |
| F11 | Se agrega `agent_telemetry` a `forbidden_modules` del contrato `turn` de `.importlinter`. | **Verificado:** hoy `.importlinter:275-292` no lo prohíbe (solo lista módulos `agent_core.*`). La auditoría y U4 piden ir por un puerto, y m04 ("Usa: M0, M2, M3, M5, M6, M10, M11") no lo lista. **Pregunta 4.** |
| F12 | Atributos de `invoke_agent`:<br>• los de `bind`: `run_id`, `turn_id`, `session_id`, `agentcore.release` y `agentcore.agent = "id@versión"`;<br>• `gen_ai.operation.name = "invoke_agent"` y `gen_ai.agent.name = <id del agente>`;<br>• `agentcore.entry` (`start_run` \| `turn`), `agentcore.principal_type` y `agentcore.locale`;<br>• con un `EngineError`, `agentcore.problem_code`.<br>`from_agent` y `to_agent` de la transferencia son **ids** de agente, sin versión (la versión del destino la da `to_release_id`), igual que `transfer_rejected.to_agent`. | ADR 0003 #4 fija los cuatro nombres de correlación. `agentcore.agent` sale de M11 §3.2 y de `tests/m11/test_telemetry.py:28`. `gen_ai.agent.name` sale de la semconv GenAI 1.44.0 fijada. **Pregunta 5.** |
| F13 | Hijos derivados (`composition/telemetry.py:derived_spans`):<br>• `decision_made` → `agentcore.decide`, de `ts − latency_ms` a `ts`, con `agentcore.decision.{id,model,provider,fallback_depth,tokens}`;<br>• `rule_evaluated` → `agentcore.rule`, instantáneo en `ts`, con `agentcore.node`, `agentcore.rule.result` y `agentcore.rule.policy` si existe;<br>• `tool_called` → `execute_tool`, de `ts − latency_ms` a `ts`, con `gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `agentcore.tool`, `agentcore.node`, `agentcore.tool.status` y `agentcore.tool.attempt`.<br>Nada más genera hijos: tampoco `agent_step` ni `knowledge_read`, porque el `chat` en vivo del gateway ya cubre el LLM. | `ts` se toma **después** de la llamada: `decision/service.py:103` (`latency_ms` medido en la línea 123). En `tool_called` hay que confirmarlo en el paso 1 de la tarea 4 (`actions/execution.py:202`). **Pregunta 13.** |
| F14 | Gateway:<br>• `gen_ai.provider.name = "openai"` (el protocolo del SDK) y el alias va en `agentcore.endpoint_alias`;<br>• `gen_ai.response.finish_reasons = [finish_reason]` también en los caminos de error;<br>• span `chat {gen_ai.request.model}`;<br>• el `chat` lleva los atributos de `bind` del turno;<br>• tracer de `agent_telemetry.tracer(...)` con `schema_url`, inyectado por serve. | M1 e I5. **Pregunta 10.** |
| F15 | `pyproject.toml` no acota `fastapi`; una prueba (T-M9-15) vigila que la configuración siga apagando todo. | M2 de la auditoría sugiere `<0.143`. **Pregunta 11.** |
| F16 | Un reintento deduplicado (`client_turn_id` o `Idempotency-Key` repetidos) devuelve el `trace_id` del intento original, que está guardado en el resultado. Se documenta en ADR 0003 #4. | Auditoría I2, nota final. **Pregunta 15.** |

---

## Preguntas para el usuario (antes de ejecutar las tareas marcadas)

Cada una es una elección de diseño que el plan tomó más allá de U1 a U5. El plan sigue la opción recomendada; si la respuesta es otra, cambian las tareas indicadas.

1. **(T3, F8) Respaldo del trace id fuera de un request.** Dentro de un request, el respaldo es el id del `IdSource` que ya genera M9 (`api/tracing.py:29`) y que ahora se propaga a M4 por un `ContextVar`. Fuera de un request (replay, `agentcore record`, evaluación del registry, motor en proceso), el plan deja `trace-{turn_id}`, sin pedir un id al `IdSource`. Pedirlo desde M4 correría la secuencia de ids y cambiaría los bytes de los seis fixtures. ¿Se acepta? Alternativa: un `IdKind.trace` nuevo, que es un cambio de M0 y está fuera de alcance.
2. **(T2, F3) ¿`setup_tracing` deja de instalar el provider global de OTel?**
   - **Recomendado:** sí. Aísla las pruebas y evita el provider cerrado de I5.
   - **Costo:** una librería instrumentada por terceros (p. ej. `opentelemetry-instrumentation-httpx`) no vería nuestro provider sin cablearla a mano.
   - **Alternativa:** seguir fijándolo una sola vez por proceso y resetear solo `_PROVIDER` en las pruebas.
3. **(T5, F10) ¿El `invoke_agent` del destino es hermano o hijo del `invoke_agent` del origen?** Recomendado: hermano, con el link. Hijo es más simple (el anidamiento sale solo), pero el span de recepción absorbería la latencia del especialista.
4. **(T3, F11) ¿Se agrega `agent_telemetry` a lo que `.importlinter` prohíbe a M4?** Recomendado: sí. Hoy está permitido por omisión.
5. **(T3 y T5, F12) Nombres de atributos.**
   - `agentcore.agent = "id@versión"` y `gen_ai.agent.name = id`.
   - `agentcore.entry`, `agentcore.principal_type` y `agentcore.locale` en `invoke_agent`.
   - `from_agent` y `to_agent` como ids sin versión.
   - ¿Se cambia el nombre del span a `invoke_agent {gen_ai.agent.name}`, como recomienda la semconv? El plan **no** lo cambia: deja la constante `INVOKE_AGENT`.
6. **(T3, F9) Alcance temporal del `invoke_agent`.** En `handle_turn` y `start_run` incluye el `commit` de la UoW. En el destino de una transferencia no lo incluye, porque el `commit` es uno solo y ocurre después. ¿Se acepta?
7. **(T2, F5) uvicorn con `log_config=None` y `access_log=False`.** Sin `log_config=None`, cualquier 500 deja hoy en stderr el mensaje y el stack de la excepción, por el `raise exc` de Starlette y el `logger.error(..., exc_info=exc)` de uvicorn. ¿Se apaga también el access log? Recomendado: sí, por la IP y las rutas con ids. Si se quiere conservarlo, el plan lo deja en `INFO` por el root JSON, con la IP en el mensaje.
8. **(T2, F6) Nivel de log.** El plan fija `INFO` en root, sin variable ni flag nuevos. ¿Se quiere `agentcore serve --log-level`? Sería un flag, no una variable `AGENTCORE_*`.
9. **(T2, F7) `span()` sin `bind`.** No-op más un aviso por nombre de span y proceso; el modo estricto queda para pruebas. ¿Se acepta?
10. **(T6, F14) GenAI en el gateway.**
    - (a) `gen_ai.provider.name = "openai"` siempre, con el alias en `agentcore.endpoint_alias`;
    - (b) un campo opcional `provider` por endpoint en `LLM_ENDPOINTS` (por defecto `"openai"`).

    El plan hace (a). Además, ¿se renombra el span a `chat {model}`? El plan lo hace, como recomienda la semconv. La constante `CHAT` sigue en `"chat"` para `gen_ai.operation.name`.
11. **(F15) ¿Se acota `fastapi` a `<0.143`?** Recomendado: no. La prueba T-M9-15 falla si una versión nueva agrega un plano encendido por defecto.
12. **(T2, F4) Subconjunto de `OTEL_*`.** Solo OTLP/HTTP-protobuf (el exportador gRPC no es dependencia), `OTEL_TRACES_EXPORTER` en `otlp` o `none` (sin `console`) y los seis samplers estándar. ¿Basta?
13. **(T4, F13) Hijos derivados.** Solo `decide`, `rule` y `execute_tool`, con los atributos de F13. ¿Se agrega `agent_step` (nodo `agent`, ADR 0019) o `knowledge_read` (M12)?
14. **(T2, F6) Logs sin campos `extra`.** Solo el conjunto cerrado de F6; cualquier dato tiene que ir en el mensaje, que ya sigue la regla "solo tipos y motivos cortos". ¿Se acepta?
15. **(T7, F16) `trace_id` de un reintento deduplicado:** el del intento original. ¿Se documenta así o se prefiere el del request del reintento? Lo segundo exige reescribir el resultado guardado.
16. **(T2, F7) Lista cerrada de atributos.** En producción, un atributo fuera de la lista se **descarta** en silencio; en modo estricto, `ValueError`. ¿O se prefiere que lance siempre?

---

## Review Focus

1. **Determinismo y replay.**
   - Grabar los seis caminos con `OtelTurnTelemetry` real y un `InMemorySpanExporter` da **exactamente** los mismos bytes que los fixtures commiteados, con `event_id`, `ts` y `hash` incluidos, y el exportador no queda vacío (`test_recording_with_live_telemetry_keeps_every_fixture_byte_identical`, T4).
   - El motor del replay usa la telemetría no-op (`test_the_replay_engine_has_no_telemetry`, T4).
   - `OtelTurnTelemetry` no recibe ni `Clock` ni `IdSource` en su constructor (revisión de código).
2. **Lista cerrada de atributos y PII.**
   - Toda clave de todo span exportado está en `ALLOWED_ATTRIBUTES`.
   - Ninguna hoja de `args`, `result`, `inputs`, `value` ni el texto del cliente aparece en un atributo (`test_no_span_attribute_carries_a_payload_value`, T4).
   - Un `RuntimeError("SECRETO…")` en un handler no deja el texto en ningún span ni en el log JSON (T-M9-16, T1 y T6).
3. **Propagación de contexto al threadpool.** En `POST /v1/runs` por `TestClient`:
   - el `invoke_agent` es hijo de `agentcore.api.request` y comparte su trace id;
   - `first_turn.trace_id` y `trace_id` son ese mismo id;
   - sin OTel, son el respaldo del middleware (`test_the_turn_trace_id_is_the_request_trace_*`, T3).
4. **Aislamiento de los providers globales.**
   - FastAPI no instala nada (T-M9-15).
   - `setup_tracing` no toca `trace.get_tracer_provider()` (T2).
   - Las fixtures `otel` y `root_logging` dejan el proceso como estaba. La suite completa se corre **dos veces** en la tarea 7, una con `-p no:randomly` si el plugin está, para detectar fugas de orden.
5. **Link de la transferencia.**
   - El `invoke_agent` del destino tiene exactamente un link, al span `agentcore.transfer`.
   - Su padre es el del origen (F10) y la traza es la misma.
   - En un rechazo no hay `to_release_id`, y `to_agent` aparece solo si el evento lo trae (T5).
6. **El `trace_id` del turno es el del request** y coincide con el de `problem+json` cuando el mismo request falla después del motor (T3).

---

## Mapa de archivos

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `agent_core/api/app.py` | `FASTAPI_TELEMETRY_OFF` y `FastAPI(telemetry=…)` | 1 |
| `agent_core/api/tracing.py` | span sin excepción, `error.type` y status en error (T1); tracer de `agent_telemetry` y `bind_trace_id` (T3) | 1, 3 |
| `agent_core/api/problems.py` | log del 500 con `trace_id`, ubicación y ruta | 6 |
| `agent_telemetry/semconv.py` (nuevo) | `SEMCONV_VERSION` y `SCHEMA_URL` (rompe el ciclo `setup` ↔ `spans`) | 2 |
| `agent_telemetry/setup.py` | provider propio sin global, `tracer()`, `shutdown_tracing()`, `resource`/`sampler`/`headers` | 2 |
| `agent_telemetry/spans.py` | `span()` no-op/estricto, sin excepciones, lista cerrada, `links`/`context`/`attributes`; `record_span`; `set_attributes`; `TRANSFER`; `current_trace_id` con respaldo | 2, 3, 5 |
| `agent_telemetry/context.py` | `bind(agent=…)`, `correlation()`, `bind_trace_id()` | 2, 3 |
| `agent_telemetry/logging.py` | `timestamp`, `exc_type` y `trace_id` con respaldo | 2 |
| `agent_telemetry/__init__.py` | exportes nuevos | 2, 3, 5 |
| `agent_core/composition/observability.py` (nuevo) | `tracing_config`, `setup_observability`, `Observability`, `install_json_logging`, `quiet_sdk_loggers` | 2 |
| `agent_core/composition/serve.py` | `run_serve` con observabilidad, uvicorn `log_config=None`, `finally: shutdown`; `build_api_deps(telemetry=…)` | 2, 3 |
| `agent_core/composition/serve_ports.py` | `resolve_ports(..., tracer=…)`; mensaje sin `{exc}` | 2, 6 |
| `agent_core/composition/registry.py` | mensaje sin `{exc}` | 6 |
| `agent_core/cli.py` | `llm-smoke` usa `quiet_sdk_loggers()` | 2 |
| `agent_core/adapters/llm/gateway.py` | tracer de `agent_telemetry`; atributos GenAI y de correlación | 2, 6 |
| `agent_core/turn/ports.py` | `TurnScope`, `TransferOutcome`, `TransferSpan`, `TurnSpan` y `TurnTelemetry` | 3 |
| `agent_core/turn/telemetry.py` (nuevo) | `NoTurnTelemetry`, `NO_SPAN` | 3 |
| `agent_core/turn/frame.py` | `TurnFrame.span` y `TurnFrame.transfer_link` | 3, 5 |
| `agent_core/turn/buffer.py` | `TurnEventSink(observe=…)` | 3 |
| `agent_core/turn/engine.py` | turnos de telemetría; `record`; transferencia y link (T5); `log.warning` en `_release_quietly` (T6) | 3, 5, 6 |
| `agent_core/turn/__init__.py` | exportes del puerto y del no-op | 3 |
| `agent_core/composition/telemetry.py` (nuevo) | `OtelTurnTelemetry`, `TransferLink`, `derived_spans`, `DerivedSpan` | 3, 4, 5 |
| `agent_core/composition/engine.py`, `__init__.py` | `EngineDeps.telemetry`; `RequestTraceIds` en lugar de `DerivedTrace` | 3 |
| `.importlinter` | M4 no importa `agent_telemetry` | 3 |
| `testing/fakes/telemetry.py` (nuevo) | `RecordingTelemetry` | 3 |
| `testing/engine_world.py`, `testing/replay/scenarios.py` | `EngineWorld(telemetry=…)`, `record_scenario(..., telemetry=…)` | 3, 4 |
| `tests/support/otel.py` (nuevo) | fixtures `otel` (exportador en memoria y reset) y `root_logging` | 2 |
| `tests/m09/test_fastapi_telemetry.py` (nuevo), `tests/m09/test_problems.py` | T-M9-15, T-M9-16 y el log del 500 | 1, 6 |
| `tests/m11/test_telemetry.py`, `tests/m11/test_json_logs.py` (nuevo) | `span()` no-op/estricto/lista cerrada; formatter | 2 |
| `tests/composition/test_observability.py` (nuevo) | `tracing_config`, `setup_observability`, `run_serve` | 2 |
| `tests/m04/test_telemetry_port.py` (nuevo), `tests/m04/harness.py` | puerto en M4 (T-M4-22) y transferencia (T-TR-16, mitad M4) | 3, 5 |
| `tests/composition/test_turn_telemetry.py` (nuevo) | `invoke_agent`, `trace_id`, threadpool, hijos derivados, determinismo y PII | 3, 4 |
| `tests/composition/test_transfer_spans.py` (nuevo) | span de la transferencia y link sobre OTel | 5 |
| `tests/u05/test_observability.py` | atributos GenAI (T-U5-19) | 6 |
| `docs/…`, `README.md` | ver tarea 7 y el paso de spec de cada tarea | 1-7 |

Orden y dependencias:
- T1 y T6 (salvo el gateway) son independientes.
- T2 precede a T3: T3 usa `span(links, context, attributes)`, `record_span`, `bind(agent)` y la fixture `otel`.
- T4 y T5 dependen de T3.
- T6 usa `correlation()` de T2.
- T7 va al final.

---

### Task 1: C1 y C2 — telemetría de FastAPI apagada y `agentcore.api.request` sin excepciones

**Modelo recomendado:** sonnet.

**Files:**
- Modify: `agent_core/api/app.py`, `agent_core/api/tracing.py`
- Test: `tests/m09/test_fastapi_telemetry.py` (nuevo)
- Docs: `docs/specs/motor/m09-acceso-y-api.md` §3.7 y la tabla de pruebas (T-M9-15, T-M9-16); ADR 0003, nota de enmienda (ver paso 5)

**Interfaces:**
- Produce: `agent_core.api.app.FASTAPI_TELEMETRY_OFF: Final[TelemetryConfig]`.
- Sin cambios de firma en `create_app` ni en `install_tracing`.

- [ ] **Step 1: Write the failing tests**

`tests/m09/test_fastapi_telemetry.py`:

```python
"""C1 and C2 of the observability audit (U1): FastAPI's native telemetry is off and the request span never
records an exception's message or stack (T-M9-15, T-M9-16)."""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.telemetry import _runtime
from fastapi.testclient import TestClient
from opentelemetry import _logs, metrics, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_core.api.app import FASTAPI_TELEMETRY_OFF, Authenticate, create_app
from agent_telemetry import setup as telemetry_setup
from tests.m09.conftest import api_deps

SECRET = "SECRETO-cliente-123"


def _providers() -> tuple[object, object, object]:
    return trace.get_tracer_provider(), metrics.get_meter_provider(), _logs.get_logger_provider()


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """A private provider for the request span (no global: T2 removes `set_tracer_provider`)."""
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    monkeypatch.setattr(telemetry_setup, "_PROVIDER", provider)
    yield exp
    provider.shutdown()


def _boom(app: FastAPI, authenticate: Authenticate) -> None:
    @app.get("/v1/boom")
    def boom() -> None:
        raise RuntimeError(SECRET)


def test_native_telemetry_is_off_even_with_an_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:  # T-M9-15
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    before, owned = _providers(), list(_runtime._owned)
    app = create_app(api_deps()[0])
    assert {k: app._telemetry[k] for k in FASTAPI_TELEMETRY_OFF} == FASTAPI_TELEMETRY_OFF  # type: ignore[literal-required]
    assert all(value is False for value in FASTAPI_TELEMETRY_OFF.values())
    assert not app._native_telemetry.enabled()
    with TestClient(app, raise_server_exceptions=False) as client:  # runs the lifespan, where FastAPI auto-configures
        client.get("/v1/no-existe")
    after = _providers()
    assert all(a is b for a, b in zip(before, after, strict=True))  # no global tracer, meter or logger provider
    assert _runtime._owned == owned  # no OTLP exporter of FastAPI's logs or metrics plane


def test_an_unhandled_error_leaves_no_text_on_the_request_span(exporter: InMemorySpanExporter) -> None:  # T-M9-16
    deps, _ = api_deps()
    client = TestClient(create_app(replace(deps, extensions=(_boom,))), raise_server_exceptions=False)
    response = client.get("/v1/boom")
    assert response.status_code == 500 and SECRET not in response.text
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "agentcore.api.request"]
    attrs = dict(span.attributes or {})
    assert attrs["error.type"] == "RuntimeError" and attrs["http.response.status_code"] == 500
    assert span.status.status_code == StatusCode.ERROR and not span.status.description
    assert all(event.name != "exception" for event in span.events)
    seen = [str(attrs), *(str(dict(e.attributes or {})) for e in span.events)]
    assert all(SECRET not in text for text in seen)


def test_a_handled_5xx_marks_the_span_as_error_without_description(exporter: InMemorySpanExporter) -> None:
    deps, _ = api_deps()

    def internal(app: FastAPI, authenticate: Authenticate) -> None:
        from agent_core.domain import EngineError, ProblemCode

        @app.get("/v1/internal")
        def fail() -> None:
            raise EngineError(ProblemCode.internal_error, "no-sale")

    client = TestClient(create_app(replace(deps, extensions=(internal,))), raise_server_exceptions=False)
    assert client.get("/v1/internal").status_code == 500
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "agentcore.api.request"]
    assert dict(span.attributes or {})["http.response.status_code"] == 500
    assert span.status.status_code == StatusCode.ERROR and not span.status.description
```

Nota para quien implemente: el fixture `exporter` usa `monkeypatch.setattr(telemetry_setup, "_PROVIDER", …)` porque T2 todavía no existe. En T2, este archivo pasa a usar la fixture `otel` de `tests/support/otel.py`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m09/test_fastapi_telemetry.py -q`
Expected: FAIL.
- `ImportError: cannot import name 'FASTAPI_TELEMETRY_OFF'`.
- Tras crear la constante sin usarla: `enabled()` da `True`, y el span tiene un evento `exception` con `exception.message = "SECRETO-cliente-123"` y sin `http.response.status_code`.

- [ ] **Step 3: Implement**

`agent_core/api/app.py`:

```python
from typing import Annotated, Final

from fastapi.telemetry import TelemetryConfig

# U1/C1: FastAPI >= 0.142 ships native OpenTelemetry on by default (tracing, metrics, logs, operation spans and
# auto-configuration from OTEL_* env vars). Its logs plane exports exception messages and stack traces, and its
# spans carry `url.path`/`url.query`. All of it is off: `agentcore.api.request` is the only server span and the
# exporter is configured explicitly by `agent_core.composition.setup_observability`.
FASTAPI_TELEMETRY_OFF: Final[TelemetryConfig] = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}
...
def create_app(deps: ApiDeps) -> FastAPI:
    app = FastAPI(title="agent-core", version="1.0.0", telemetry=FASTAPI_TELEMETRY_OFF)
```

`agent_core/api/tracing.py` (cuerpo del middleware):

```python
from opentelemetry.trace import Status, StatusCode

    @app.middleware("http")
    async def _trace(request: Request, call_next: RequestResponseEndpoint) -> Response:
        tracer = get_provider().get_tracer("agentcore.api")
        # C2: never an `exception` event (message, stack) nor a status description: only the type.
        with tracer.start_as_current_span(
            "agentcore.api.request",
            attributes={"http.request.method": request.method},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            ctx = span.get_span_context()
            request.state.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else ids.new_id(IdKind.event)
            try:
                response = await call_next(request)
            except Exception as exc:  # the 500 handler runs outside this middleware (ServerErrorMiddleware)
                span.set_attribute("error.type", type(exc).__name__)
                span.set_attribute("http.response.status_code", 500)
                span.set_status(Status(StatusCode.ERROR))
                raise
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            return response
```

Además, actualizar el docstring de `install_tracing`: la lista cerrada es `http.request.method`, `http.response.status_code` y `error.type`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/m09 -q`, después `uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`.
Expected: PASS. `contracts --check` no cambia: el OpenAPI no depende de `telemetry=`. Si cambia, detenerse y avisar.

- [ ] **Step 5: Spec and commit**

`m09-acceso-y-api.md` §3.7: agregar dos puntos (en español).
- La telemetría nativa de FastAPI está apagada (`FASTAPI_TELEMETRY_OFF`), así que `agentcore.api.request` es el único span de servidor.
- En un error, el span lleva `error.type` y `http.response.status_code`, y nunca el evento `exception` ni una descripción de estado.

Tabla de pruebas: T-M9-15 y T-M9-16 con `test_fastapi_telemetry`.

ADR 0003, sección "Decisión" #1: nota de enmienda (2026-10-02): "la telemetría nativa de FastAPI (≥ 0.142) se apaga; la exportación la configura la raíz de composición (paso 2 del refactor de observabilidad)".

```bash
git add agent_core/api tests/m09/test_fastapi_telemetry.py docs/specs/motor/m09-acceso-y-api.md docs/adr/0003-observabilidad-dos-planos.md
git commit -F msg.txt   # fix(api): turn off FastAPI native telemetry and never record exceptions on the request span
```

---

### Task 2: `setup_observability` y endurecimiento de `agent_telemetry`

**Modelo recomendado:** opus.

**Files:**
- Create: `agent_telemetry/semconv.py`, `agent_core/composition/observability.py`, `tests/support/otel.py`, `tests/m11/test_json_logs.py`, `tests/composition/test_observability.py`
- Modify:
  - `agent_telemetry/{setup,spans,context,logging,__init__}.py`;
  - `agent_core/api/tracing.py`: usa `agent_telemetry.tracer`;
  - `agent_core/composition/{serve,serve_ports,__init__}.py`;
  - `agent_core/adapters/llm/gateway.py`: solo el tracer;
  - `agent_core/cli.py`: `llm-smoke`;
  - `tests/m11/test_telemetry.py` y `tests/m09/test_fastapi_telemetry.py`: pasan a la fixture `otel`.
- Docs: `docs/specs/motor/m11-auditoria-transcript-replay.md` §2 (firma de `span`) y la decisión 15; README (lo completa la tarea 7).

**Interfaces:**
- Produce (`agent_telemetry`):
  - `tracer(name: str) -> Tracer`;
  - `shutdown_tracing() -> None`;
  - `setup_tracing(endpoint=None, exporter=None, *, resource=None, sampler=None, headers=None) -> TracerProvider`;
  - `span(name, *, attributes=None, links=(), context=None, **attrs)`;
  - `record_span(name, *, parent, start_ns, end_ns, attributes)`;
  - `set_attributes(span, attributes)`;
  - `configure(*, capture_content=None, strict=None)`;
  - `ALLOWED_ATTRIBUTES: frozenset[str]`;
  - `bind(..., agent=None)`;
  - `correlation() -> Mapping[str, str]`;
  - `SCHEMA_URL`.
- Produce (composition):
  - `tracing_config(env, *, version) -> TracingConfig | None`;
  - `setup_observability(env, *, version=None, exporter=None, stream=None) -> Observability`;
  - `Observability.tracer(name)` y `Observability.shutdown()`;
  - `ObservabilityConfigError(problems)`;
  - `quiet_sdk_loggers()`;
  - `resolve_ports(..., tracer: Tracer | None = None)`.

- [ ] **Step 1: Write the failing tests**

`tests/support/otel.py`:

```python
"""Test isolation for the operational plane: a private span exporter and a root logger left as found."""

import logging
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel


@pytest.fixture
def otel() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    tel.setup_tracing(exporter=exporter)
    tel.configure(capture_content=False, strict=True)
    try:
        yield exporter
    finally:
        tel.shutdown_tracing()
        tel.configure(capture_content=False, strict=False)


@pytest.fixture
def root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    quiet = {name: logging.getLogger(name).level for name in ("openai", "httpx", "httpcore")}
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, value in quiet.items():
            logging.getLogger(name).setLevel(value)
```

`tests/m11/test_telemetry.py` (cambios y pruebas nuevas; las existentes pasan a `otel` en lugar de `exporter`):

```python
pytest_plugins = ["tests.support.otel"]


def test_setup_tracing_never_touches_the_global_provider() -> None:
    from opentelemetry import trace

    before = trace.get_tracer_provider()
    tel.setup_tracing(exporter=InMemorySpanExporter())
    try:
        assert trace.get_tracer_provider() is before
    finally:
        tel.shutdown_tracing()


def test_after_shutdown_spans_are_no_op() -> None:
    exporter = InMemorySpanExporter()
    tel.setup_tracing(exporter=exporter)
    tel.shutdown_tracing()
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT) as s:
        assert not s.is_recording()
    assert exporter.get_finished_spans() == ()


def test_span_without_context_is_a_no_op_outside_strict_mode(caplog: pytest.LogCaptureFixture) -> None:  # I4
    tel.configure(strict=False)
    with tel.span(tel.CHAT) as s, tel.span(tel.CHAT):
        assert s is trace.INVALID_SPAN
    assert caplog.text.count("sin run_id") == 1  # one warning per span name


def test_span_without_context_is_rejected_in_strict_mode(otel: InMemorySpanExporter) -> None:
    with pytest.raises(tel.MissingTelemetryContext):
        with tel.span(tel.CHAT):
            pass


def test_an_exception_inside_a_span_leaves_only_its_type(otel: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError):
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT):
            raise RuntimeError("SECRETO-123")
    (span,) = otel.get_finished_spans()
    assert dict(span.attributes or {})["error.type"] == "RuntimeError"
    assert all(e.name != "exception" for e in span.events) and not span.status.description


def test_attributes_outside_the_allow_list_are_dropped_or_rejected(otel: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError):
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, attributes={"user.text": "x"}):
            pass
    tel.configure(strict=False)
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, attributes={"user.text": "x"}):
        pass
    assert "user.text" not in dict(otel.get_finished_spans()[-1].attributes or {})


def test_record_span_uses_the_given_times_and_parent(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT) as parent:
        tel.record_span(tel.RULE, parent=parent, start_ns=1_000, end_ns=5_000,
                        attributes={"agentcore.node": "n1", "agentcore.rule.result": True})
    rule = next(s for s in otel.get_finished_spans() if s.name == tel.RULE)
    assert (rule.start_time, rule.end_time) == (1_000, 5_000)
    assert rule.parent is not None and rule.parent.span_id == parent.get_span_context().span_id
    assert dict(rule.attributes or {})["run_id"] == "run-0001"


def test_spans_declare_the_pinned_schema(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass
    scope = otel.get_finished_spans()[0].instrumentation_scope
    assert scope is not None and scope.schema_url == tel.SCHEMA_URL
```

`test_span_without_context_is_rejected` (actual) se reemplaza por las dos pruebas de modo. `test_endpoint_uses_batch_processor_and_reconfiguring_shuts_down_the_previous_provider` se conserva y se agrega `tel.shutdown_tracing()` al final.

`tests/m11/test_json_logs.py`:

```python
"""ADR 0003 #4 and I3: JSON logs with timestamp, exception type only, and run/trace correlation."""

import json
import logging
import sys

import pytest

import agent_telemetry as tel

pytest_plugins = ["tests.support.otel"]


def _record(exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    record = logging.LogRecord("agentcore.api", logging.ERROR, __file__, 1, "error no controlado", None, exc_info)
    record.created = 1_790_000_000.123  # fixed instant: `created` is set by `logging`, not by our Clock
    return record


def test_a_line_carries_timestamp_level_logger_and_message() -> None:
    line = json.loads(tel.JsonLogFormatter().format(_record()))
    assert line["timestamp"] == "2026-09-21T14:13:20.123Z"
    assert (line["level"], line["logger"], line["message"]) == ("ERROR", "agentcore.api", "error no controlado")


def test_an_exception_contributes_its_type_never_its_message_nor_stack() -> None:
    try:
        raise RuntimeError("SECRETO-123")
    except RuntimeError as exc:
        text = tel.JsonLogFormatter().format(_record(exc))
    assert json.loads(text)["exc_type"] == "RuntimeError"
    assert "SECRETO-123" not in text and "Traceback" not in text


def test_extra_fields_are_not_emitted() -> None:
    record = _record()
    record.customer_text = "SECRETO"  # e.g. logger.info(..., extra={...})
    assert "SECRETO" not in tel.JsonLogFormatter().format(record)


def test_bound_context_and_trace_id_are_present(otel: object) -> None:
    with tel.bind(run_id="run-0001", release="rel-1", agent="atencion@1.0.0"), tel.span(tel.CHAT):
        line = json.loads(tel.JsonLogFormatter().format(_record()))
    assert line["run_id"] == "run-0001" and line["agentcore.agent"] == "atencion@1.0.0"
    assert len(line["trace_id"]) == 32
```

Antes de dar por buena la cadena esperada de `timestamp` ("2026-09-21T14:13:20.123Z"), comprobar el valor con `datetime.fromtimestamp(1_790_000_000.123, UTC)`. Si no coincide, se corrige **la constante de la prueba**, no el formato.

`tests/composition/test_observability.py`:

```python
"""`setup_observability` (U2, I3, I5, I6, M5): standard OTEL_* variables, JSON logs on root, flush on exit."""

import argparse
import io
import json
import logging
from typing import Any

import pytest
from opentelemetry.sdk.trace import sampling
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.composition import serve as serve_module
from agent_core.composition.observability import (
    ObservabilityConfigError,
    setup_observability,
    tracing_config,
)
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


def test_no_endpoint_means_no_tracing() -> None:
    assert tracing_config({}, version="1") is None
    assert tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_SDK_DISABLED": "true"},
                          version="1") is None
    assert tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_TRACES_EXPORTER": "none"},
                          version="1") is None


def test_standard_variables_build_endpoint_resource_headers_and_sampler() -> None:
    config = tracing_config({
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://phoenix:6006/",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer%20k",
        "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=demo,service.name=ignorado",
        "OTEL_SERVICE_NAME": "agentcore-demo",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio", "OTEL_TRACES_SAMPLER_ARG": "0.25",
    }, version="0.1.0")
    assert config is not None
    assert config.endpoint == "http://phoenix:6006/v1/traces"
    assert config.headers == {"authorization": "Bearer k"}
    assert config.resource == {"service.name": "agentcore-demo", "service.version": "0.1.0",
                               "deployment.environment.name": "demo"}
    assert isinstance(config.sampler, sampling.ParentBased)


def test_the_traces_specific_endpoint_wins_and_is_used_verbatim() -> None:
    config = tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:1",
                             "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://b:2/custom"}, version="1")
    assert config is not None and config.endpoint == "http://b:2/custom"


@pytest.mark.parametrize("env", [
    {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"},
    {"OTEL_TRACES_SAMPLER": "nope"},
    {"OTEL_TRACES_SAMPLER": "traceidratio", "OTEL_TRACES_SAMPLER_ARG": "dos"},
    {"OTEL_TRACES_EXPORTER": "zipkin"},
])
def test_invalid_values_are_startup_problems_and_never_echo_headers(env: dict[str, str]) -> None:
    with pytest.raises(ObservabilityConfigError) as info:
        tracing_config({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1",
                        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRETO", **env}, version="1")
    assert info.value.problems and all("SECRETO" not in p for p in info.value.problems)


def test_setup_installs_json_logs_on_root_and_quiets_the_sdk_loggers(root_logging: None) -> None:
    stream = io.StringIO()
    observability = setup_observability({}, version="1", stream=stream)
    try:
        logging.getLogger("agentcore.api").warning("hola")
        assert json.loads(stream.getvalue().splitlines()[-1])["message"] == "hola"
        assert logging.getLogger("openai").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        observability.shutdown()
    assert all(getattr(h, "agentcore", False) is False for h in logging.getLogger().handlers)


def test_shutdown_flushes_and_closes_the_exporter(root_logging: None) -> None:
    exporter = InMemorySpanExporter()
    observability = setup_observability({}, version="1", exporter=exporter, stream=io.StringIO())
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass
    observability.shutdown()
    assert len(exporter.get_finished_spans()) == 1 and exporter._stopped  # type: ignore[attr-defined]


def test_run_serve_wires_observability_and_shuts_it_down(
        monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    seen: dict[str, Any] = {}

    def resolve(args: Any, env: Any, clock: Any, ids: Any, *, tracer: Any = None) -> Any:
        seen["tracer"] = tracer
        return make_ports(world, issuer)

    monkeypatch.setattr(serve_module, "resolve_ports", resolve)
    shut: list[bool] = []
    real_setup = serve_module.setup_observability

    def setup(env: Any) -> Any:
        observability = real_setup(env, version="1", stream=io.StringIO())
        monkeypatch.setattr(observability, "shutdown", lambda: shut.append(True))
        return observability

    monkeypatch.setattr(serve_module, "setup_observability", setup)
    started: dict[str, Any] = {}
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: started.update(kw))
    assert code == 0 and shut == [True] and seen["tracer"] is not None
    assert started["log_config"] is None and started["access_log"] is False


def test_run_serve_shuts_down_even_if_uvicorn_raises(monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    ...  # same wiring; `serve` raises RuntimeError -> the shutdown still ran and the error propagates


def test_invalid_otel_configuration_exits_2(capsys: pytest.CaptureFixture[str], root_logging: None) -> None:
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=EngineWorld().clock,
                                  ids=EngineWorld().ids, serve=lambda app, **kw: None,
                                  env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:1", "OTEL_TRACES_SAMPLER": "nope"})
    assert code == 2 and "OTEL_TRACES_SAMPLER" in capsys.readouterr().err
```

`test_run_serve_shuts_down_even_if_uvicorn_raises` se escribe completo, con el mismo cableado de `test_run_serve_wires_observability_and_shuts_it_down`.

En `tests/composition/test_serve_ports.py`:

```python
def test_resolve_ports_injects_the_given_tracer_in_the_gateway() -> None:
    marker = object()
    ports = _resolve(AGENTCORE_ALLOW_DEMO="1", tracer=marker)  # extend `_resolve` with **kwargs for resolve_ports
    assert ports.gateway._tracer is marker  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m11/test_telemetry.py tests/m11/test_json_logs.py tests/composition/test_observability.py tests/composition/test_serve_ports.py -q`
Expected: FAIL (`ImportError`: `shutdown_tracing`, `observability`, `tracer`, `record_span`, …).

- [ ] **Step 3: Implement `agent_telemetry`**

`agent_telemetry/semconv.py`:

```python
"""Pinned GenAI semantic conventions (ADR 0003 #1; M11 decision 15)."""

SEMCONV_VERSION = "1.44.0"  # verified against opentelemetry-semantic-conventions 0.66b0 (uv.lock)
SCHEMA_URL = f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}"
```

`agent_telemetry/setup.py`:

```python
"""Exporter configuration. The provider lives here, never as OpenTelemetry's global (F3): every agentcore
tracer comes from `tracer()`, so reconfiguring or shutting down never leaves a span on a closed provider."""

from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, Sampler

from agent_telemetry.semconv import SCHEMA_URL, SEMCONV_VERSION

_NOOP = trace.NoOpTracerProvider()
_PROVIDER: TracerProvider | None = None


def get_provider() -> trace.TracerProvider:
    return _PROVIDER if _PROVIDER is not None else _NOOP


def tracer(name: str) -> trace.Tracer:
    """A tracer of the current provider that declares the pinned semconv schema."""
    return get_provider().get_tracer(name, SEMCONV_VERSION, schema_url=SCHEMA_URL)


def setup_tracing(endpoint: str | None = None, exporter: SpanExporter | None = None, *,
                  resource: Resource | None = None, sampler: Sampler | None = None,
                  headers: Mapping[str, str] | None = None) -> TracerProvider:
    """`endpoint` (OTLP/HTTP, batched) or an explicit `exporter` (tests, synchronous). The sampler is explicit so
    the SDK never reads OTEL_TRACES_SAMPLER behind the caller's back. Reconfiguring shuts the previous one down."""
    global _PROVIDER
    if exporter is None:
        if endpoint is None:
            raise ValueError("indica endpoint OTLP o un exporter")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor: SimpleSpanProcessor | BatchSpanProcessor = BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers=dict(headers or {})))
    else:
        processor = SimpleSpanProcessor(exporter)
    provider = TracerProvider(resource=resource or Resource({"service.name": "agentcore"}),
                              sampler=sampler or ParentBased(ALWAYS_ON), shutdown_on_exit=False)
    provider.add_span_processor(processor)
    if _PROVIDER is not None:
        _PROVIDER.shutdown()
    _PROVIDER = provider
    return provider


def shutdown_tracing() -> None:
    """Flush and close the current provider (end of `serve`, tests); afterwards every span is a no-op."""
    global _PROVIDER
    if _PROVIDER is not None:
        _PROVIDER.force_flush()
        _PROVIDER.shutdown()
        _PROVIDER = None
```

`agent_telemetry/context.py`: `bind` agrega `agent: str | None = None` (clave `"agentcore.agent"`) y se agrega `def correlation() -> Mapping[str, str]` (alias público de `current()`).

`agent_telemetry/spans.py` (lo central):

```python
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, Status, StatusCode
from opentelemetry.util.types import AttributeValue

from agent_telemetry.context import current
from agent_telemetry.semconv import SEMCONV_VERSION
from agent_telemetry.setup import tracer as _tracer

INVOKE_AGENT, DECIDE, RULE, EXECUTE_TOOL, CHAT = ("invoke_agent", "agentcore.decide", "agentcore.rule",
                                                  "execute_tool", "chat")
_SCOPE = "agent_telemetry"
_REQUIRED = ("run_id", "agentcore.release")
_LOG = logging.getLogger("agent_telemetry")

# Closed list (rule 6): ids, entity refs, enums, counters and booleans. Never a payload value or text.
ALLOWED_ATTRIBUTES: frozenset[str] = frozenset({
    "run_id", "turn_id", "session_id", "agentcore.release", "agentcore.agent",
    "agentcore.entry", "agentcore.principal_type", "agentcore.locale", "agentcore.flow", "agentcore.node",
    "agentcore.problem_code", "error.type",
    "gen_ai.operation.name", "gen_ai.agent.name", "gen_ai.tool.name", "gen_ai.tool.call.id",
    "agentcore.decision.id", "agentcore.decision.model", "agentcore.decision.provider",
    "agentcore.decision.fallback_depth", "agentcore.decision.tokens",
    "agentcore.rule.policy", "agentcore.rule.result",
    "agentcore.tool", "agentcore.tool.status", "agentcore.tool.attempt",
})
_capture_content = False
_strict = False
_warned: set[str] = set()


def configure(*, capture_content: bool | None = None, strict: bool | None = None) -> None:
    global _capture_content, _strict
    if capture_content is not None:
        _capture_content = capture_content
    if strict is not None:
        _strict = strict


def _allowed(name: str, attrs: Mapping[str, Any]) -> dict[str, AttributeValue]:
    unknown = sorted(k for k in attrs if k not in ALLOWED_ATTRIBUTES)
    if unknown and _strict:
        raise ValueError(f"span {name!r}: atributos fuera de la lista cerrada: {', '.join(unknown)}")
    return {k: v for k, v in attrs.items() if k in ALLOWED_ATTRIBUTES and v is not None}


def mark_error(active: Span, exc: BaseException) -> None:
    """Only the type: an exception's message may carry secrets or PII (rule 6)."""
    active.set_attribute("error.type", type(exc).__name__)
    active.set_status(Status(StatusCode.ERROR))


@contextmanager
def span(name: str, *, attributes: Mapping[str, Any] | None = None, links: Sequence[Link] = (),
         context: Context | None = None, **attrs: Any) -> Iterator[Span]:
    # bound context wins over kwargs: a span cannot change its run_id or release
    merged = _allowed(name, {**{_key(k): v for k, v in attrs.items()}, **(attributes or {}), **current()})
    missing = [k for k in _REQUIRED if k not in merged]
    if missing:
        if _strict:
            raise MissingTelemetryContext(f"span {name!r} sin {', '.join(missing)}; usa bind(...)")
        if name not in _warned:  # I4: telemetry never fails a turn
            _warned.add(name)
            _LOG.warning("span %s sin %s: se omite", name, ", ".join(missing))
        yield trace.INVALID_SPAN
        return
    with _tracer(_SCOPE).start_as_current_span(
            name, context=context, links=links, attributes=merged,
            record_exception=False, set_status_on_exception=False) as active:
        try:
            yield active
        except BaseException as exc:
            mark_error(active, exc)
            raise


def record_span(name: str, *, parent: Span, start_ns: int, end_ns: int, attributes: Mapping[str, Any]) -> None:
    """A finished child with explicit times (derived from audit events, M11 §3.2). No-op on a non-recording parent."""
    if not parent.is_recording():
        return
    merged = _allowed(name, {**attributes, **current()})
    child = _tracer(_SCOPE).start_span(name, context=trace.set_span_in_context(parent), attributes=merged,
                                        start_time=start_ns)
    child.end(end_time=max(end_ns, start_ns))


def set_attributes(active: Span, attributes: Mapping[str, Any]) -> None:
    """`Span.set_attributes` through the closed list."""
    active.set_attributes(_allowed("set_attributes", attributes))
```

`set_content` sigue igual: escribe `agentcore.content.*` directo en el span, solo si se activó. `current_trace_id` sigue igual hasta T3.

`agent_telemetry/logging.py`:

```python
import json
import logging
from datetime import UTC, datetime

from agent_telemetry.context import current
from agent_telemetry.spans import current_trace_id


class JsonLogFormatter(logging.Formatter):
    """Closed set of fields (F6): never `exc_text`, the stack or `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
        body: dict[str, object] = {"timestamp": stamp.replace("+00:00", "Z"), "level": record.levelname,
                                   "logger": record.name, "message": record.getMessage(), **current()}
        if record.exc_info and record.exc_info[0] is not None:
            body["exc_type"] = record.exc_info[0].__name__
        trace_id = current_trace_id()
        if trace_id is not None:
            body["trace_id"] = trace_id
        return json.dumps(body, ensure_ascii=False, default=str)
```

`agent_telemetry/__init__.py` exporta además: `ALLOWED_ATTRIBUTES`, `SCHEMA_URL`, `correlation`, `mark_error`, `record_span`, `set_attributes`, `setup_tracing`, `shutdown_tracing` y `tracer`.

`agent_core/api/tracing.py`: `from agent_telemetry import tracer` y `tracer("agentcore.api")` en lugar de `get_provider().get_tracer(...)`. `tests/m09/test_fastapi_telemetry.py`: el fixture local `exporter` se reemplaza por `otel` (`pytest_plugins = ["tests.support.otel"]`).

- [ ] **Step 4: Implement `composition/observability.py` and the serve wiring**

```python
"""Operational plane of `agentcore serve` (ADR 0003 #1/#4): traces from the standard OTEL_* variables, JSON logs
on root and flush on exit. Reads only the injected `env` (F4)."""

import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import TextIO
from urllib.parse import unquote

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import sampling
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Tracer
from opentelemetry.util.re import parse_env_headers

import agent_telemetry as tel

_TRACES_PATH = "/v1/traces"
_SDK_LOGGERS = ("openai", "httpx", "httpcore")  # the openai SDK logs request bodies (model view) at DEBUG


class ObservabilityConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class TracingConfig:
    endpoint: str
    headers: Mapping[str, str]  # may hold credentials: never printed or logged
    resource: Mapping[str, str]
    sampler: sampling.Sampler


def tracing_config(env: Mapping[str, str], *, version: str) -> TracingConfig | None:
    """OpenTelemetry SDK environment variables (subset, F4). `None` = tracing off."""
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    problems: list[str] = []
    exporter = (env.get("OTEL_TRACES_EXPORTER") or "otlp").strip().lower()
    if exporter == "none":
        return None
    if exporter != "otlp":
        problems.append("OTEL_TRACES_EXPORTER solo admite `otlp` o `none`")
    endpoint = env.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if not endpoint and (base := env.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()):
        endpoint = base.removesuffix("/") + _TRACES_PATH
    if not endpoint:
        if problems:
            raise ObservabilityConfigError(problems)
        return None
    protocol = (env.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or env.get("OTEL_EXPORTER_OTLP_PROTOCOL")
                or "http/protobuf").strip()
    if protocol != "http/protobuf":
        problems.append("OTEL_EXPORTER_OTLP_PROTOCOL: solo `http/protobuf` (no hay exportador gRPC)")
    sampler = _sampler(env.get("OTEL_TRACES_SAMPLER"), env.get("OTEL_TRACES_SAMPLER_ARG"), problems)
    if problems:
        raise ObservabilityConfigError(problems)
    raw_headers = env.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or env.get("OTEL_EXPORTER_OTLP_HEADERS") or ""
    resource = {"service.name": "agentcore", "service.version": version,
                **_resource_attributes(env.get("OTEL_RESOURCE_ATTRIBUTES", ""))}
    if service := env.get("OTEL_SERVICE_NAME", "").strip():
        resource["service.name"] = service  # wins over OTEL_RESOURCE_ATTRIBUTES, as in the spec
    return TracingConfig(endpoint=endpoint, headers=dict(parse_env_headers(raw_headers, liberal=True)),
                         resource=resource, sampler=sampler)
```

Más estas funciones:
- `_sampler(name, arg, problems)`: los seis valores estándar. `traceidratio` usa `TraceIdRatioBased(ratio)`, con `ratio` en `[0, 1]` (por defecto `1.0`); `parentbased_*` envuelve en `ParentBased`. Un valor desconocido o un `ARG` inválido agregan un problema que nombra la variable, nunca su valor si es de headers.
- `_resource_attributes(raw)`: `k=v` separados por coma, con `unquote`; ignora entradas vacías.
- `_version()`: `package_version("agent-core")` o `"0+unknown"` (`PackageNotFoundError`).

```python
@dataclass
class Observability:
    tracing: bool
    _handler: logging.Handler = field(repr=False)

    def tracer(self, name: str) -> Tracer:
        return tel.tracer(name)

    def shutdown(self) -> None:
        """Flush pending spans (BatchSpanProcessor) and remove the root handler. Idempotent."""
        tel.shutdown_tracing()
        root = logging.getLogger()
        if self._handler in root.handlers:
            root.removeHandler(self._handler)


def quiet_sdk_loggers() -> None:
    for name in _SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def install_json_logging(stream: TextIO, level: int = logging.INFO) -> logging.Handler:
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, "agentcore", False)]:
        root.removeHandler(old)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(tel.JsonLogFormatter())
    handler.agentcore = True  # type: ignore[attr-defined]  # marks the handler this module owns
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def setup_observability(env: Mapping[str, str], *, version: str | None = None,
                        exporter: SpanExporter | None = None, stream: TextIO | None = None) -> Observability:
    config = tracing_config(env, version=version or _version())
    if config is not None or exporter is not None:
        tel.setup_tracing(
            endpoint=config.endpoint if config is not None else None, exporter=exporter,
            headers=config.headers if config is not None else None,
            resource=Resource(dict(config.resource)) if config is not None else None,
            sampler=config.sampler if config is not None else None)
    handler = install_json_logging(stream or sys.stderr)
    quiet_sdk_loggers()
    return Observability(tracing=config is not None or exporter is not None, _handler=handler)
```

`agent_core/composition/serve.py`, `run_serve`:

```python
def run_serve(args: argparse.Namespace, *, clock: Clock, ids: IdSource, env: Mapping[str, str],
              serve: Callable[..., None] | None = None) -> int:
    try:
        observability = setup_observability(env)
    except ObservabilityConfigError as exc:
        print("agentcore serve no puede arrancar:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    try:
        try:
            ports = resolve_ports(args, env, clock, ids, tracer=observability.tracer(GATEWAY_TRACER))
        except ServeConfigError as exc:
            ...  # unchanged: print the problems, return 2
        ...  # unchanged: doubles, alias warnings, registry service
        app = create_app(build_api_deps(ports, registry_service=registry_service))
        if serve is None:
            import uvicorn

            serve = uvicorn.run
        # F5: uvicorn's own logging config would print "Exception in ASGI application" with the exception's
        # message and stack (Starlette re-raises after the 500 handler); with `log_config=None` it reaches the
        # root JSON formatter, which keeps only `exc_type`. The access log would print client IPs and id paths.
        serve(app, host=args.host, port=args.port, log_config=None, access_log=False)
        return 0
    finally:
        observability.shutdown()  # I6: flush the batch exporter on exit
```

`GATEWAY_TRACER = "agent_core.adapters.llm"` (constante de módulo). La prueba existente `test_run_serve_prints_the_doubles_and_starts_uvicorn` sigue en verde; su `fake_uvicorn` ya acepta `**kwargs`. Agregarle la fixture `root_logging`, porque ahora `run_serve` toca root.

`serve_ports.resolve_ports(args, env, clock, ids, *, tracer: Tracer | None = None)` pasa `tracer=tracer` a `OpenAICompatGateway`.

`gateway.py`:

```python
        self._tracer = tracer  # None: the current agent_telemetry provider, resolved per call (F3/I5)

    def _active_tracer(self) -> Tracer:
        return self._tracer if self._tracer is not None else tel.tracer("agent_core.adapters.llm")
```

`generate` usa `self._active_tracer().start_as_current_span(...)` y se borra `from opentelemetry import trace` si queda sin uso.

`cli.py`, `_run_llm_smoke`: el bucle `for name in ("openai", "httpx")` pasa a `quiet_sdk_loggers()`, importado de `agent_core.composition.observability` (cli puede importar composition).

`agent_core/composition/__init__.py` exporta `Observability`, `ObservabilityConfigError`, `setup_observability` y `tracing_config`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/m11 tests/m09 tests/composition tests/u05 -q`, después `uv run pytest -q` completo (la fixture `otel` y el cambio de F3 tocan pruebas de otros módulos), `uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`.
Expected: PASS. `tests/u05/test_observability.py` sigue en verde porque inyecta su propio `tracer`.

- [ ] **Step 6: Spec and commit**

`m11-auditoria-transcript-replay.md`:
- §2: nueva firma de `span` y `record_span`, `tracer`, `shutdown_tracing`, `ALLOWED_ATTRIBUTES` y `configure(strict=…)`.
- Decisión 15: "el provider es propio de `agent_telemetry` y no se instala como global (F3)".
- Agregar la prueba T-M11-12 "configuración por `OTEL_*`, logs JSON y vaciado al salir" (`test_observability`, `test_json_logs`).

```bash
git add agent_telemetry agent_core/api/tracing.py agent_core/composition agent_core/adapters/llm/gateway.py agent_core/cli.py tests/support/otel.py tests/m11 tests/m09/test_fastapi_telemetry.py tests/composition docs/specs/motor/m11-auditoria-transcript-replay.md
git commit -F msg.txt   # feat(observability): setup_observability for serve over the standard OTEL_* variables
```

---

### Task 3: puerto `TurnTelemetry`, `invoke_agent` por turno y `trace_id` real

**Modelo recomendado:** opus.

**Files:**
- Create: `agent_core/turn/telemetry.py`, `agent_core/composition/telemetry.py`, `testing/fakes/telemetry.py`, `tests/m04/test_telemetry_port.py`, `tests/composition/test_turn_telemetry.py`
- Modify:
  - `agent_core/turn/{ports,frame,buffer,engine,__init__}.py`;
  - `agent_core/composition/{engine,serve,__init__}.py`;
  - `agent_core/api/tracing.py`: `bind_trace_id`;
  - `agent_telemetry/{context,spans,__init__}.py`: respaldo del trace id;
  - `.importlinter` (F11);
  - `tests/m04/harness.py`: `World(telemetry=…)`;
  - `testing/engine_world.py`: `EngineWorld(telemetry=…)`.
- Docs: `m04-ciclo-del-turno.md` (§2, §3.9 nuevo, §7 T-M4-22, §16); `m09-acceso-y-api.md` §3.7 (trace id del turno).

**Interfaces:**
- Produce (M4, `agent_core.turn`):

```python
@dataclass(frozen=True)
class TurnScope:
    run_id: str
    turn_id: str
    session_id: str | None
    release: str
    agent: EntityRef
    entry: Literal["start_run", "turn"]
    principal_type: str
    locale: str


@dataclass(frozen=True)
class TransferOutcome:
    outcome: Literal["transferred", "rejected"]
    to_agent: str | None = None
    to_release_id: str | None = None   # only with `transferred`
    reason_code: str | None = None     # only with `rejected`


class TransferSpan(Protocol):
    link: object | None  # opaque handle for the target turn's `links`; None = nothing to link

    def finish(self, outcome: TransferOutcome) -> None: ...


class TurnSpan(Protocol):
    def record(self, events: Sequence[EngineEvent]) -> None:
        """Events this turn just chained (audit view), in chain order; called after every append."""
        ...

    def transfer(self, transfer_id: str) -> AbstractContextManager[TransferSpan]: ...


class TurnTelemetry(Protocol):
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> AbstractContextManager[TurnSpan]: ...
```

- `agent_core.turn.NoTurnTelemetry` y `agent_core.turn.NO_SPAN`.
- `TurnEngine(..., telemetry: TurnTelemetry | None = None)`.
- Produce (composition):
  - `OtelTurnTelemetry()`;
  - `RequestTraceIds` (reemplaza a `DerivedTrace`; ver el paso 4);
  - `EngineDeps.telemetry: TurnTelemetry | None = None`;
  - `build_api_deps(ports, *, registry_service=None, telemetry=None)`.
- Produce (`agent_telemetry`): `bind_trace_id(trace_id)` (context manager); `current_trace_id()` devuelve el id OTel válido o el enlazado.

- [ ] **Step 1: Write the failing tests**

`testing/fakes/telemetry.py`:

```python
"""`TurnTelemetry` double: records what M4 reports, in order (m04 §3.9)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from agent_core.domain import EngineEvent
from agent_core.turn import TransferOutcome, TransferSpan, TurnScope, TurnSpan


@dataclass
class RecordedTransfer:
    transfer_id: str
    outcome: TransferOutcome | None = None
    link: object = field(default_factory=object)


@dataclass
class RecordedTurn:
    scope: TurnScope
    links: tuple[object, ...]
    events: list[EngineEvent] = field(default_factory=list)
    transfers: list[RecordedTransfer] = field(default_factory=list)
    error: str | None = None
    closed: bool = False


class _TransferSpan:
    def __init__(self, recorded: RecordedTransfer) -> None:
        self._recorded = recorded
        self.link: object | None = recorded.link

    def finish(self, outcome: TransferOutcome) -> None:
        self._recorded.outcome = outcome


class _TurnSpan:
    def __init__(self, turn: RecordedTurn) -> None:
        self._turn = turn

    def record(self, events: Sequence[EngineEvent]) -> None:
        self._turn.events.extend(events)

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        recorded = RecordedTransfer(transfer_id)
        self._turn.transfers.append(recorded)
        yield _TransferSpan(recorded)


class RecordingTelemetry:
    def __init__(self) -> None:
        self.turns: list[RecordedTurn] = []

    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        recorded = RecordedTurn(scope, tuple(links))
        self.turns.append(recorded)
        try:
            yield _TurnSpan(recorded)
        except BaseException as exc:
            recorded.error = type(exc).__name__
            raise
        finally:
            recorded.closed = True
```

`tests/m04/harness.py`: `World.__init__(..., telemetry: Any = None)` guarda `self.telemetry = telemetry`, y `engine_kwargs()` agrega `"telemetry": telemetry`.

`tests/m04/test_telemetry_port.py` (T-M4-22):

```python
"""M4 reports each turn to `TurnTelemetry` (m04 §3.9, T-M4-22). The default is a no-op."""

import pytest

from agent_core.audit import AuditLog
from agent_core.domain import EngineError
from agent_core.turn import NoTurnTelemetry, TurnEngine
from testing.fakes.telemetry import RecordingTelemetry
from tests.m04.harness import RUN_ID, SESSION_ID, World
from tests.m04.helpers import cmd


def _world() -> tuple[World, RecordingTelemetry]:
    telemetry = RecordingTelemetry()
    return World(chain_factory=AuditLog, telemetry=telemetry), telemetry


def test_the_default_telemetry_is_a_no_op() -> None:
    w = World()
    assert isinstance(w.engine._telemetry, NoTurnTelemetry)


def test_handle_turn_opens_one_scope_with_the_correlation_ids() -> None:
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    result = w.turn("hola")
    (turn,) = telemetry.turns
    assert turn.closed and turn.error is None and turn.links == ()
    scope = turn.scope
    assert (scope.run_id, scope.turn_id, scope.session_id) == (RUN_ID, result.turn_id, SESSION_ID)
    assert scope.entry == "turn" and scope.release == w.saved().release and scope.agent == w.saved().agent
    assert scope.principal_type == w.saved().principal.type.value and scope.locale == w.saved().locale


def test_record_receives_exactly_the_chained_events_in_order() -> None:
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("hola")
    (turn,) = telemetry.turns
    assert [e.event_id for e in turn.events] == [e.event_id for e in w.events()]


def test_events_flushed_by_m3_are_recorded_too() -> None:
    """`tool_called` goes through `TurnEventSink`, not `_finish`."""
    w, telemetry = _world()
    w.at_confirm()  # a `confirm` followed by the write: M3 flushes through the sink
    result = w.turn_confirm(w.saved_token(), "yes")  # use the harness helpers that existing write tests use
    recorded = [e.event_id for t in telemetry.turns for e in t.events]
    assert recorded == [e.event_id for e in w.events()] and "tool_called" in w.event_types()
    assert result.turn_id == telemetry.turns[-1].scope.turn_id


def test_start_run_opens_a_start_run_scope() -> None:
    w, telemetry = _world()
    run = w.engine.start_run(w.principal, None, w.run_input())  # same input the start_run tests build
    (turn,) = telemetry.turns
    assert turn.scope.entry == "start_run" and turn.scope.run_id == run.run_id


@pytest.mark.parametrize("case", ["duplicate", "in_progress", "closed"])
def test_no_scope_for_turns_that_emit_no_turn_completed(case: str) -> None:  # m04 §3.7
    ...  # build each case as T-M4-17 does (`tests/m04/test_turn_completed.py`); assert telemetry.turns == []


def test_an_exception_closes_the_scope_and_propagates() -> None:
    w, telemetry = _world()
    w.open_run(active=True)
    w.understand.fail_with(RuntimeError("boom"))  # or the failing understand double the harness already has
    with pytest.raises(RuntimeError):
        w.turn("hola")
    (turn,) = telemetry.turns
    assert turn.closed and turn.error == "RuntimeError"


def test_telemetry_does_not_change_events_or_ids() -> None:
    plain, (traced, _) = World(chain_factory=AuditLog), _world()
    for w in (plain, traced):
        w.open_run(active=True)
        w.understand.push(cmd("continue"))
        w.turn("hola")
    assert [e.model_dump() for e in plain.events()] == [e.model_dump() for e in traced.events()]
```

Las pruebas con `...` son obligatorias: se escriben completas copiando la preparación de la prueba citada. `w.at_confirm`, `w.turn_confirm` y `w.run_input` se reemplazan por los helpers reales del harness (`seed_at_confirm`, el `RunInput` de `tests/m04/test_start_run.py`, y `understand.push` de una excepción si existe). **No se agregan helpers nuevos al harness salvo `telemetry`.**

`tests/composition/test_turn_telemetry.py` (T-M4-22, mitad OTel, y U3):

```python
"""`OtelTurnTelemetry`: one live `invoke_agent` per turn, and the turn's trace id is the request's (U3)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.api.app import create_app
from agent_core.composition import OtelTurnTelemetry, RequestTraceIds
from agent_core.composition.serve import build_api_deps
from agent_core.ports import IdKind
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


class RecordingIds:
    """Wraps the world's ids and keeps every id handed out, in order."""

    def __init__(self, inner: object) -> None:
        self._inner, self.handed = inner, []

    def new_id(self, kind: IdKind) -> str:
        value = self._inner.new_id(kind)  # type: ignore[attr-defined]
        self.handed.append((kind, value))
        return value

    def secret_token(self) -> str:
        return self._inner.secret_token()  # type: ignore[attr-defined]


def _client(world: EngineWorld, *, ids: object | None = None) -> tuple[TestClient, TestIdentityIssuer]:
    issuer = TestIdentityIssuer(world.clock)
    ports = make_ports(world, issuer)
    if ids is not None:
        ports = replace(ports, ids=ids)
    app = create_app(build_api_deps(ports, telemetry=OtelTurnTelemetry()))
    return TestClient(app, raise_server_exceptions=False), issuer


def _create(client: TestClient, issuer: TestIdentityIssuer) -> dict[str, object]:
    resp = client.post("/v1/runs", json={"agent": "atencion"},
                       headers={"Authorization": f"Bearer {issuer.customer()}", "Idempotency-Key": "k-1"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_each_turn_opens_one_invoke_agent_with_closed_attributes(otel: InMemorySpanExporter) -> None:
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    world.start()
    world.understands("continue")
    world.matches()
    world.turn("no reconozco un cargo")
    spans = [s for s in otel.get_finished_spans() if s.name == tel.INVOKE_AGENT]
    assert [dict(s.attributes or {})["agentcore.entry"] for s in spans] == ["start_run", "turn"]
    for s in spans:
        attrs = dict(s.attributes or {})
        assert set(attrs) <= tel.ALLOWED_ATTRIBUTES
        assert attrs["agentcore.agent"] == "atencion@1.0.0" and attrs["gen_ai.agent.name"] == "atencion"
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert {"run_id", "turn_id", "session_id", "agentcore.release"} <= set(attrs)


def test_the_turn_trace_id_is_the_request_trace_with_otel(otel: InMemorySpanExporter) -> None:  # U3, threadpool
    client, issuer = _client(EngineWorld())
    body = _create(client, issuer)
    (request,) = [s for s in otel.get_finished_spans() if s.name == "agentcore.api.request"]
    (invoke,) = [s for s in otel.get_finished_spans() if s.name == tel.INVOKE_AGENT]
    assert request.context is not None and invoke.context is not None
    trace_hex = format(request.context.trace_id, "032x")
    assert body["trace_id"] == trace_hex and body["first_turn"]["trace_id"] == trace_hex  # type: ignore[index]
    assert invoke.context.trace_id == request.context.trace_id
    assert invoke.parent is not None and invoke.parent.span_id == request.context.span_id


def test_the_turn_trace_id_is_the_request_fallback_without_otel() -> None:  # U3
    world = EngineWorld()
    ids = RecordingIds(world.ids)
    client, issuer = _client(world, ids=ids)
    body = _create(client, issuer)
    fallback = ids.handed[0]  # the middleware asks first, before any engine id
    assert fallback[0] is IdKind.event
    assert body["trace_id"] == body["first_turn"]["trace_id"] == fallback[1]  # type: ignore[index]


def test_a_problem_after_the_engine_carries_the_same_trace_id(otel: InMemorySpanExporter) -> None:
    ...  # a turn on a closed run (410) and a turn that succeeds: each response's trace_id is its request's span


def test_a_deduplicated_retry_returns_the_original_trace_id(otel: InMemorySpanExporter) -> None:  # F16
    ...  # two POST /turns with the same client_turn_id: same trace_id although they are two requests


def test_outside_a_request_the_trace_id_is_derived_from_the_turn() -> None:  # F8 (3)
    assert RequestTraceIds().current("turn-0001") == "trace-turn-0001"
    with tel.bind_trace_id("abc"):
        assert RequestTraceIds().current("turn-0001") == "abc"
```

Las dos pruebas con `...` se escriben completas, con el mismo cliente.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m04/test_telemetry_port.py tests/composition/test_turn_telemetry.py -q`
Expected: FAIL (`ImportError: NoTurnTelemetry`, `OtelTurnTelemetry`, `bind_trace_id`; `TypeError` por `telemetry=`).

- [ ] **Step 3: Implement M4**

`agent_core/turn/telemetry.py`:

```python
"""Default `TurnTelemetry`: does nothing (tests, replay, `record`, registry evaluation). The real one lives in
composition over `agent_telemetry`; M4 never imports OpenTelemetry (F11)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from agent_core.domain import EngineEvent
from agent_core.turn.ports import TransferOutcome, TransferSpan, TurnScope, TurnSpan


class _NoTransferSpan:
    link: object | None = None

    def finish(self, outcome: TransferOutcome) -> None:
        return None


class _NoTurnSpan:
    def record(self, events: Sequence[EngineEvent]) -> None:
        return None

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        yield _NoTransferSpan()


NO_SPAN: TurnSpan = _NoTurnSpan()


class NoTurnTelemetry:
    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        yield NO_SPAN
```

`agent_core/turn/frame.py`:
- `span: "TurnSpan" = NO_SPAN`, que se importa de `agent_core.turn.telemetry`;
- `transfer_link: object | None = None`, que se usa en T5 y se declara ya para no tocar dos veces el dataclass.

`agent_core/turn/buffer.py`:

```python
    def __init__(self, buffer: EventBuffer, chain: EventChain, ensure_turn_started: Callable[[], None],
                 observe: Callable[[list[EngineEvent]], None] | None = None) -> None:
        ...
        self._observe = observe

    def record(self, uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
        if self._buffer.turn_started_reserved and not self._buffer.turn_started_filled:
            self._ensure()
        appended = [*self._buffer.drain(), *events]
        self._chain.append(uow, state.run_id, appended)
        if self._observe is not None:
            self._observe(appended)
```

`agent_core/turn/engine.py`:

```python
    def __init__(self, *, ..., authz: AuthzPort | Any | None = None,
                 telemetry: TurnTelemetry | None = None) -> None:
        ...
        self._telemetry = telemetry or NoTurnTelemetry()

    @staticmethod
    def _scope(state: RunState, turn_id: str, entry: Literal["start_run", "turn"]) -> TurnScope:
        return TurnScope(run_id=state.run_id, turn_id=turn_id, session_id=state.session_id,
                         release=state.release, agent=state.agent, entry=entry,
                         principal_type=state.principal.type.value, locale=state.locale)
```

En `_new_frame`:
- se agrega el parámetro `span: TurnSpan`, que se pasa a `TurnFrame(..., span=span)`;
- `TurnEventSink(..., observe=lambda events: frame.span.record(events))`.

En `_finish`:

```python
        saved = frame.uow.save_run(state, expected_version=state.state_version)
        chained = [*frame.buffer.drain(), completed]
        self._chain.append(frame.uow, saved.run_id, chained)
        frame.span.record(chained)  # telemetry reads, never writes, the turn's events (U4)
```

En `handle_turn`, entre la relectura del run y el `commit`:

```python
                state = uow.load_run(found.run_id) or found  # estado fresco tras tomar el lease
                if state.status != "open":
                    raise EngineError(ProblemCode.run_closed, "run cerrado")
                with self._telemetry.turn(self._scope(state, turn_id, "turn")) as span:
                    outcome = self._process(uow, state, principal, on_behalf_of, turn, turn_id, meter, span=span)
                    uow.commit()
                leased = None
```

`_process(..., *, prelude=(), store_result=True, span: TurnSpan = NO_SPAN)` pasa `span` a `_new_frame`.

En `start_run`:

```python
        with self._uow_factory() as uow, self._telemetry.turn(self._scope(state, turn_id, "start_run")) as span:
            frame = self._new_frame(uow, state, turn_id, "start_run", None, agent, runtime, meter, release, span)
            ...
            uow.commit()
```

En `_continue_in_target`, el `_process` del destino va dentro de:

```python
        with self._telemetry.turn(self._scope(target, frame.turn_id, "turn")) as span:
            result = self._process(..., prelude=prelude, store_result=False, span=span)
```

Con `links=()` en esta tarea; T5 pasa el link.

`agent_core/turn/__init__.py` exporta `NO_SPAN`, `NoTurnTelemetry`, `TransferOutcome`, `TransferSpan`, `TurnScope`, `TurnSpan` y `TurnTelemetry`.

`.importlinter`, contrato `turn`: agregar `agent_telemetry` a `forbidden_modules` con el comentario `# M4 talks to telemetry through TurnTelemetry (F11)`.

- [ ] **Step 4: Implement `agent_telemetry` (trace id fallback), M9 and composition**

`agent_telemetry/context.py`:

```python
_TRACE_FALLBACK: ContextVar[str | None] = ContextVar("agent_telemetry_trace_fallback", default=None)


@contextmanager
def bind_trace_id(trace_id: str) -> Iterator[None]:
    """The request's trace id when no OTel trace is active (M9 sets it from its IdSource, U3)."""
    token = _TRACE_FALLBACK.set(trace_id)
    try:
        yield
    finally:
        _TRACE_FALLBACK.reset(token)


def trace_fallback() -> str | None:
    return _TRACE_FALLBACK.get()
```

`agent_telemetry/spans.py`:

```python
def current_trace_id() -> str | None:
    """The active OTel trace id or, without one, the id bound by `bind_trace_id`."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else trace_fallback()
```

`agent_core/api/tracing.py`: después de calcular `request.state.trace_id`, `call_next` corre dentro de `with bind_trace_id(request.state.trace_id):`. El `ContextVar` llega al endpoint síncrono: `BaseHTTPMiddleware` y el threadpool de anyio copian el contexto, el mismo mecanismo que ya lleva el span OTel.

`agent_core/composition/engine.py`:

```python
class RequestTraceIds:
    """`TraceIds` (U3, F8): the request's trace id (OTel or M9's fallback); outside a request, derived from the
    turn. Never asks the IdSource: that would shift recorded ids and change the committed fixtures."""

    def current(self, turn_id: str) -> str:
        return tel.current_trace_id() or f"trace-{turn_id}"
```

- `DerivedTrace` se elimina del módulo y de `__init__` (nadie la usa fuera de composition; ver `rg DerivedTrace`).
- `EngineDeps.telemetry: TurnTelemetry | None = None` va antes de `config`.
- `build_engine` pasa `trace=deps.trace or RequestTraceIds()` y `telemetry=deps.telemetry`.

`agent_core/composition/telemetry.py` (en esta tarea solo `invoke_agent`; `record` y `transfer` son no-op hasta T4 y T5):

```python
"""`TurnTelemetry` over `agent_telemetry` (ADR 0003 #4, M11 §3.2). Takes no Clock and no IdSource: span ids and
times come from the OTel SDK; derived children take theirs from the events (T4)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from opentelemetry.trace import Span

import agent_telemetry as tel
from agent_core.domain import EngineError, EngineEvent
from agent_core.turn import TransferSpan, TurnScope, TurnSpan
from agent_core.turn.telemetry import NO_SPAN  # reuse the no-op transfer until T5


class _OtelTurnSpan:
    def __init__(self, active: Span, scope: TurnScope) -> None:
        self._active, self._scope = active, scope

    def record(self, events: Sequence[EngineEvent]) -> None:
        return None  # T4

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        with NO_SPAN.transfer(transfer_id) as span:  # T5
            yield span


class OtelTurnTelemetry:
    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        with tel.bind(run_id=scope.run_id, turn_id=scope.turn_id, session_id=scope.session_id,
                      release=scope.release, agent=str(scope.agent)):
            with tel.span(tel.INVOKE_AGENT, attributes={
                    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": scope.agent.id,
                    "agentcore.entry": scope.entry, "agentcore.principal_type": scope.principal_type,
                    "agentcore.locale": scope.locale}) as active:
                try:
                    yield _OtelTurnSpan(active, scope)
                except EngineError as exc:
                    active.set_attribute("agentcore.problem_code", exc.code.value)
                    raise
```

`import agent_core.turn.telemetry` desde composition está permitido: composition importa todo. Si `lint-imports` exige solo la interfaz pública, usar `from agent_core.turn import NO_SPAN`.

- `agent_core/composition/__init__.py` exporta `OtelTurnTelemetry` y `RequestTraceIds`.
- `serve.build_api_deps(ports, *, registry_service=None, telemetry: TurnTelemetry | None = None)` pasa `telemetry` a `EngineDeps`.
- `run_serve` pasa `telemetry=OtelTurnTelemetry()` **siempre**: sin exportador, los spans son no-op, pero `bind` sigue correlacionando los logs con `run_id`.
- `testing/engine_world.EngineWorld(..., telemetry: TurnTelemetry | None = None)` la pasa a `EngineDeps`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/m04 tests/m09 tests/m11 tests/composition -q`, después `uv run pytest -q`, `uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`.
Expected: PASS. `test_el_fixture_committeado_esta_vigente` sigue en verde: el default es no-op y `RequestTraceIds` no consume ids.

- [ ] **Step 6: Spec and commit**

`m04-ciclo-del-turno.md`:
- §2: agregar `telemetry` (`TurnTelemetry`, opcional) a las dependencias por constructor.
- §3.9 nuevo, "Telemetría del turno", en español, con F9 y F12:
  - dónde se abre cada turno;
  - `record` tras cada `append`;
  - sin span en deduplicados, `409` y `410`;
  - M4 nunca importa OTel;
  - la telemetría no cambia eventos.
- §7: T-M4-22 (`tests/m04/test_telemetry_port.py`, `tests/composition/test_turn_telemetry.py`).
- §16: `RequestTraceIds` reemplaza a `DerivedTrace`.

`m09-acceso-y-api.md` §3.7: el middleware publica el trace id con `bind_trace_id`, y el `TurnResult` del mismo request lo devuelve.

```bash
git add agent_core/turn agent_core/composition agent_core/api/tracing.py agent_telemetry .importlinter testing tests/m04 tests/composition docs/specs/motor/m04-ciclo-del-turno.md docs/specs/motor/m09-acceso-y-api.md
git commit -F msg.txt   # feat(turn): TurnTelemetry port, a live invoke_agent per turn and the request's trace id
```

---

### Task 4: hijos `decide`, `rule` y `execute_tool` derivados de los eventos

**Modelo recomendado:** opus.

**Files:**
- Modify: `agent_core/composition/telemetry.py`, `testing/replay/scenarios.py` (`record_scenario(..., telemetry=None)`)
- Test: `tests/composition/test_turn_telemetry.py`, ampliado
- Docs: `m11-auditoria-transcript-replay.md` §3.2 (cómo se generan los hijos), §7 (T-M11-13 y T-M11-14), §10

**Interfaces:**
- Produce: `DerivedSpan(name, start_ns, end_ns, attributes)` y `derived_spans(events: Sequence[EngineEvent]) -> list[DerivedSpan]` (función pura).
- `_OtelTurnSpan.record(events)` llama a `tel.record_span` para cada uno, con el `invoke_agent` como padre.
- Consume: `DecisionMade`, `RuleEvaluated` y `ToolCalled` de `agent_core.domain`.

- [ ] **Step 1: Confirm the timing assumption, then write the failing tests**

Leer `agent_core/actions/execution.py` (alrededor de las líneas 140-205) y confirmar que el `ts` de `tool_called` se toma **después** de la llamada a la tool y de medir `latency_ms`, como en `decision/service.py:103`. Si no es así, **detenerse y preguntar**: F13 supone "fin = `ts`".

Pruebas, agregadas a `tests/composition/test_turn_telemetry.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_core.audit import dump_fixture
from agent_core.composition.telemetry import derived_spans
from agent_core.domain import DecisionMade, RuleEvaluated, ToolCalled
from testing.replay import SCENARIOS, record_scenario

REGISTRY = Path("tests/fixtures/registry-demo")
RUNS = Path("tests/fixtures/runs")
T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _ns(ts: datetime) -> int:
    return (ts - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1_000


def test_a_decision_becomes_a_decide_span_ending_at_its_ts(decision_made: DecisionMade) -> None:  # T-M11-14
    (span,) = derived_spans([decision_made])
    assert span.name == tel.DECIDE and span.end_ns == _ns(decision_made.ts)
    assert span.end_ns - span.start_ns == decision_made.payload.latency_ms * 1_000_000
    assert set(span.attributes) == {"agentcore.decision.id", "agentcore.decision.model",
                                    "agentcore.decision.provider", "agentcore.decision.fallback_depth",
                                    "agentcore.decision.tokens"}


def test_a_rule_is_an_instant_span_without_its_inputs(rule_evaluated: RuleEvaluated) -> None:
    (span,) = derived_spans([rule_evaluated])
    assert span.name == tel.RULE and span.start_ns == span.end_ns == _ns(rule_evaluated.ts)
    assert span.attributes["agentcore.rule.result"] is rule_evaluated.payload.result
    assert "inputs" not in str(span.attributes)


def test_a_tool_call_carries_ids_and_status_never_args_result_or_error(tool_called: ToolCalled) -> None:
    (span,) = derived_spans([tool_called])
    assert span.name == tel.EXECUTE_TOOL
    assert span.attributes["gen_ai.tool.name"] == tool_called.payload.tool.id
    assert span.attributes["gen_ai.tool.call.id"] == tool_called.payload.call_id
    assert span.attributes["agentcore.tool.status"] == tool_called.payload.status.value
    assert set(span.attributes) <= tel.ALLOWED_ATTRIBUTES


def test_other_events_produce_no_children(turn_started: object, turn_completed: object) -> None:
    assert derived_spans([turn_started, turn_completed]) == []  # type: ignore[list-item]


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_recording_with_live_telemetry_keeps_every_fixture_byte_identical(  # T-M11-13, Review Focus 1
        camino: str, otel: InMemorySpanExporter) -> None:
    grabado = dump_fixture(record_scenario(camino, REGISTRY, telemetry=OtelTurnTelemetry()))
    assert (RUNS / f"{camino}.yaml").read_text(encoding="utf-8") == grabado
    names = [s.name for s in otel.get_finished_spans()]
    assert tel.INVOKE_AGENT in names and tel.DECIDE in names  # the telemetry really ran


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_children_match_the_events_and_hang_from_their_turn(camino: str, otel: InMemorySpanExporter) -> None:
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    SCENARIOS[camino](world)
    events = world.audit.read(world.driver.run_id)  # type: ignore[arg-type]
    spans = otel.get_finished_spans()
    for name, kind in ((tel.DECIDE, "decision_made"), (tel.RULE, "rule_evaluated"),
                       (tel.EXECUTE_TOOL, "tool_called")):
        assert sum(s.name == name for s in spans) == sum(e.type == kind for e in events), name
    turns = {s.context.span_id: s for s in spans if s.name == tel.INVOKE_AGENT}  # type: ignore[union-attr]
    for child in (s for s in spans if s.name in (tel.DECIDE, tel.RULE, tel.EXECUTE_TOOL)):
        assert child.parent is not None and child.parent.span_id in turns
        parent_turn = dict(turns[child.parent.span_id].attributes or {})["turn_id"]
        assert dict(child.attributes or {})["turn_id"] == parent_turn


@pytest.mark.parametrize("camino", sorted(SCENARIOS))
def test_no_span_attribute_carries_a_payload_value(camino: str, otel: InMemorySpanExporter) -> None:  # rule 6
    world = EngineWorld(telemetry=OtelTurnTelemetry())
    SCENARIOS[camino](world)
    events = world.audit.read(world.driver.run_id)  # type: ignore[arg-type]
    leaves = _string_leaves([_payload_values(e) for e in events]) | set(world.driver.texts())
    for s in otel.get_finished_spans():
        attrs = dict(s.attributes or {})
        assert set(attrs) <= tel.ALLOWED_ATTRIBUTES, s.name
        for value in attrs.values():
            assert str(value) not in leaves, (s.name, value)


def test_the_replay_engine_has_no_telemetry() -> None:
    from agent_core.turn import NoTurnTelemetry
    from testing.replay.runner import RecordedEngineRunner

    runner = RecordedEngineRunner(...)  # build it as tests/composition/test_replay_fixtures.py does
    assert isinstance(runner.engine._telemetry, NoTurnTelemetry)  # type: ignore[attr-defined]
```

Notas para quien implemente:
- Las fixtures `decision_made`, `rule_evaluated`, `tool_called`, `turn_started` y `turn_completed` se toman de los eventos de un camino grabado (`record_scenario("escalado_por_monto")` trae `rule_evaluated`; `resuelto` trae `tool_called`), con `pytest.fixture(scope="module")`.
- `_payload_values(e)` toma, según el tipo, `args`, `result`, `error`, `inputs` y `value`. `_string_leaves` recorre JSON y devuelve las hojas `str` de 4 o más caracteres; se excluyen los ids, porque `call_id` y `decision_id` sí van en atributos.
- `world.driver.texts()`: si `Driver` no expone los textos del cliente, se toman de `world.driver.ops` (`op["text"]`), **sin** agregar API a `Driver`.
- `RecordedEngineRunner(...)`: copiar la construcción de la prueba citada.

`testing/replay/scenarios.record_scenario(name, registry_root=REGISTRY_DEMO, *, telemetry: TurnTelemetry | None = None)` pasa `telemetry` a `EngineWorld`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/composition/test_turn_telemetry.py -q`
Expected: FAIL. `ImportError: derived_spans`; los conteos de hijos dan 0.

- [ ] **Step 3: Implement**

`agent_core/composition/telemetry.py`:

```python
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class DerivedSpan:
    name: str
    start_ns: int
    end_ns: int
    attributes: Mapping[str, str | int | bool]


def _ns(ts: datetime) -> int:
    return (ts - _EPOCH) // timedelta(microseconds=1) * 1_000


def derived_spans(events: Sequence[EngineEvent]) -> list[DerivedSpan]:
    """M11 §3.2 children from the turn's audit events (U4, F13). Times: `ts` is taken after the call, so a span
    ends at `ts` and starts `latency_ms` earlier; a rule is instantaneous. Never a payload value (rule 6)."""
    out: list[DerivedSpan] = []
    for event in events:
        end = _ns(event.ts)
        if isinstance(event, DecisionMade):
            d = event.payload
            out.append(DerivedSpan(tel.DECIDE, end - d.latency_ms * _NS_PER_MS, end, {
                "agentcore.decision.id": d.decision_id, "agentcore.decision.model": str(d.model),
                "agentcore.decision.provider": d.provider_used,
                "agentcore.decision.fallback_depth": d.fallback_depth, "agentcore.decision.tokens": d.tokens}))
        elif isinstance(event, RuleEvaluated):
            r = event.payload
            attrs: dict[str, str | int | bool] = {"agentcore.node": r.node_id, "agentcore.rule.result": r.result}
            if r.policy is not None:
                attrs["agentcore.rule.policy"] = str(r.policy)
            out.append(DerivedSpan(tel.RULE, end, end, attrs))
        elif isinstance(event, ToolCalled):
            t = event.payload
            out.append(DerivedSpan(tel.EXECUTE_TOOL, end - t.latency_ms * _NS_PER_MS, end, {
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": t.tool.id,
                "gen_ai.tool.call.id": t.call_id, "agentcore.tool": str(t.tool), "agentcore.node": t.node_id,
                "agentcore.tool.status": t.status.value, "agentcore.tool.attempt": t.attempt}))
    return out
```

`_OtelTurnSpan.record`:

```python
    def record(self, events: Sequence[EngineEvent]) -> None:
        for child in derived_spans(events):
            tel.record_span(child.name, parent=self._active, start_ns=child.start_ns, end_ns=child.end_ns,
                            attributes=child.attributes)
```

Sin `try/except` aquí. `record_span` no lanza en producción: es no-op con un padre no grabado y descarta atributos fuera de la lista.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/composition tests/m11 -q`, después `uv run pytest -q` y los cuatro comandos.
Expected: PASS. Los seis fixtures siguen idénticos con telemetría y sin ella.

- [ ] **Step 5: Spec and commit**

`m11` §3.2, reescrito en español con F13 y U4:
- `invoke_agent` en vivo por turno (M4, `TurnTelemetry`);
- los hijos `agentcore.decide`, `agentcore.rule` y `execute_tool` se derivan de los eventos con tiempos de `ts` y `latency_ms`;
- `chat` en vivo desde el gateway;
- lista cerrada de atributos;
- no-op en replay.

§7: T-M11-13 ("grabar con telemetría real deja los fixtures idénticos") y T-M11-14 ("hijos derivados: tiempos, padre y atributos sin valores"). §10: marcar como hechos los spans de la fase 2.

```bash
git add agent_core/composition/telemetry.py testing/replay/scenarios.py tests/composition/test_turn_telemetry.py docs/specs/motor/m11-auditoria-transcript-replay.md
git commit -F msg.txt   # feat(telemetry): derive decide, rule and execute_tool spans from the turn's audit events
```

---

### Task 5: span `agentcore.transfer` y link del run destino

**Modelo recomendado:** sonnet si T3 quedó como se describe; opus si hubo desvíos en `_continue_in_target`.

**Files:**
- Modify:
  - `agent_core/turn/engine.py` (`_resolve_transfer`, `_continue_in_target`);
  - `agent_core/composition/telemetry.py` (`TransferLink`, `_OtelTransferSpan`, padre hermano en `turn`);
  - `agent_telemetry/spans.py`: `TRANSFER = "agentcore.transfer"` y las seis claves `agentcore.transfer.*` en `ALLOWED_ATTRIBUTES`;
  - `agent_telemetry/__init__.py`.
- Test: `tests/m04/test_telemetry_port.py` (T-TR-16, mitad M4), `tests/composition/test_transfer_spans.py` (nuevo)
- Docs: spec de transferencia §8 y §10 (T-TR-16); m04 §3.8 y §3.9

**Interfaces:**
- Consume: `TurnSpan.transfer`, `TransferSpan.finish` y `TransferSpan.link` (T3); `event_target(request)` (`turn/transfer.py`).
- Produce: `agent_core.composition.telemetry.TransferLink(span_context: SpanContext, parent: Context)` (handle opaco para M4).

- [ ] **Step 1: Write the failing tests**

En `tests/m04/test_telemetry_port.py`:

```python
from agent_core.turn import TransferOutcome
from tests.m04.harness import SPECIALIST_RELEASE_ID

TEXT = "no reconozco un cargo"


def _reception(telemetry: RecordingTelemetry, **kwargs: object) -> World:
    w = World(chain_factory=AuditLog, telemetry=telemetry)
    w.reception(**kwargs)  # type: ignore[arg-type]
    return w


def test_a_valid_transfer_reports_its_outcome_and_links_the_target_turn() -> None:  # T-TR-16
    telemetry = RecordingTelemetry()
    w = _reception(telemetry)
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    origin, target = telemetry.turns[-2:]
    (transfer,) = origin.transfers
    moved = next(e for e in w.events() if e.type == "run_transferred")
    assert transfer.transfer_id == moved.payload.transfer_id
    assert transfer.outcome == TransferOutcome(outcome="transferred", to_agent="disputas",
                                               to_release_id=SPECIALIST_RELEASE_ID)
    assert target.links == (transfer.link,) and target.transfers == []
    assert target.scope.run_id == moved.payload.to_run_id and target.scope.turn_id == origin.scope.turn_id
    assert origin.scope.agent.id == "recepcion" and target.scope.agent.id == "disputas"


def test_a_rejection_echoes_to_agent_only_when_the_event_does() -> None:
    telemetry = RecordingTelemetry()
    w = _reception(telemetry, choice="saldos")  # not in the directory read: `transfer_rejected.to_agent` is None
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    (turn,) = telemetry.turns
    (transfer,) = turn.transfers
    assert transfer.outcome == TransferOutcome(outcome="rejected", reason_code="not_in_directory")


def test_a_rejection_of_a_listed_target_echoes_it_without_release() -> None:
    telemetry = RecordingTelemetry()
    w = _reception(telemetry, origin_depth=1)  # `transfer_limit`: the target is in the directory
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    rejected = next(e for e in w.events() if e.type == "transfer_rejected")
    (transfer,) = telemetry.turns[-1].transfers
    assert transfer.outcome == TransferOutcome(outcome="rejected", to_agent=rejected.payload.to_agent,
                                               reason_code="transfer_limit")
    assert rejected.payload.to_agent == "disputas"


def test_transfer_telemetry_does_not_change_the_two_chains() -> None:
    plain, traced = World(chain_factory=AuditLog), World(chain_factory=AuditLog, telemetry=RecordingTelemetry())
    for w in (plain, traced):
        w.reception()
        w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
        w.turn(TEXT)
    for run in plain.session_runs():
        assert [e.model_dump() for e in plain.audit.read(run.run_id)] == \
               [e.model_dump() for e in traced.audit.read(run.run_id)]
```

`tests/composition/test_transfer_spans.py`:

```python
"""ADR 0021 D9 over OpenTelemetry (T-TR-16): `agentcore.transfer` and the target's span link."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel
from agent_core.audit import AuditLog
from agent_core.composition import OtelTurnTelemetry
from tests.m04.harness import SPECIALIST_RELEASE_ID, World
from tests.m04.helpers import cmd

pytest_plugins = ["tests.support.otel"]
TEXT = "no reconozco un cargo"


def _spans(otel: InMemorySpanExporter, name: str) -> list:  # type: ignore[type-arg]
    return [s for s in otel.get_finished_spans() if s.name == name]


def test_transfer_span_attributes_and_the_target_link(otel: InMemorySpanExporter) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    with tel.span(tel.CHAT, attributes={"run_id": "req", "agentcore.release": "req"}) as request:  # stands for the API span
        w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    origin, target = sorted(_spans(otel, tel.INVOKE_AGENT), key=lambda s: s.start_time)
    moved = next(e for e in w.events() if e.type == "run_transferred")
    attrs = dict(transfer.attributes or {})
    assert attrs == {**{k: attrs[k] for k in ("run_id", "turn_id", "session_id", "agentcore.release",
                                               "agentcore.agent")},
                     "agentcore.transfer.id": moved.payload.transfer_id,
                     "agentcore.transfer.from_agent": "recepcion", "agentcore.transfer.to_agent": "disputas",
                     "agentcore.transfer.to_release_id": SPECIALIST_RELEASE_ID,
                     "agentcore.transfer.outcome": "transferred"}
    assert transfer.parent is not None and transfer.parent.span_id == origin.context.span_id  # F10
    (link,) = target.links
    assert link.context.span_id == transfer.context.span_id and link.context.trace_id == transfer.context.trace_id
    assert target.parent is not None and origin.parent is not None
    assert target.parent.span_id == origin.parent.span_id == request.get_span_context().span_id  # siblings
    assert target.context.trace_id == origin.context.trace_id
    assert dict(target.attributes or {})["run_id"] == moved.payload.to_run_id


def test_a_rejected_transfer_has_no_release_and_links_nothing(otel: InMemorySpanExporter) -> None:
    w = World(chain_factory=AuditLog, telemetry=OtelTurnTelemetry())
    w.reception(choice="saldos")
    w.understand.push(cmd("continue"))
    w.turn(TEXT)
    (transfer,) = _spans(otel, tel.TRANSFER)
    attrs = dict(transfer.attributes or {})
    assert attrs["agentcore.transfer.outcome"] == "rejected"
    assert attrs["agentcore.transfer.reason_code"] == "not_in_directory"
    assert "agentcore.transfer.to_release_id" not in attrs and "agentcore.transfer.to_agent" not in attrs
    assert all(not s.links for s in _spans(otel, tel.INVOKE_AGENT))
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m04/test_telemetry_port.py tests/composition/test_transfer_spans.py -q`
Expected: FAIL. `origin.transfers == []`, no hay span `agentcore.transfer` y `target.links == ()`.

- [ ] **Step 3: Implement M4**

`_resolve_transfer`:

```python
        frame.pending_transfer = None
        transfer_id = self._transferer.new_transfer_id()
        with frame.span.transfer(transfer_id) as span:  # ADR 0021 D9: the span covers the validation
            plan = self._transferer.validate(frame, request, transfer_id)
            if isinstance(plan, str):
                span.finish(TransferOutcome(outcome="rejected", to_agent=event_target(request), reason_code=plan))
            else:
                span.finish(TransferOutcome(outcome="transferred", to_agent=plan.target_ref.id,
                                            to_release_id=plan.release.id))
                frame.transfer_link = span.link
        if isinstance(plan, str):
            ...  # unchanged: transfer_rejected, `rejected` branch, advance, recursion
            return
        ...  # unchanged: run_transferred, close_run, transfer_plan
```

`event_target(request)` es la misma función que llena `transfer_rejected.to_agent`, así que el eco coincide por construcción (U5).

`_continue_in_target`:

```python
        links = () if frame.transfer_link is None else (frame.transfer_link,)
        with self._telemetry.turn(self._scope(target, frame.turn_id, "turn"), links) as span:
            result = self._process(..., span=span)
```

- [ ] **Step 4: Implement composition**

```python
@dataclass(frozen=True)
class TransferLink:
    """Opaque to M4: the transfer span's context and the context the origin's `invoke_agent` was opened in, so
    the target's `invoke_agent` is its sibling (F10) and links back to the transfer. Nothing is persisted."""

    span_context: SpanContext
    parent: Context


class _OtelTransferSpan:
    def __init__(self, active: Span, parent: Context) -> None:
        self._active = active
        self.link: object | None = TransferLink(active.get_span_context(), parent)

    def finish(self, outcome: TransferOutcome) -> None:
        attrs: dict[str, str] = {"agentcore.transfer.outcome": outcome.outcome}
        if outcome.to_agent is not None:
            attrs["agentcore.transfer.to_agent"] = outcome.to_agent
        if outcome.outcome == "transferred" and outcome.to_release_id is not None:
            attrs["agentcore.transfer.to_release_id"] = outcome.to_release_id
        if outcome.outcome == "rejected" and outcome.reason_code is not None:
            attrs["agentcore.transfer.reason_code"] = outcome.reason_code
        tel.set_attributes(self._active, attrs)
```

`_OtelTurnSpan` recibe además `parent: Context`: el `otel_context.get_current()` capturado **antes** de abrir su `invoke_agent`. Además:

```python
    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        with tel.span(tel.TRANSFER, attributes={"agentcore.transfer.id": transfer_id,
                                                "agentcore.transfer.from_agent": self._scope.agent.id}) as active:
            yield _OtelTransferSpan(active, self._parent)
```

En `OtelTurnTelemetry.turn`:

```python
        transfer_links = [link for link in links if isinstance(link, TransferLink)]
        parent = transfer_links[0].parent if transfer_links else None  # sibling of the origin's invoke_agent
        otel_links = [Link(l.span_context) for l in transfer_links if l.span_context.is_valid]
        captured = otel_context.get_current() if parent is None else parent
        ... tel.span(tel.INVOKE_AGENT, context=parent, links=otel_links, attributes={...}) as active:
                yield _OtelTurnSpan(active, scope, captured)
```

Nota: las claves `agentcore.transfer.*` se pasan con `attributes=` porque `_key()` convertiría `from_agent` en `from.agent`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/m04 tests/composition -q`, después `uv run pytest -q` y los cuatro comandos.
Expected: PASS.

- [ ] **Step 6: Spec and commit**

Spec de transferencia §8, punto "OTel", reescrito en español:
- `agentcore.transfer` es hijo del `invoke_agent` del origen y cubre la validación;
- atributos de U5, con los nombres `agentcore.transfer.*`;
- en un rechazo, sin `to_release_id`, y `to_agent` solo si `transfer_rejected` lo trae;
- el `invoke_agent` del destino es hermano del del origen y lleva un *span link* al span de la transferencia;
- nada se persiste.

§10: agregar T-TR-16 (`tests/m04/test_telemetry_port.py`, `tests/composition/test_transfer_spans.py`).

m04 §3.8: una línea al final, "la resolución abre `TurnSpan.transfer` (§3.9)". §3.9: el link.

```bash
git add agent_core/turn/engine.py agent_core/composition/telemetry.py agent_telemetry tests/m04/test_telemetry_port.py tests/composition/test_transfer_spans.py docs/specs/2026-09-30-transferencia-entre-agentes-design.md docs/specs/motor/m04-ciclo-del-turno.md
git commit -F msg.txt   # feat(transfer): agentcore.transfer span and a link from the target's invoke_agent
```

---

### Task 6: logs y atributos GenAI

**Modelo recomendado:** sonnet.

**Files:**
- Modify:
  - `agent_core/api/problems.py`;
  - `agent_core/turn/engine.py` (`_release_quietly`);
  - `agent_core/composition/serve_ports.py:236`;
  - `agent_core/composition/registry.py:147`;
  - `agent_core/adapters/llm/gateway.py`.
- Test: `tests/m09/test_problems.py`, `tests/m04/test_lifecycle.py` (o el archivo de las pruebas de lease), `tests/composition/test_serve_ports.py`, `tests/composition/test_registry_cli.py`, `tests/u05/test_observability.py`
- Docs: `2026-09-28-llm-gateway-design.md` §3.5 y §7 (T-U5-19)

**Interfaces:** sin cambios de firma. El `JsonLogFormatter` con `timestamp` y `exc_type` ya quedó en T2.

- [ ] **Step 1: Write the failing tests**

`tests/m09/test_problems.py`:

```python
def test_an_unhandled_error_is_logged_with_trace_id_location_and_route_never_its_text(
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="agentcore.api")
    resp = _client().get("/boom")
    trace_id = resp.json()["trace_id"]
    (record,) = [r for r in caplog.records if r.name == "agentcore.api"]
    message = record.getMessage()
    assert "RuntimeError" in message and f"trace_id={trace_id}" in message
    assert "ruta=/boom" in message and "test_problems.py:" in message  # file:line of the raising frame
    assert "secreto-interno-xyz" not in caplog.text
```

En la prueba de lease del harness de M4 (donde está `test_..._releases_the_lease...`):

```python
def test_a_failed_lease_release_leaves_a_warning_with_the_type(caplog: pytest.LogCaptureFixture) -> None:
    # Make the turn fail after the lease and the release UoW fail too (CommitCounter / a raising uow_factory).
    ...
    assert "lease" in caplog.text and "RuntimeError" in caplog.text and "SECRETO" not in caplog.text
```

`tests/composition/test_serve_ports.py`:

```python
def test_a_failing_piece_factory_reports_only_the_exception_type() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(AGENTCORE_ALLOW_DEMO="1", tools="tests.composition.test_serve_ports:boom_factory")
    text = " ".join(info.value.problems)
    assert "RuntimeError" in text and "SECRETO" not in text


def boom_factory(ctx: object) -> object:
    raise RuntimeError("SECRETO-de-fabrica")
```

En `tests/composition/test_registry_cli.py` va una prueba gemela para `agentcore registry` con un `--harness` que lanza `ImportError("SECRETO")`.

`tests/u05/test_observability.py` (T-U5-19):

```python
def test_genai_attributes_follow_the_semconv(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(120, 30)))
    tracer, exporter = _tracer()
    make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert span.name == "chat vendor/modelo-x"
    assert attrs["gen_ai.provider.name"] == "openai" and attrs["agentcore.endpoint_alias"] == "openrouter"
    assert attrs["gen_ai.response.finish_reasons"] == ("stop",)


def test_finish_reasons_are_recorded_on_a_truncated_output(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("{", usage=(5, 5), finish_reason="length"))
    ...  # GatewayError(invalid_output); the span still has finish_reasons == ("length",)


def test_the_chat_span_carries_the_turn_correlation(respx_mock: MockRouter) -> None:
    import agent_telemetry as tel

    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(1, 1)))
    tracer, exporter = _tracer()
    with tel.bind(run_id="run-0001", turn_id="turn-0001", release="rel-1", agent="atencion@1.0.0"):
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["run_id"] == "run-0001" and attrs["agentcore.release"] == "rel-1"
```

La prueba existente `test_a_call_emits_one_chat_span_with_genai_attributes` cambia en `span.name` y `gen_ai.provider.name`, según F14 y la pregunta 10. Si `completion()` de `tests/u05/helpers.py` no acepta `finish_reason`, se agrega ese parámetro con valor por defecto `"stop"`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m09/test_problems.py tests/m04 tests/composition/test_serve_ports.py tests/composition/test_registry_cli.py tests/u05 -q`
Expected: FAIL en cada prueba nueva.

- [ ] **Step 3: Implement**

`problems.py`:

```python
import traceback
from pathlib import Path


def _where(exc: BaseException) -> str:
    """`file:line` of the frame that raised: a location, never the message."""
    frames = traceback.extract_tb(exc.__traceback__)
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"


def _route(request: Request) -> str:
    """The route template (`/v1/sessions/{session_id}/turns`), never the path with its ids."""
    route = request.scope.get("route")
    return str(getattr(route, "path", "?"))

    ...
    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> ProblemResponse:
        trace_id = request_trace_id(request)
        # only the type and where: the exception's text may carry secrets (I3)
        log.error("error no controlado: %s en %s ruta=%s trace_id=%s",
                  type(exc).__name__, _where(exc), _route(request), trace_id)
        return problem(ProblemCode.internal_error, "", trace_id)
```

`turn/engine.py`:

```python
_LOG = logging.getLogger("agent_core.turn")
...
        except Exception as exc:  # the retry will wait for the lease TTL; leave a trace (I3)
            _LOG.warning("lease sin liberar run_id=%s turn_id=%s causa=%s", run_id, turn_id, type(exc).__name__)
```

`serve_ports.py:236`: `f"no se pudo cargar {name} ({path}): {type(exc).__name__}; revisa ..."`. Se quita `: {exc}`, igual que en `cli.py:102,121,207`. Ídem en `registry.py:147`: `f"no se pudo cargar el verificador o el harness ({type(exc).__name__}); ..."`.

`gateway.py`, en `generate`:

```python
        with self._active_tracer().start_as_current_span(
                f"chat {profile.model}", record_exception=False, set_status_on_exception=False) as span:
            span.set_attributes(dict(tel.correlation()))  # ADR 0003 #4: run_id, release... of the bound turn
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "openai")  # the wire protocol (F14)
            span.set_attribute("agentcore.endpoint_alias", profile.endpoint_alias)
            ...
```

En `_result`, justo después de `choice = response.choices[0]`:

```python
    if choice.finish_reason:
        trace.get_current_span().set_attribute("gen_ai.response.finish_reasons", [str(choice.finish_reason)])
```

El span `chat` es el actual: `_result` corre en el hilo del llamador, dentro del `with`. Hay que conservar `from opentelemetry import trace` para `get_current_span`.

- [ ] **Step 4: Run the tests**

Run: las carpetas del paso 2, `uv run pytest -q` y los cuatro comandos.
Expected: PASS.

- [ ] **Step 5: Spec and commit**

Gateway §3.5:
- `gen_ai.provider.name = "openai"`, con el alias en `agentcore.endpoint_alias`;
- nombre del span `chat {model}`;
- `gen_ai.response.finish_reasons`;
- correlación del turno.

§7: T-U5-19.

```bash
git add agent_core/api/problems.py agent_core/turn/engine.py agent_core/composition/serve_ports.py agent_core/composition/registry.py agent_core/adapters/llm/gateway.py tests docs/specs/2026-09-28-llm-gateway-design.md
git commit -F msg.txt   # fix(logs): correlatable 500s, a trace for an unreleased lease, no exception text; GenAI attributes
```

---

### Task 7: documentación veraz

**Modelo recomendado:** sonnet.

**Files:** `docs/specs/motor/m11-auditoria-transcript-replay.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`, `docs/adr/0003-observabilidad-dos-planos.md`, `docs/adr/0021-transferencia-entre-agentes.md`, `docs/specs/motor/m04-ciclo-del-turno.md`, `docs/specs/motor/00-indice.md`, `docs/specs/2026-09-30-transferencia-entre-agentes-design.md`, `README.md`.

- [ ] **Step 1: Read what each earlier task already wrote**

Leer el diff de T1 a T6 sobre `docs/` para no duplicar. Esta tarea **reconcilia**; no reescribe lo que cada tarea ya dejó en su spec.

- [ ] **Step 2: Edit**

- **M11:**
  - el estado (cabecera) agrega "rev. 5 (2026-10-02): plano operativo cableado";
  - §3.2 queda como lo dejó T4;
  - §1, responsabilidad 2: "spans OTel en vivo (`invoke_agent` por turno, `chat`) e hijos derivados de los eventos";
  - §10: T-M11-12 a T-M11-14 hechos.
- **`TEMAS-ABIERTOS-PENDIENTES.md`:**
  - línea 64: "Phoenix para latencia y costo (spans OTel `agentcore.*` desde el 2026-10-02: `invoke_agent` por turno con hijos derivados de los eventos; ver M11 §3.2)";
  - "Pendiente de construcción": las vistas SQL. La conexión a Phoenix queda documentada en el README;
  - #19: quitar "Spans OTel" de pendientes y el "sin spans OTel" del encabezado; agregar "hecho (2026-10-02)".
- **ADR 0003:**
  - #4, nota de enmienda: "`trace_id` de la respuesta = trace id del request (OTel o respaldo del `IdSource` de M9); un reintento deduplicado devuelve el del intento original (F16)";
  - #1, notas:
    - variables `OTEL_*` estándar;
    - provider propio sin global;
    - telemetría nativa de FastAPI apagada;
    - hijos derivados de eventos (híbrido) y por qué no rompe el determinismo;
  - "Consecuencias": los atributos de span son una lista cerrada (`ALLOWED_ATTRIBUTES`).
- **ADR 0021:** línea de estado sin "sin spans OTel"; D9 apunta a la spec §8.
- **Spec de transferencia:** cabecera de estado; §11, fila 6, "hecha" (con spans OTel desde 2026-10-02); §12.15 sin "spans OTel".
- **`00-indice.md:226`:** quitar "sin spans OTel". Si el índice tiene una tabla de puertos por módulo, agregar `TurnTelemetry` (M4, local) en la fila de M4.
- **m04:** comprobar que §2, §3.9, §7 y §16 son coherentes entre sí.
- **README:** sección nueva "Telemetría" después de "Comandos", en español:
  - qué sale (spans `agentcore.api.request`, `invoke_agent` y sus hijos, `chat`, `agentcore.transfer`; logs JSON a stderr);
  - tabla de variables `OTEL_*` admitidas (F4);
  - Phoenix local: `docker run -p 6006:6006 arizephoenix/phoenix` y `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006 agentcore serve …`;
  - que el contenido está apagado y los atributos son una lista cerrada;
  - que `trace_id` en cada respuesta permite ir a la traza;
  - que FastAPI no exporta nada por su cuenta;
  - la línea 86 (`agent_telemetry/  trazas OpenTelemetry`) pasa a "trazas OpenTelemetry y logs JSON correlacionados".

- [ ] **Step 3: Full verification**

Run:
- `uv run pytest -q`, dos veces seguidas: Review Focus 4, fugas de estado global entre pruebas;
- `uv run pytest -q -p no:cacheprovider tests/m11 tests/m09 tests/composition`, en otro orden de carpetas que el de la primera corrida;
- `uv run lint-imports`, `uv run mypy`, `uv run ruff check .`, `uv run agentcore contracts --check`;
- `git diff --stat ebbeaf7 -- agent_core/domain agent_core/ports contracts tests/fixtures/runs`.

Expected: todo en verde. El último comando **no lista nada**: ni M0, ni contratos, ni fixtures cambiaron. `tests/integration` se omite (sin docker); decirlo en el informe.

- [ ] **Step 4: Commit**

```bash
git add README.md docs
git commit -F msg.txt   # docs(observability): M11 §3.2, TEMAS, ADR 0003/0021, transfer spec and README telemetry made truthful
```

---

## Self-review

- **Cobertura de lo pedido:**

| Pedido | Tarea |
|---|---|
| C1: telemetría de FastAPI apagada; prueba con `OTEL_EXPORTER_OTLP_ENDPOINT` de que no hay providers globales y los planos de logs y métricas quedan apagados | 1 (F1, T-M9-15; verificado en el paquete y en el scratchpad) |
| C2: `record_exception=False`, estado sin mensaje y `http.response.status_code` en el error | 1 (F2, T-M9-16) |
| `setup_observability` con `Resource`/`service.name` de `OTEL_*`, formatter JSON en root, silenciar `openai`/`httpx`, flush y shutdown, tracer inyectado en el gateway, `span()` no-op sin `bind` | 2 (F3, F4, F6, F7) |
| Puerto `TurnTelemetry`, `invoke_agent` por turno, `trace_id` real con respaldo determinista | 3 (F8, F9, F11, F12) |
| Hijos derivados con tiempos de `ts` y `latency_ms`; no-op en replay; eventos y hashes intactos | 4 (F13; T-M11-13 graba los seis fixtures con telemetría real) |
| Span de transferencia, atributos de U5, link y nada persistido | 5 (F10) |
| Log del 500, `except: pass`, prints con `{exc}`, atributos GenAI y `finish_reasons` | 6 (F14); el `timestamp` del formatter quedó en 2 |
| M11 §3.2, TEMAS línea 64, ADR 0003, m04, spec de transferencia §8/§12 y ADR 0021, README | 7, más el paso de spec de cada tarea |

- **Hallazgo nuevo fuera de la auditoría:** uvicorn registra mensaje y stack de toda excepción no controlada (F5, pregunta 7). Lo corrige T2.
- **Tipos y nombres consistentes:**
  - `TurnScope`, `TransferOutcome`, `TurnSpan`, `TransferSpan`, `TurnTelemetry`, `NoTurnTelemetry` y `NO_SPAN` (T3) los usan T4, T5 y `testing/fakes/telemetry.py`;
  - `OtelTurnTelemetry`, `RequestTraceIds`, `TransferLink` y `derived_spans` viven en `agent_core/composition/telemetry.py` y `engine.py`;
  - `tel.TRANSFER` y las claves `agentcore.transfer.*` se agregan en T5 a `ALLOWED_ATTRIBUTES`.
- **Sin cambios de M0 ni de `contracts/`:** `TurnScope` y `TransferOutcome` son de M4. El paso 3 de T7 lo comprueba con `git diff`.
- **Determinismo:**
  - la telemetría no recibe `Clock` ni `IdSource`;
  - `RequestTraceIds` no consume ids;
  - el replay usa el no-op;
  - T-M11-13 lo prueba sobre bytes, no sobre una muestra.
- **Riesgos principales:**
  1. `BaseHTTPMiddleware` y la propagación del `ContextVar` de `bind_trace_id` al endpoint síncrono. Lo cubre `test_the_turn_trace_id_is_the_request_fallback_without_otel`; si falla, pasar el respaldo con `request.state` y un `Depends`, no con un global.
  2. `record_span` con tiempos del `FakeClock` (año 2026) anteriores o posteriores al padre vivo: OTel lo admite y solo afecta la visualización en pruebas. En producción, `SystemClock` y el SDK usan el mismo reloj de pared.
  3. La suposición de F13 sobre el `ts` de `tool_called`: T4 la comprueba antes de escribir código.
- **Abiertos no resueltos:**
  - todas las preguntas 1 a 16;
  - el replay de sesión (§12.10 de la spec de transferencia), el linaje por run sin `origin` (§12.19) y las vistas SQL de TEMAS §11 quedan como están.
