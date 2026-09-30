# M5 — DecisionModel y Understand

- Estado: rev. 2 (2026-09-29) · Fase 4
- Paquete: `agent_core.decision`
- Origen: spec general §4.4, §7, §12 (métricas de `DecisionModel`), §15 (riesgo JEV)
- ADRs: 0005 (contrato, JEV como proveedor, campos calibrados, tabla de umbrales), 0012 (calibración por idioma, datos PT)
- Usa: M0, M7 (vista `model`, resolución de tokens) · Lo usan: M2 (`decide`), M4 (Understand)

## 1. Propósito y límites

Toda elección tipada con probabilidad pasa por aquí: recibe una entrada en vista `model`, consulta una cadena de proveedores, calibra, aplica la tabla de umbrales y devuelve un valor con la marca de si cada campo calibrado supera su umbral. Understand es un `DecisionModel` más.

Incluye también el **formato del artefacto de calibración** y la herramienta offline que lo produce, porque el runtime depende de él.

**No hace:** decidir qué hacer con un campo bajo umbral (M2 lo convierte en `low_confidence`, M4 en `clarify`/`unclear`), detectar idioma (M6), llamar al LLM directamente (unidad 5 vía `LLMGateway`).

## 2. Interfaz pública

```python
# DecisionModelDef, ProviderSpec y CalibrationRef son tipos de M0 (§2.4): los lee M1 (G0-11) y el registro.
@dataclass(frozen=True)
class RawPrediction:      value: dict; p_raw: dict[str, float | None]; top_k: dict[str, list[tuple[str, float]]]
                          latency_ms; tokens; cost_usd: Decimal; model_version: str = "unknown"
class DecisionProvider(Protocol):
    name: str
    def predict(self, spec: ProviderSpec, inputs_model_view: dict, schema: dict, locale: Locale) -> RawPrediction
class ProviderTimeout(Exception); class DecisionConfigError(Exception)
class ProviderError(Exception):      # uso parcial de una llamada fallida (también cuesta)
    tokens: int; cost_usd: Decimal
@dataclass(frozen=True)
class DecisionOutput:     value: dict; p_cal: dict[str, float | None]; p_raw; top_k
                          above_threshold: dict[str, bool]; provider_used; model_version
                          fallback_depth: int; latency_ms; tokens; cost_usd: Decimal; decision_id
                          model_calls: int                    # llamadas `predict` (reintentos y fallbacks incluidos)
                          def as_decision(self) -> Decision   # el `Decision` de M0 (sin `above_threshold`)
@dataclass(frozen=True)
class EventScope:         run_id; release; turn_id: str | None = None; session_id: str | None = None

class DecisionService:
    def __init__(self, registry, providers: Mapping[str, DecisionProvider], calibrations: CalibrationSource,
                 clock, ids)
    def decide(self, model_ref: EntityRef, inputs_model_view: dict, locale: Locale, token_vault,
               *, scope: EventScope) -> tuple[DecisionOutput, DecisionMade]
    def decide_output(self, model_ref, inputs_model_view, locale, token_vault) -> DecisionOutput  # sin evento

@dataclass(frozen=True)
class UnderstandContext:  model_ref: EntityRef; flows: list[str]; interrupts: list[str]
                          current_node: str | None; confirm_pending: bool
                          recent_turns: list[str]              # `text_model` (vista model); M4 arma n fijo
                          token_vault: TokenVault; scope: EventScope
@dataclass(frozen=True)
class UnderstandResult:   command: Command; flow: str | None; interrupt: str | None; additional_flows: list[str]
                          slots: dict            # claimed: sin validar
                          above_threshold: dict[str, bool]; decision_id: str
                          p_cal: dict[str, float | None]       # solo los campos con marca
class UnderstandService:
    def __init__(self, decisions: DecisionService)
    def run(self, model_view_text: str, context: UnderstandContext, locale: Locale
            ) -> tuple[UnderstandResult, list[DecisionMade]]

# Proveedores (agent_core.decision): RuleProvider(), ClassifierProvider(loader: ArtifactLoader),
# LlmStructuredProvider(gateway: LLMGateway), JevProvider(transport: JevTransport, capture=None),
# HttpJevTransport(api_key: Callable[[], str], clock: Clock, *, base_url, max_retries, backoff_base_ms, sleep),
# JevTransportError(status: int | None)

# Offline (paquete agent_core.decision.calibration)
class CalibrationArtifact: run_id; split_hash; method
                           calibrators: dict[(field, provider, lang), IsotonicMap]
                           thresholds: dict[(field, value, provider, lang), float]
                           target: dict[field, Target{metric: "precision" | "recall", value}]
                           limitations: list[str]; metrics: dict
class CalibrationSource(Protocol): def get(self, run_id: str) -> CalibrationArtifact | None
def calibrate(model_def, dev_split: Sequence[DevExample], providers, *, targets: Mapping[str, Target],
              min_samples: Mapping[str, int], min_support: int) -> CalibrationArtifact
```

