# M5 — DecisionModel y Understand

- Estado: borrador · Fase 4
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
class RawPrediction:      value: dict; p_raw: dict[str, float | None]; top_k: dict[str, list[tuple[str, float]]]
                          latency_ms; tokens; cost_usd
class DecisionProvider(Protocol):
    name: str
    def predict(self, spec: ProviderSpec, inputs_model_view: dict, schema: dict, locale: str) -> RawPrediction
class DecisionOutput:     value: dict; p_cal: dict[str, float | None]; p_raw; top_k
                          above_threshold: dict[str, bool]; provider_used; model_version
                          fallback_depth: int; latency_ms; tokens; cost_usd; decision_id

class DecisionService:
    def decide(self, model_ref: EntityRef, inputs_model_view: dict, locale: str, token_vault) -> tuple[DecisionOutput, EngineEvent]
class UnderstandService:
    def run(self, model_view_text: str, context: UnderstandContext, locale: str) -> tuple[UnderstandResult, list[EngineEvent]]

class UnderstandResult:   command; flow: str | None; interrupt: str | None; additional_flows: list[str]
                          slots: dict[str, Any]; above_threshold: dict[str, bool]; decision_id

# Offline (paquete agent_core.decision.calibration)
class CalibrationArtifact: run_id; split_hash; method
                           calibrators: dict[(field, provider, lang), IsotonicMap]
                           thresholds: dict[(field, value, provider, lang), float]
                           target: dict[field, {"metric": "precision" | "recall", "value": float}]
def calibrate(model_def, dev_split, providers) -> CalibrationArtifact
```

## 3. Comportamiento

### 3.1 `decide`

1. Arma la entrada con `input_view` (siempre vista `model`; un path fuera de esa vista es error de configuración).
2. Recorre `providers` en orden:
   - timeout o error → siguiente proveedor (`fallback_depth += 1`);
   - salida fuera de `output_schema` → **1** reintento con el mismo proveedor; si vuelve a fallar, siguiente.
3. Calibra cada campo calibrado con el mapa `(field, provider, lang)`. Sin mapa: `p_cal = p_raw` si `method = none`, si no `null`.
4. Umbral: `thresholds[(field, value, provider, lang)]`; **combinación ausente = 1.0**. `above_threshold[field] = p_cal is not None and p_cal >= umbral`. `p_cal = null` cuenta como bajo umbral.
5. **Tokens:** todo valor que parezca token se resuelve con `token_vault.exists(token)`; un token desconocido invalida la salida → todos los campos calibrados quedan bajo umbral.
6. Cadena agotada → `DecisionOutput` con `above_threshold` todo en `false` (la rama es `low_confidence`).
7. Emite `decision_made` con la salida completa (vista `audit`).

### 3.2 Understand

- Esquema cerrado: `command` (enum), `flow` (enum de flows de la release), `interrupt` (enum de interrupciones de la release), `additional_flows`, `slots`.
- Campos calibrados: `command`, `flow` (solo con `start_flow`), `interrupt` (solo con `interrupt`).
- Umbrales de `interrupt` se fijan por **recall** (objetivo en `target`); el resto por precisión.
- Una sola llamada a modelo por turno. El contexto incluye `recent_turns` (unidad 7) en vista `model`, el nodo actual y si hay `confirm` pendiente.
- Los slots salen como `claimed`; M4/M2 nunca los tratan como hechos.

### 3.3 Proveedores del MVP

| Proveedor | Uso en el MVP | Notas |
|---|---|---|
| `jev` | preferido si pasa la prueba de humo | adaptador HTTP; captura del request para medir fugas (M7) |
| `classifier` | respaldo y baseline | artefacto entrenado (ref + hash de datos) que produce el científico de datos; TF-IDF + regresión logística es suficiente para empezar |
| `rule` | decisiones triviales | p ∈ {0, 1} |
| `llm_structured` | **solo baseline en evaluación**, sin umbral | vía `LLMGateway`; `p_cal = null` salvo logprobs |

Cambiar de proveedor o de orden es un cambio de datos (`DecisionModelDef`), no de código.

### 3.4 Calibración offline

1. Corre cada proveedor sobre el split de desarrollo, por idioma.
2. Ajusta isotónica por `(field, provider, lang)`.
3. Elige por `(field, value, provider, lang)` el umbral mínimo que cumple el objetivo (precisión para `command`/`flow`, recall para `interrupt`).
4. Si la muestra PT no alcanza el mínimo de la unidad 6, copia la calibración ES y lo marca como limitación en el artefacto.
5. Reporta ECE, macro-F1, precisión al umbral y cobertura por idioma y proveedor.

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

`decision_made {decision_id, model@v, provider_used, fallback_depth, value (audit), p_cal, p_raw, top_k, latency_ms, tokens, cost_usd, locale}`.

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

- Resultado de la prueba de humo de JEV.
- Mínimo de muestra PT (lo fija la unidad 6).
