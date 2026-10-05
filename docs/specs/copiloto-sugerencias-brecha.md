# Brecha del agente `copiloto-sugerencias` (ADR 0026)

Fecha: 2026-10-05 · Estado: **núcleo implementado (B1 a B4 resueltas); datos y `eval_suite` PROVISIONALES (B5 parcial)**. Fuentes: ADR 0026 (aceptado el 2026-10-05), `support-platform/docs/platform/api/slice-15b-copilot-suggestions.md`, plataforma ADR 0005 y el código de la plataforma (`application/ai/suggestions.py`, `infrastructure/ai/http_runtime.py`, `domain/ai/suggestion.py`), leídos sin modificarlos.

**Cambio de interfaz:** `SCHEMA_VERSION` 1.5.0 → **1.6.0** (menor, aditivo). `contracts/` regenerado (`AnyEvent`, `Flow`, `Node`, `RunResult` y los tipos nuevos). Avisar a todos los módulos y a la plataforma: la respuesta de `POST /v1/runs` lleva ahora `suggestions` (siempre; vacía si no hay).

## 0. Qué hay hecho y qué no

| Pieza | Estado |
|---|---|
| `input` de un run `task` con lista de turnos (`list` slot, commit 601ca11) | Hecho en el núcleo |
| Tipo `Suggestion` (`reply`, `tool`, `action`, `escalate`) y `RunResult.suggestions` (B1) | **Hecho** (M0 rev. 16, M4) |
| Evento `suggestions_produced` (contadores, nunca texto) (B1) | **Hecho** (M0, M2); catálogo de métricas `engine.suggestions_produced` |
| Nodo `suggest` propio (B2): M0 (esquema), M1 (G0-28 y alcance de otras reglas), M2 (manejador), M8 (`Suggester`), composición | **Hecho** |
| `Step.input` en `start` del `eval_suite` y el arnés arma `RunInput` con `input` (B3) | **Hecho** |
| `Expect` con aserciones sobre `suggestions` (B4) | **Hecho** (`suggestion_count`, `suggestions: [SuggestionExpect]`; el arnés devuelve `ScenarioRun`) |
| Flow `sugerir@1`, modelo `sin-sugerencia`, prompt `p/sugerir`, política de escalamiento, calibración, release `sugerencias-demo` (B5) | **Hecho, PROVISIONAL** (`tests/fixtures/copiloto-sugerencias/`) |
| `eval_suite` cargable y ejecutable con modelo y clasificador guionados (B3, B4, B5) | **Hecho**: 6 escenarios; **faltan** otro cliente fuera de la delegación y portugués (la inyección ya está: 7 escenarios) |
| Agente probado con un modelo REAL, calibración con datos reales, política real de escalamiento | **No hecho** |

Lo que **no** se probó: ningún modelo real produjo estas sugerencias. En la suite, el «modelo» y el clasificador son dobles que devuelven la salida esperada de cada caso: la suite prueba el flow, la política y la validación de M8, no la calidad de un modelo. Lo "esperado" sigue siendo una especificación, no una medición.

## 1. Brechas del núcleo

### B1. La salida: `Suggestion` y `RunResult.suggestions` (M0, M4, M9, M11) — RESUELTA
- M0: `domain/suggestions.py` (unión discriminada por `type`, límites de longitud de la plataforma, `executable: Literal[False]`), `RunResult.suggestions: list[Suggestion] = []` (un resultado guardado antes se sigue leyendo) y `SCHEMA_VERSION` 1.5.0.
- M4: `start_run` entrega la lista que acumuló el flow, **solo si el run termina `completed`**; se guarda con el resultado idempotente.
- M9: `POST /v1/runs` la publica (`to_jsonable`). **No se agregó a `GET /v1/runs/{id}`**, que tampoco publica `output`: la plataforma lee `suggestions` de la respuesta de `POST` (pendiente solo si la plataforma pidiera `GET`).
- M11/replay: evento `suggestions_produced` con contadores y tipos, `result`, `failures` (ids de comprobaciones), `regenerations`, una huella con clave de la lista y el uso del LLM (`llm` es campo de medición: el replay lo excluye). Nunca el texto.
- Se descartó `TurnResult.suggestions` (nada lo usa).

