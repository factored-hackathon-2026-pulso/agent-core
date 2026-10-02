# ADR 0003 — Observabilidad en dos planos

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por los hallazgos R2 e I1 de la revisión externa, por los temas #3 (alcance del replay) y #4 (huellas con clave) de la auto-revisión, y por las métricas de eficiencia (spec rev. 14).
- Unidad: 0 · Fundamentos (obligatoria en todo repo)

## Contexto
El reto exige trazas, registros de ejecución y auditoría determinista, y advierte que "el chain-of-thought oculto no es un artefacto de auditoría". Los backends de trazas muestrean y retienen con pérdida.

**(Auto-revisión #3)** La spec prometía que el replay reproduce la secuencia de `node_entered`, pero `rule`, `verify` y las tools `compute` operan sobre la vista `full`, y el log de auditoría solo guarda la vista `audit`. Sobre un run real el replay no podía recalcular esos nodos, y leerlo todo de eventos vaciaba la garantía.

**(Auto-revisión #4)** El log guardaba el `sha256` sin clave de cada entrada del transcript. Un mensaje corto se adivina, y después de suprimir el transcript el hash seguía permitiendo confirmar qué decía.

## Decisión
1. **Trazas operativas**
   - OpenTelemetry con las GenAI semantic conventions (spans `invoke_agent`, `chat` y `execute_tool`), más atributos propios `agentcore.*`.
   - Se exportan por OTLP.
   - El backend de la demo es Arize Phoenix: un contenedor, OTel nativo, con datasets y experimentos.
   - La versión de semconv queda fijada.
   - **Enmienda (2026-10-02):** la telemetría nativa de FastAPI (≥ 0.142) se apaga; la exportación la configura la raíz de composición (`setup_observability`, `agentcore serve`).
     - **Configuración:** variables `OTEL_*` estándar (subconjunto: OTLP/HTTP-protobuf, `otlp` o `none`, los seis samplers estándar; M11 §3.2). Sin endpoint no hay trazas y los logs JSON siguen.
     - **Provider propio, sin global:** `agent_telemetry` crea su provider y no lo instala como el global de OTel; los tracers de agentcore salen de `tracer(name)`.
     - **Spans híbridos:** `invoke_agent` es en vivo, uno por turno, y lo abre M4 por un puerto local (`TurnTelemetry`, m04 §3.9) sin importar OpenTelemetry. Los hijos `agentcore.decide`, `agentcore.rule` y `execute_tool` se derivan de los eventos ya encadenados, con los tiempos de su `ts` y `latency_ms`. Esto no rompe el determinismo: la telemetría solo lee eventos ya construidos, no recibe `Clock` ni `IdSource`, y el replay usa la implementación no-op. Grabar los seis caminos con telemetría real da los mismos bytes que los fixtures (T-M11-13).
     - **Mejor esfuerzo:** una falla de la telemetría no rompe el turno, y los hijos de un turno que luego se revierte se exportan igual.
2. **Log de auditoría**
   - Tabla append-only en Postgres, con un evento por decisión, encadenado por hash **dentro de cada run** (enmienda I1). La cadena usa `sha256` sobre eventos que ya están en vista `audit`.
   - El núcleo no define "caso": un caso de negocio es un `subject {kind: case, ref}`, y los reportes por caso agrupan runs por subject.
   - Es la fuente de verdad de la evaluación y de la auto-mejora.
   - No depende del backend de trazas.
   - **Enmienda (métricas de eficiencia, 2026-09-28):** como las trazas se muestrean, las métricas de tiempo y costo también salen del log: `latency_ms` en `decision_made` y `tool_called`, uso del LLM en `response_emitted` y el evento `turn_completed` con la duración del turno por etapa. Son **campos de medición**: se miden con `Clock.monotonic_ns()`, ninguna decisión depende de ellos y el replay los excluye de la comparación (M0 §2.10).
3. **Qué se registra como razonamiento**
   - Registros de decisión estructurados:
     - nodo y transición;
     - salida tipada del modelo;
     - regla evaluada con sus entradas y su resultado;
     - tool con argumentos y resultado enmascarados;
     - verificación;
     - `entidad@versión`.
   - Nunca se registra el **razonamiento intermedio** ni el chain-of-thought del modelo.
   - **Enmienda R2:** la regla anterior **no** alcanza al texto del usuario, a la respuesta final mostrada ni a los borradores que el validador rechazó.
     - Esos textos van a un **transcript store** separado (unidad 7), en vista `model` (PII tokenizada, ADR 0008), con retención y supresión propias.
     - El log de auditoría solo guarda su **huella con clave** (HMAC-SHA256 con `kid`, ADR 0008; enmienda #4), así que un transcript se puede suprimir sin romper la cadena de hash y, sin la clave, la huella no permite confirmar qué decía.
     - Así la evaluación de Understand, el held-out tomado de tráfico y la auto-mejora tienen el texto que necesitan, sin atar datos personales a un log inmutable.
4. **Paquete común `agent-telemetry`, que usa todo repo**
   - Toda respuesta de API lleva `trace_id`.
     - **Enmienda (2026-10-02):** es el trace id del request (el de OTel si hay una traza activa; si no, un respaldo del `IdSource` de M9). Un reintento deduplicado devuelve el del intento original. Fuera de un request, sin provider, el motor usa `trace-{turn_id}`.
   - Todo span y todo evento llevan `run_id`, `turn_id` (si aplica), `session_id` (si aplica) y `agentcore.release`.
   - `run_started` lleva los atributos de reporte autorizados (`reportable_attrs`: p. ej. país, segmento, canal, locale), y cada `turn_started` lleva el idioma detectado, para comparar resultados por idioma y segmento sin re-identificar (hallazgo R3).
   - La captura de contenido **en trazas** está desactivada por defecto y, cuando se activa, usa la vista `audit`.
   - Los logs son JSON y se correlacionan con la traza.
5. **Replay en dos modos (auto-revisión #3)**
   - El replay nunca re-ejecuta modelos ni tools externas: sus salidas se leen de los eventos. Antes de consumirlos, verifica la cadena de hash del run.
   - **`fixture` (CI):** runs grabados con datos sintéticos cuyo fixture incluye las entradas en vista `full`. Recalcula transiciones, `rule`/`policy`, `compute`, predicados de `verify`, validador de respuesta y reclamos de éxito. Una divergencia bloquea el merge.
   - **`audit` (runs reales):** solo usa el log de auditoría. Verifica la integridad y recalcula las transiciones; los resultados que dependen de la vista `full` se leen de `rule_evaluated`, `tool_called`, `action_verified` y `response_emitted`.
   - La garantía de cada modo queda declarada en la spec (§11); `audit` no re-verifica la evaluación de reglas sobre datos reales.

## Alternativas
- **(R2) Guardar el texto dentro del log de auditoría:** se descarta porque ata datos personales a un log append-only que no puede suprimirse.
- **(R2) No guardar texto:** se descarta porque impide evaluar Understand y alimentar la auto-mejora.
- **Langfuse:** licencia MIT y gestión de prompts, pero exige 5 o más servicios (ClickHouse, Redis, S3). Es la mejor opción para producción a escala y se adopta cambiando solo el endpoint OTLP.
- **Jaeger/SigNoz genérico:** no tiene vistas de LLM.
- **(#3) Replay solo de transiciones en todos los casos:** se descarta porque un bug en la evaluación de reglas o en una `compute` no se detectaría nunca.
- **(#3) Almacén cifrado de entradas `full` con retención limitada y borrado por destrucción de clave:** permite recálculo completo de runs reales, pero agrega un almacén con PII en reposo. Queda como **diseño de producción**.
- **(#4) Clave de huellas por subject:** permite borrado criptográfico por cliente; queda como **diseño de producción** (ADR 0008).

## Consecuencias
- **(#3)** Los fixtures de CI se generan solo con datos sintéticos. La cobertura de fixtures por camino del flow es lo que hace útil el modo `fixture`.
- **(#3)** El modo `audit` sirve para demostrar integridad y consistencia del camino, no para detectar bugs que solo aparecen con datos reales.
- **(#4)** El núcleo calcula la huella del transcript; el transcript store (unidad 7) devuelve solo un `entry_id`.
- **(2026-10-02)** Los atributos de span son una lista cerrada (`ALLOWED_ATTRIBUTES`): ids, referencias, enums y contadores; nunca un valor de payload ni texto del cliente. Un atributo fuera de la lista se descarta; en modo estricto (pruebas) lanza `ValueError`. Los tracers crudos (`agentcore.api.request`, `chat`) tienen su propia lista cerrada.

## Riesgos y lo no verificado
- No pude confirmar que las GenAI semconv de OTel sean estables; se fija la versión que se usa.
- La comparación entre Phoenix y Langfuse proviene de fuentes secundarias.

## Fuentes
- https://opentelemetry.io/blog/2026/genai-observability/
- https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md
- https://www.morphllm.com/comparisons/arize-phoenix-vs-langfuse
- https://www.rfc-editor.org/rfc/rfc8785 (JCS)
