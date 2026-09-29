# M6 — Guardas de entrada

- Estado: borrador · Fase 4
- Paquete: `agent_core.guards`
- Origen: spec general §4.3 (idioma, tamaño, injection), §4.5.6 (modo degradado), §8.3.5 (idioma en el validador), §15 (riesgo de mensajes cortos)
- ADRs: 0012 (detector de idioma), 0005 (JEV fuera del idioma), 0008 (`untrusted_text`)
- Usa: M0 · Lo usan: M4 (cada turno), M8 (idioma del texto generado)

## 1. Propósito y límites

Guardas deterministas y locales que corren **antes** de Understand: decidir el `locale` del turno, rechazar mensajes demasiado grandes y marcar posibles inyecciones.

**No hace:** llamar modelos externos, cambiar el flow (M4 decide qué hacer con la salida), enmascarar PII (M7; M6 recibe el texto ya en vista `model`).

## 2. Interfaz pública

```python
class LangDecision:  decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
                     locale: str; locale_prior: str | None; letters: int
                     top2: list[tuple[str, float]]; detector: str        # "lingua@<versión>"
def detect_language(text_model_view: str, cfg: LanguageDetection, thresholds: LangThresholds,
                    supported: list[str], prior: str | None) -> LangDecision      # función pura

class InjectionResult: flagged: bool; signals: list[str]; ruleset: str          # "injection-rules@v"
def scan_injection(text: str, ruleset: InjectionRuleset) -> InjectionResult

class GuardResult:   lang: LangDecision; size_ok: bool; injection: InjectionResult
class GuardService:
    def run(self, text_model_view, state, agent, release, request_lang, first_turn: bool) -> tuple[GuardResult, list[EngineEvent]]
```

## 3. Comportamiento

### 3.1 Idioma (reglas de §4.3, en orden)

1. **Limpieza:** se quitan dígitos, montos, URLs, emojis, tokens de PII (formato de M7) y etiquetas `<datos_no_confiables>`; se cuentan las letras restantes.
2. **Primer turno:** `prior = request_lang` si está en `supported_locales`, si no `default_locale`.
3. **Corto:** `letters < min_letters` → `short`, conserva `prior`.
4. **Candidatos cerrados:** lingua restringido a `supported + unsupported` de la release. Si la distancia entre los dos primeros es menor a la mínima → `undetermined`, conserva.
5. **Histéresis:** primero soportado y distinto de `prior` → `switched` solo si `score ≥ switch_threshold`; si no, `kept`.
6. **No soportado:** primero no soportado, `score ≥ unsupported_threshold` y `letters ≥ min_letters_unsupported` → `unsupported`; si no, `kept`.
7. **Umbrales:** salen de `thresholds_from` (corrida de la unidad 6). Si no hay corrida, ambos valen 1.0: nunca `switched` ni `unsupported`.

El detector se construye una vez por proceso por versión de configuración (`lru_cache`), porque cargar lingua es caro.

### 3.2 Tamaño

`len(text) > max_input_chars` (propuesta: 4.000, configurable en la release) → `size_ok = false`; M4 responde con una plantilla y no procesa el turno.

### 3.3 Injection (propuesta para el MVP)

La spec dice que el detector "marca y cuenta" sin definir el método. Propuesta:

- Conjunto de reglas **versionado en la release** (`injection-rules@v`): expresiones regulares y listas de frases en ES/PT/EN ("ignora las instrucciones", "eres ahora", "system prompt", delimitadores falsos como `</datos_no_confiables>`, etc.).
- `flagged = true` si alguna regla coincide. `signals` lista los ids de las reglas.
- Se aplica al texto del usuario **y** a los campos `untrusted_text` que M7 envuelve antes de mandarlos a un modelo.
- Reemplazable por un clasificador detrás de la misma interfaz, si la suite adversarial lo justifica.

M4 activa el modo degradado con `flagged = true`.

## 4. Invariantes

- `detect_language` es pura: mismo texto, misma configuración y mismos umbrales → misma decisión.
- Sin umbrales calibrados, el `locale` nunca cambia por detección.
- Ninguna guarda llama a la red.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| lingua no carga | error de arranque del servicio (no se degrada en silencio) |
| Texto vacío tras la limpieza | `short` |
| Mensaje enorme | `size_ok = false` |

## 6. Eventos que emite

- Salida de guardas dentro de `turn_started`: `{detector@v, letters, top2, decision, locale_prior, locale, injection: {flagged, signals, ruleset}, size_ok}`.
- `injection_flagged {signals, ruleset}` cuando aplica.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M6-01 | Turno en PT con `prior = es` y umbrales calibrados → `switched` a PT | 8 |
| T-M6-02 | "sí", "ok", "não", o solo un monto → `short`, conserva | 8 |
| T-M6-03 | Portuñol sin distancia suficiente → `undetermined`, conserva | 8 |
| T-M6-04 | Una palabra suelta en inglés no da `unsupported` | 8 |
| T-M6-05 | Frase larga en inglés sobre umbral → `unsupported` | 8 |
| T-M6-06 | Sin corrida de calibración nunca hay `switched` ni `unsupported` | 8 |
| T-M6-07 | La limpieza ignora tokens de PII y montos | — |
| T-M6-08 | Primer turno: `lang` soportado se usa; no soportado cae a `default_locale` | — |
| T-M6-09 | Reglas de injection marcan los casos de la suite adversarial directa y en `untrusted_text` | 13 |
| T-M6-10 | Texto sobre el máximo → `size_ok = false` | — |

## 8. Evaluación

- **Idioma:** precisión por largo del mensaje (1–3 palabras, oraciones), tasa de `undetermined`, tasa de `switched` erróneos, latencia p50/p95. Se corre en la prueba de humo junto con JEV (ADR 0005).
- **Injection:** precisión y recall sobre la suite adversarial (unidad 6); tasa de turnos degradados en tráfico normal (falsos positivos).

## 9. Puntos de iteración

- Umbrales: nueva corrida de calibración, sin código.
- Candidatos no soportados: dato de la release.
- Detector de injection: nuevas reglas = nueva versión del ruleset; clasificador = implementación nueva de `scan_injection`.
- JEV como segunda opinión solo en `undetermined`, si la prueba de humo lo justifica.

## 10. Definición de terminado

- `detect_language` y `scan_injection` puras con T-M6-01…10 en verde.
- Ruleset de injection inicial en `agent-registry` con sus casos.
- Latencia p95 de las guardas < 20 ms en nuestro entorno (propuesta).

## 11. Abiertos

- Método del detector de injection (propuesta en 3.3, pendiente de aprobación).
- Distancia mínima entre los dos primeros candidatos: ¿valor fijo o calibrado? Propuesta: calibrado en la misma corrida.