### B2. El nodo `suggest` (M1, M2, M8) — RESUELTA (Abierto 1: nodo propio)
Ver ADR 0026 §2. M1: G0-28 (solo flows task; el flow no escribe; `actions_allowed` son escrituras aparte de `tools_allowed`; `escalate` solo por la rama `true` de una `rule`) y G0-06/07/10/15/22/24/25 alcanzan al nodo. M2: `SuggesterPort` y manejador (no ejecuta tools; la evidencia de `escalate` la arma el flow y no va al modelo). M8: `Suggester` (generar, validar, regenerar una vez). La composición cablea `SuggesterAdapter`. G0-22 no se relajó: la salida del nodo no es un hecho.

### B3. El `eval_suite` no podía alimentar un run `task` — RESUELTA
`Step.input: dict[str, JsonValue] | None` (solo en `start`), que el arnés pasa a `RunInput.input`. Un `input` ausente **no entra al volcado** (los hashes de las suites ya publicadas no cambian; lo fija una prueba).

### B4. El `eval_suite` no podía afirmar sobre `suggestions` — RESUELTA
`Expect.suggestion_count` y `Expect.suggestions` (`type`, `expect` at_least_one/none, `tool`, `reason_code`, `language`, `citations_min`, `text_contains`, `text_excludes`; todos los campos dados valen sobre la misma sugerencia). Las sugerencias son texto para una persona y no van a los eventos, así que el arnés devuelve además `ScenarioRun(events, suggestions)` (`SuggestionAwareHarness`); con un arnés que solo devuelve eventos, una expectativa sobre sugerencias **falla cerrada**. Un valor de `sensitive_values` en una sugerencia cuenta como fuga (`platform_pii_leak`). Las `assertions` sobre `engine.suggestions_produced` también sirven.

### B5. Datos que faltaban — PARCIAL (todo PROVISIONAL y marcado)
- **Hecho (provisional):** flow `sugerir@1`, modelo de decisión `sin-sugerencia` (señales `sin_sugerencia`/`sugerir`/`escalar`; umbrales a mano en `cal-sugerencias-provisional`), prompt `p/sugerir` (ES/PT, sin probar con un modelo real), política `sugerir-escalamiento` (marcador: no es `escalamiento-disputa-monto`, no dispara ningún traspaso), `budgets` propios (Abierto 4: 0,10 USD, 4 llamadas, 20 s), `max_items` 3 (Abierto 2), `motive_draft` en español (Abierto 3), `actions_allowed` vacío (Abierto 6) y release `sugerencias-demo`. El directorio es un registro autocontenido; las plantillas, tools, detección de idioma y reglas de inyección son copias de `registry-e2e` (deduplicar cuando haya un registro de fixtures compartido).
- **Pendiente:** la **política real de escalamiento** (supervisores; plataforma ADR 0005 abierto 3); la mejora «tercer contacto en 7 días» (se quitó por ahora); calibrar `sin_sugerencia` con datos reales y decidir quién etiqueta (Abierto 5); medir el tope de costo y el tiempo con un modelo real; los tres casos del ADR §8 que faltan (inyección, otro cliente fuera de la delegación —necesita la autorización real de M9, no la permisiva del arnés— y portugués).

## 2. Discrepancias entre agent-core (ADR 0026) y la plataforma

Se verificó con una ejecución puntual (antes de implementar) que las salidas esperadas de los casos, pasadas por `_suggestions` y `normalize_suggestions` de la plataforma, se parsean sin perder tipo ni texto.