`DecisionPort.decide` de M2 (`decide(model, inputs, locale) -> DecisionResult`) no puede vivir aquí (M5 no importa `interpreter`): el adaptador `DecisionOutput → DecisionResult` (con `vault = ctx.vault` y `model_calls`) lo escribe M2 o la composición de M4 (P1, fuera de M5).

## 3. Comportamiento

### 3.1 `decide`

1. Arma la entrada con `input_view` (siempre vista `model`; un path fuera de esa vista es error de configuración). Nota de implementación: `decide` no arma la entrada (la recibe ya proyectada por M2/M4 en vista `model`); solo verifica que las claves **de primer nivel** de `inputs_model_view` estén en `input_view` (si este no es vacío) y no valida rutas anidadas.
2. Recorre `providers` en orden (un proveedor sin adaptador registrado es `DecisionConfigError`, revisado antes de llamar a ninguno):
   - timeout o error → siguiente proveedor (`fallback_depth += 1`);
   - salida fuera de `output_schema` → **1** reintento con el mismo proveedor; si vuelve a fallar, siguiente.
   `tokens` y `cost_usd` acumulan todas las llamadas (también las fallidas); `latency_ms` es la duración total medida con `Clock.monotonic_ns`.
3. Calibra cada campo calibrado con el mapa `(field, provider, lang)` del artefacto de `calibration.run`. Sin mapa: `p_cal = p_raw` si `method = none`, si no `null`. `p_raw = null` da `p_cal = null`.
4. Umbral (artefacto de `thresholds_from`): `thresholds[(field, value, provider, lang)]`; **combinación ausente = 1.0 y nunca pasa**, ni con `p_cal = 1.0` (coherente con §5: sin calibración, siempre `low_confidence`). `above_threshold[field] = p_cal is not None and p_cal >= umbral`. `p_cal = null` cuenta como bajo umbral; un campo calibrado ausente de `value` también.
5. **Tokens:** todo string de `value` (valores y claves, en cualquier profundidad) que coincida con `TOKEN_PATTERN` de M7 se verifica con `token_vault.exists(token)`; un token desconocido invalida la salida → todos los campos calibrados quedan bajo umbral (`p_cal` se conserva para diagnóstico y `value` no se toca).
6. Cadena agotada → `DecisionOutput` con `value = {}`, `p_cal`/`p_raw` en `null`, `above_threshold` todo en `false`, `provider_used = model_version = "none"` y `fallback_depth = len(providers)` (la rama es `low_confidence`).
7. Emite `decision_made` con la salida completa (vista `audit`).

### 3.2 Understand

- Esquema cerrado, armado por release (no muta el `DecisionModelDef` del registro): `command` (enum de `Command`), `flow` (enum de flows de la release), `interrupt` (enum de interrupciones de la release), `additional_flows` (lista del enum de flows), `slots` (único objeto libre).
- Entrada al proveedor (vista `model`): `{text, recent_turns, current_node, confirm_pending}`.
- Campos calibrados: `command`, `flow` (solo con `start_flow`), `interrupt` (solo con `interrupt`). Los demás `above_threshold` se omiten (no `false`); `additional_flows` y `slots` nunca llevan umbral.
- Cadena agotada: `command = clarify` con `above_threshold["command"] = false` (valor neutro; M4 decide qué hacer).
- Umbrales de `interrupt` se fijan por **recall** (objetivo en `target`); el resto por precisión.
- Una llamada a JEV por turno para los campos calibrados; los slots se extraen con una segunda llamada a `llm_structured` solo cuando `command = start_flow` (ADR 0005, enmienda 2026-09-29). **Pendiente de implementar:** `UnderstandService.run` hoy hace una sola llamada. El contexto incluye `recent_turns` (unidad 7) en vista `model`, el nodo actual y si hay `confirm` pendiente.
- Los slots salen como `claimed`; M4/M2 nunca los tratan como hechos.

### 3.3 Proveedores del MVP