| # | Discrepancia | Estado |
|---|---|---|
| D1 | **Forma del `input`.** La plataforma enviaba `sla` y `sugerencia_anterior` como objetos y `motivo_llegada: null`; el motor solo acepta escalares y una lista plana. | **Decidido:** la plataforma aplana su `input` (otro agente lo hace) y omite los nulos; el núcleo NO acepta objetos ni null. El `input_schema` aplanado del agente se mantiene (`sla_estado`, `sla_minutos_restantes`, `sugerencia_borrador`, `sugerencia_escalacion_aceptada`). Hasta que la plataforma lo aplane, `start_run` responde 422 (lo fija `test_discrepancy_d1_…`). |
| D2 | `input.assistant_session_id` no lo envía la plataforma. | **Resuelto en el ADR §1:** se recibe solo como metadato de trazabilidad (correlación de logs/auditoría), no como memoria; ningún nodo lo lee (prueba). Sin efecto hasta que la plataforma lo envíe. |
| D3 | `tool.label`: la plataforma lo muestra; el ADR no lo definía. | **Decidido:** lo emite la plataforma; el ADR no lo define. |
| D4 | `args` de `tool` y `action` y `action.executable`: la plataforma no los lee. | Informativo. El núcleo valida `args` contra el `args_schema` y `executable` es siempre `false`. |
| D5 | Máximo y duplicados: la plataforma conserva a lo sumo 8 sugerencias, un solo `reply` y un solo `escalate`. | **Resuelto:** máximo 3 (Abierto 2) y M8 rechaza un segundo `reply` o `escalate` (`duplicate`) en vez de perderlos en silencio. |
| D6 | «Tercer contacto en 7 días»: `build_input` no envía el dato. | **Decidido:** la regla se quita por ahora (mejora futura). |
| D7 | Tiempo: la plataforma espera 60 s como máximo y promete 5-10 s. | El presupuesto del agente es de 20 s por turno (PROVISIONAL, Abierto 4); sin medir con un modelo real. |
| D8 | Catálogo de tools: el agente usa 3 lecturas, un subconjunto de las 5 del copiloto Q&A. | Informativo; seguro. |
| D9 | `SCHEMA_VERSION`: 1.4.0 (slots `list`) y 1.5.0 (`AgentStepPayload.tokens`, trazas a Langfuse) ya estaban en `main`. | Se subió a **1.6.0** (menor, aditivo sobre 1.5.0). |
| D10 | Límites que la plataforma aplica en silencio (`evidence`, `summary`, `why`, `motive_draft`, `citations`, `tool`, `reason_code`, `language`). | **Resuelto:** los tipos de M0 usan esos mismos límites, así que lo que el núcleo acepta la plataforma lo conserva entero. |

Coinciden: nombres snake_case en el HTTP de agent-core (`reason_code`, `motive_draft`); `suggestions` leído de la respuesta de `POST /v1/runs`; lista vacía como resultado normal (`status: none`); `reason_code` con los prefijos `rule:`/`policy:`/`interrupt:` o de M0; `escalate` solo recomienda; `action` no ejecutable. **Nuevo:** la referencia de `tool` sale como `id@MAYOR` (`leer_movimientos@1`); si la plataforma espera otra forma, hay que acordarla.