| Proveedor | Uso en el MVP | Notas |
|---|---|---|
| `jev` | preferido si pasa la prueba de humo | adaptador sobre transporte inyectable (`JevTransport`) con el contrato real de `POST /v1/systemone` (§3.3.1); `HttpJevTransport` (stdlib) recibe la key como `Callable[[], str]` que inyecta la composición (única que lee `JEV_API_KEY`); el adaptador nunca la ve; captura del request para medir fugas (M7) |
| `classifier` | respaldo y baseline | artefacto JSON `tfidf-logreg-v1` (ref + hash de datos) que exporta el científico de datos; TF-IDF + regresión logística evaluados en Python puro (sin `scikit-learn`); ver §3.5 |
| `rule` | decisiones triviales | p ∈ {0, 1}; `config` en §3.5 |
| `llm_structured` | **solo baseline en evaluación**, sin umbral | vía `LLMGateway`; `p_cal = null` salvo logprobs |

Cambiar de proveedor o de orden es un cambio de datos (`DecisionModelDef`), no de código.

#### 3.3.1 JEV: esquema → preguntas tipadas

Request `{"state": {"locale", "input": <vista model>}, "model", "questions"}`; respuesta `{"model", "answers", "usage"}` (https://docs.typesafe.ai/api). JEV no extrae valores libres: solo responde preguntas tipadas.

| Propiedad del `output_schema` | Pregunta | `RawPrediction` |
|---|---|---|
| `string` + `enum` (1–255 strings distintos) | `choice`; `criteria` de `config.questions.<campo>.criteria` (debe cubrir el enum) o `null` por opción | `value` = opción; `p_raw` = `probabilities[opción]`; `top_k` = 5 mayores, orden `(-p, etiqueta)` |
| `boolean` | `noul` | `value = noul ≥ 0.5`; `p_raw` = probabilidad del valor elegido |
| escala declarada (`config.questions.<campo> = {type: "score", levels: [{value, description}]}`, 2–10 niveles) | `score` | `value` = nivel del argmax de `probabilities` (no el `score` ponderado); `p_raw` = su probabilidad |
| `array` de enum, con `config.multi_flow = true` | un `noul` por opción (`<campo>__<opción>`) | lista de opciones con `noul ≥ 0.5`; `p_raw = None` (sin umbral); por defecto se omite |
| `object` libre (`slots`) u otros | ninguna | se omite del `value` |

- El `confidence` de JEV se descarta: no es `p_cal` ni `p_raw`; la calibración de M5 va encima de `p_raw`.
- `config`: `model` (alias o ID; `model_version` = `"jev:" + model devuelto`), `timeout_ms` (obligatorio), `questions`, `multi_flow`, `input_usd_per_mtok` (coste = tokens de entrada; la salida no se cobra).
- `instructions` sale de `config.questions.<campo>.instructions`, o de `description`/`title` del esquema, o de un texto por defecto. Sin ningún campo preguntable es `DecisionConfigError`.
- `latency_ms = 0` (lo mide `decide`); `tokens = input + output`. Una respuesta sin `usage`, con tipo distinto al pedido, opción fuera del enum o probabilidad fuera de [0, 1] es `ProviderError` (con el uso parcial si se pudo leer).
- `HttpJevTransport`: `timeout_ms` es el presupuesto total con reintentos; backoff exponencial en 429/529 (respeta `Retry-After`); sin reintento en 401/422 ni otros errores; no sigue redirects; `https` obligatorio salvo `localhost`; los errores llevan solo el código HTTP.
- Límites de `jev-1.13.0` (`/models`): 40 req/s, 100K tokens/s, contexto de 64k.

### 3.4 Calibración offline

1. Corre cada proveedor sobre el split de desarrollo, por idioma.
2. Ajusta isotónica por `(field, provider, lang)`.
3. Elige por `(field, value, provider, lang)` el umbral según el objetivo: **precisión** (`command`/`flow`): el mínimo umbral con precisión ≥ objetivo; **recall** (`interrupt`): el **máximo** umbral con recall ≥ objetivo (con recall el "mínimo que cumple" sería trivial). Sin soporte (`< min_support`) o sin umbral que cumpla → la combinación se omite (queda en 1.0).
4. Si la muestra de un idioma no alcanza `min_samples[lang]` (sin valor por defecto: lo fija la unidad 6), copia la calibración del idioma base `es` y añade `"<lang>: calibración copiada de es (muestra < mínimo)"` a `limitations`. Sin ejemplos `es` es error.
5. Reporta ECE (10 bins), macro-F1, precisión al umbral y cobertura (y recall al umbral si el objetivo es recall) por idioma y proveedor, en `CalibrationArtifact.metrics` (valores como string con 6 decimales; idioma con muestra sintética marcado `synthetic`).
6. `calibrate` es función pura del contenido: `split_hash = sha256(JCS(ejemplos ordenados por id))` y `run_id = "cal-" + sha256(JCS({modelo, split_hash, método, proveedores, targets, min_samples, min_support}))[:16]`, sin reloj ni `IdSource`; un proveedor que falla o responde fuera de esquema cuenta como ejemplo sin predicción.

### 3.5 Formatos y comando

- **`CalibrationSource.get(run_id)`** (P6): protocolo interno de M5 (no es puerto de M0) con `InMemoryCalibrationSource` y `DirectoryCalibrationSource` (`<run_id>.json`). `calibration.run` da los calibradores y `thresholds_from` la tabla de umbrales; sin `thresholds_from` todo queda bajo umbral.
- **`rule.config`** (P7): `{"cases": [{"when": {"path": "<clave de la entrada>", "equals": <json>}, "value": {...}}], "default": {...}}`. Primer caso que coincide → `p_raw = 1.0`; sin caso → `default` con `p_raw = 0.0`; sin `default`, `ProviderError`. Igualdad estricta (`true` no es `1`).
- **`classifier` `tfidf-logreg-v1`** (P4): `{format, data_hash, vocab, idf, classes, coef, intercept}`; tokens `\w+` en NFKC minúsculas, `x = conteo * idf` normalizado L2, softmax estable; `top_k` ordenado por `(-p, valor)`. El artefacto entra por un `ArtifactLoader` inyectado. Toma `inputs["text"]` e ignora otras claves.
- **Informe** (P8): `uv run python -m agent_core.decision report --artifact <ruta.json> [--events <ruta.jsonl>] [--format md|json] [--out <ruta>]` (no toca `agent_core/cli.py`); artefacto ilegible → código 2. Con `--events` agrega latencia p50/p95 (nearest-rank), costo total y tasa de respaldo por idioma y proveedor.

## 4. Invariantes

- Solo los campos de `calibrated_fields` tienen `above_threshold`.
- Ningún umbral se edita a mano: todos salen de un `CalibrationArtifact` con `split_hash`.
- La entrada a cualquier proveedor externo está en vista `model`.
- Nunca se registra razonamiento del modelo, solo la salida tipada.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Proveedor caído o lento | siguiente de la cadena |
| Salida fuera de esquema | 1 reintento, luego siguiente |
| Token desconocido | salida inválida → bajo umbral |
| Cadena agotada | todo bajo umbral (`low_confidence`) |
| Falta la calibración | umbral 1.0 en todo → siempre `low_confidence`/`clarify` (seguro, pero inútil: CI de `agent-registry` debe exigir `thresholds_from`) |

## 6. Eventos que emite

`decision_made {decision_id, model@v, provider_used, model_version, fallback_depth, value (audit), p_cal, p_raw, top_k, above_threshold, latency_ms, tokens, cost_usd, locale}`; `latency_ms` es campo de medición (`MEASURED_FIELDS`). `value` lleva tokens, nunca `full`; no se registra razonamiento del modelo.

## 7. Pruebas

Con `ScriptedProvider` (salidas y latencias guionadas) y artefactos de calibración pequeños en fixtures.

| ID | Caso | §13 |
|---|---|---|
| T-M5-01 | Combinación ausente en la tabla → bajo umbral | 11 |
| T-M5-02 | Timeout del primero → usa el segundo, `fallback_depth = 1` | — |
| T-M5-03 | Fuera de esquema: 1 reintento y luego siguiente | — |
| T-M5-04 | Token desconocido invalida la salida | — |
| T-M5-05 | `p_cal = null` cuenta como bajo umbral | — |
| T-M5-06 | Umbral por idioma: mismo `p_cal` pasa en ES y no en PT si la tabla lo dice | — |
| T-M5-07 | Understand: `slots` quedan `claimed`, `additional_flows` sin umbral | — |
| T-M5-08 | `decision_made` trae todos los campos | — |
| T-M5-09 | `calibrate` reproduce el mismo artefacto con el mismo split (determinista) | — |
| T-M5-10 | Ningún request capturado hacia `jev` contiene `pii_direct` en claro | 6 |

## 8. Evaluación

- **Principal:** precisión al umbral con cobertura, por campo calibrado; para interrupciones, recall.
- **Secundarias:** ECE, macro-F1, latencia p50/p95, costo, tasa de respaldo; todo por idioma (PT marcado sintético) y proveedor.
- **Baseline del reto:** mismo `DecisionModelDef` corrido con `classifier`, `jev` y `llm_structured`.
- **Prueba de humo (bloqueante antes del miércoles 30/09):** 50 casos ES + 50 PT sobre JEV: precisión, latencia desde nuestro entorno, cobertura de idioma. La parte de idioma la corre M6 en la misma prueba.

## 9. Puntos de iteración

- Proveedor nuevo = adaptador que cumple `DecisionProvider` + fila en la cadena.
- Método de calibración (`platt`, `temperature`) = implementación nueva detrás de `calibrators`.
- La auto-mejora puede proponer cambios de proveedor o de umbral objetivo; nunca editar umbrales.

## 10. Definición de terminado

- `decide` y Understand con `classifier` + `rule`; JEV conectado si pasa la prueba de humo.
- Artefacto de calibración real para Understand en ES (y PT si llega la muestra).
- T-M5-01…10 en verde; reporte de métricas generado por un comando.

## 11. Abiertos

- **P0b (contrato real de JEV): cerrado el 2026-09-29.** Adaptador y `HttpJevTransport` implementados contra la doc oficial (§3.3.1); informe en `docs/informes/2026-09-29-m5-jev-humo.md`.
- **P0 (prueba de humo de JEV): ejecutada el 2026-09-29 con `jev-1.13.0`** (50 ES + 50 PT + 10 portuñol sintéticos; informe `docs/informes/2026-09-29-m5-jev-humo.md` §4–§6). `command` 96% ES / 94% PT, `flow` 100% (n=18 por idioma); latencia desde el entorno del usuario p50 375 ms / p95 453 ms; 0 errores y 0 respuestas 429/529 en 220 llamadas; coste ≈ 0.00002 USD por llamada; ECE cruda 3.5% (ES) y 6.0% (PT), con solo 7–8 casos bajo p = 0.9. **Salvedades:** n pequeño y textos inventados; `continue` 62.5% (n=8, confundido con `clarify`, con `p_raw` < 0.5); `start_flow`↔`cancel` se solapan (pt-11 falla con p = 0.89). No sustituye la calibración con datos etiquetados reales (P9).
- **Decisión sobre JEV en Understand (2026-09-29):** `choice`/`noul` sí (`command`, `flow`, `interrupt`), con calibración de M5 por idioma y `model` fijado a `jev-1.13.0`; slots libres por `llm_structured`; `additional_flows` apagado por defecto (no probado). JEV **no** entra como segunda opinión de idioma (ADR 0005 #5: no es claramente mejor en mensajes cortos). **ADR 0005 enmendado el 2026-09-29** (una llamada a JEV por turno más una a `llm_structured` para slots solo con `start_flow`; versión `jev-1.13.0` fijada) y §3.2 actualizado. **Falta implementar la segunda llamada en `UnderstandService`** (abierto).
- Región de procesamiento y período de retención de JEV: no figuran en la doc pública ni en el DPA (pedirlos por escrito).
- Mínimo de muestra PT (lo fija la unidad 6): `calibrate` lo exige como parámetro `min_samples`, sin valor por defecto.
- Artefactos reales de Understand ES (y PT si llega la muestra) y del clasificador: los produce otro equipo (P9).
- Adaptador `DecisionOutput → DecisionResult` de M2 (P1): lo escribe M2 o la composición de M4.

## 12. Decisiones de la rev. 2

Confirmadas por el usuario el 2026-09-29: `decide(..., *, scope: EventScope)`; `UnderstandContext`/`UnderstandResult` (con `p_cal`, nombre `above_threshold`); `RawPrediction.model_version`; `ProviderError` con uso parcial; `DecisionOutput.model_calls`; regla de recall (máximo umbral); formato `tfidf-logreg-v1`; `CalibrationSource`; `rule.config`; comando del informe; `calibrate` con parámetros keyword-only sin defecto.

Decisiones de implementación que conviene revisar: (a) combinación ausente en la tabla nunca pasa, ni con `p_cal = 1.0` (§3.1.4); (b) `run_id` también hashea `min_samples` y `min_support`; (c) `ArtifactLoader` y `Target` entran a la interfaz pública; (d) cadena agotada en Understand devuelve `clarify` por debajo del umbral; (e) `fallback_depth = len(providers)` con la cadena agotada.