## 3. PII y texto no confiable
- Todo slot entra al modelo envuelto como `untrusted_text` (M7 D8) y la PII detectada en el texto (tarjeta, correo, cédula, celular de los casos sintéticos) se sustituye por tokens del vault. Un cierre falso `</datos_no_confiables>` dentro de un turno queda escapado. No hace falta añadir `turnos[*].texto` al catálogo de campos.
- **Salida (nuevo):** M8 rechaza una sugerencia con PII de un hecho (`find_clear_pii`), con un valor que M7 ocultó del mensaje del cliente (`find_tokenized_echo`, nuevo en M7 §3.7.1) o con cualquier token. La suite lo ejercita con mutaciones (la tarjeta y el correo del cliente vuelven en el borrador: la suite falla). El evento `suggestions_produced` nunca lleva texto.
- **Hueco conocido de M7 (no corregido aquí):** el detector no ve un PAN con separadores U+200B, `/` o `_`, dígitos árabe-índicos, un celular con paréntesis ni un correo con `@` separado; esas variantes llegan en claro a la vista `model` y, al no entrar al vault, tampoco se detectan como eco en la salida (`test_known_gap_…` fija uno). Los casos sintéticos solo cubren formatos canónicos, así que **no** se demuestra que no haya fugas por formato.
- La evidencia de `escalate` sale de valores escalares de slots (o hechos) con la vista del detector: un valor con PII hace `gave_up`. La evidencia no se envía al modelo.
- No se verificó con un modelo real que no repita PII: la suite usa un modelo guionado.
- Reglas del validador independiente de `test_copiloto_sugerencias_data.py` que el ADR no fija (copiadas de la plataforma o supuestas): `text` ≤ 4000, `language` ∈ locales del agente, `evidence` ≥ 1, `why` no vacío.
- `assistant_session_id`: la plataforma podría enviarlo en una rama que no pude leer; D2 vale para su código principal.

### Pendientes de M7 (huecos hallados por la revisión; no se tocó el detector)
PAN con U+00AD, U+2060, saltos de línea y 4+ espacios; dígitos árabe-índicos y fullwidth; celular con paréntesis + U+200B parcialmente tokenizado; correo ofuscado (`arroba`, `[at]`, ` @ `, `punto`, U+200B en el dominio) que llega a una sugerencia con `result=ok`; dígitos escritos con palabras. Hoy la única defensa contra cifras con separadores es el chequeo `numbers` (los `args` no lo tienen). **El agente de M7 (rama aparte) cubre la normalización antes de detectar; al fusionarse hay que re-correr la suite del copiloto.**

### Cambios de la ronda de correcciones (2026-10-05)
- Esquema OpenAPI de `Step`/`Expect` conservado (`exclude_if` en vez de `model_serializer`; prueba `test_openapi_keeps_suite_schemas`).
- G0-28 exige `suggested` → `end` y prohíbe `escalate`/`transfer`; las sugerencias solo viven en el turno que cierra el run (un flow task no espera, G0-16).
- M8: rechazo de enlaces, argumentos no declarados, tokens existentes en `args`/textos; el motivo largo ya no lanza.
- `suite_problems`: `empty_expectation` y `count_without_outcome` (solo escenarios con `input`).
- Escenario de inyección con modelo obediente. «Otro cliente fuera de la delegación» sigue sin cubrir (el arnés usa autorización permisiva).
- Replay de un run con `suggest` y guard de inyección sobre `input.turnos`: pendientes (ver ADR §Pendientes).

## 4. Cómo ejecutar y qué prueban las pruebas nuevas
- `uv run pytest tests/composition/test_copiloto_sugerencias_eval.py`: el agente corre en el motor real; la suite pasa con todo sano (6 escenarios × 2 repeticiones) y **falla** al romper el modelo guionado (cifra inventada, tool fuera del catálogo, escalación creada por el modelo, PII repetida…), el clasificador o los datos del agente (política, motivo, evidencia, `tools_allowed`, hecho citable, resultado del fin vacío). Hay una copia sin mutar como control.
- Cada capa tiene sus pruebas: `tests/m00/test_suggestions.py`, `tests/m01/test_suggest_node.py`, `tests/m02/test_suggest.py`, `tests/m07/test_tokenized_echo.py`, `tests/m08/test_suggester.py`, `tests/registry/test_suite_suggestions.py`.
- Lo que la suite NO puede fallar por sí sola: un cambio de comportamiento del modelo real. Una señal falsa de «escalar» ante una consulta simple, por ejemplo, **no** hace fallar la suite porque la `rule` la contiene (es el valor de que escalar salga de una política); lo fija `test_a_false_escalation_signal_on_a_simple_query_is_contained_by_the_rule`.
