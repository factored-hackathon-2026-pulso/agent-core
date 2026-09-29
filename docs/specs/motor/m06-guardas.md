# M6 — Guardas de entrada

- Estado: rev. 2 (2026-09-29) · Fase 4
- Paquete: `agent_core.guards`
- Origen: spec general §4.3 (idioma, tamaño, injection), §4.5.6 (modo degradado), §8.3.5 (idioma en el validador), §15 (riesgo de mensajes cortos)
- ADRs: 0012 (detector de idioma), 0005 (JEV fuera del idioma), 0008 (`untrusted_text`)
- Usa: M0 · Lo usan: M4 (cada turno), M8 (idioma del texto generado)

## 1. Propósito y límites

Guardas deterministas y locales que corren **antes** de Understand: decidir el `locale` del turno, rechazar mensajes demasiado grandes y marcar posibles inyecciones.

**No hace:** llamar modelos externos, cambiar el flow (M4 decide qué hacer con la salida), enmascarar PII (M7; M6 recibe el texto ya en vista `model`).

## 2. Interfaz pública

```python
class LangThresholds:  switch_threshold: float = 1.0; unsupported_threshold: float = 1.0
                       min_distance: float = 0.10                       # corrida de calibración (unidad 6); 1.0 = desactivado
class LangDecision:  decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
                     locale: str; locale_prior: str | None; letters: int
                     top2: list[tuple[str, float]]; detector: str        # "lingua@<versión>"
def detect_language(text_model_view: str, cfg: LanguageDetection, thresholds: LangThresholds,
                    supported: list[str], prior: str | None) -> LangDecision      # función pura

class InjectionResult: flagged: bool; signals: list[str]; ruleset: str          # "<id>@<versión>" o "none"
def scan_injection(text: str, ruleset: InjectionRuleset) -> InjectionResult

class GuardResult:   lang: LangDecision; size_ok: bool; injection: InjectionResult
                     def to_output(self) -> GuardsOutput                          # para TurnStartedPayload.guards
class GuardService:
    def __init__(self, registry: RegistryPort, clock: Clock, ids: IdSource,
                 calibrations: Mapping[str, LangThresholds] | None = None)
    def run(self, text_model_view, state, agent, release, request_lang, first_turn: bool,
            *, turn_id: str | None = None) -> tuple[GuardResult, list[EngineEvent]]
    def scan_untrusted(self, text, state, release, *, turn_id: str | None = None) -> tuple[InjectionResult, list[EngineEvent]]
    def validate_release(self, release: Release, agent: Agent) -> None            # error de arranque (GuardsConfigError)
class GuardsConfigError(Exception)
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

**Aclaraciones (rev. 2):**

- Conjunto de candidatos de lingua: `supported ∪ cfg.unsupported`; `cfg.candidates` debe contener a `supported` (si no, `GuardsConfigError`).
- Un umbral solo está activo si es `< 1.0`; con 1.0 la regla nunca se cumple, aunque `lingua` devuelva 1.0.
- `unsupported` conserva `locale = prior`; M4 responde con la plantilla `unsupported_language` en el idioma por defecto del agente.
- `min_distance` es un campo calibrado de `LangThresholds` (sin corrida vale 0.10).
- `thresholds_from` se resuelve con el mapeo `calibrations` que recibe `GuardService`; ausente en el mapeo equivale a "sin corrida".
- Con `short` no se ejecuta `lingua`: `top2 = []`.
- Limpieza (paso 1): NFC; se quitan etiquetas `<datos_no_confiables …>`/`</datos_no_confiables>`, tokens `⟦tag:n⟧`, URLs, emails, montos (`$`, `€`, `£` con cifras) y códigos de moneda (`COP`, `MXN`, `ARS`, `USD`, `EUR`, `BRL`); se descartan dígitos, símbolos y emojis y quedan letras, marcas, puntuación y espacios.

### 3.2 Tamaño

`len(text) > max_input_chars` (propuesta: 4.000, configurable en la release) → `size_ok = false`; M4 responde con una plantilla y no procesa el turno. Si `size_ok = false` no se evalúan idioma ni injection (`lang = short` con `letters` 0, injection sin marcar).

### 3.3 Injection (propuesta para el MVP)

La spec dice que el detector "marca y cuenta" sin definir el método. Propuesta:

- Conjunto de reglas **versionado en la release** (`injection-rules@v`): expresiones regulares y listas de frases en ES/PT/EN ("ignora las instrucciones", "eres ahora", "system prompt", delimitadores falsos como `</datos_no_confiables>`, etc.).
- `flagged = true` si alguna regla coincide. `signals` lista los ids de las reglas.
- Se aplica al texto del usuario **y** a los campos `untrusted_text` que M7 envuelve antes de mandarlos a un modelo.
- Reemplazable por un clasificador detrás de la misma interfaz, si la suite adversarial lo justifica.

**Normalización (rev. 2):** antes de comparar, el texto pasa por `html.unescape`, NFKC, eliminación de caracteres de formato (categoría `Cf`, p. ej. `\u200b`), minúsculas y colapso de espacios; las frases del ruleset se normalizan igual y las regex se compilan con `re.IGNORECASE`. `Release.injection_ruleset = None` → no se escanea (`ruleset = "none"`). El escaneo de campos `untrusted_text` lo hace `GuardService.scan_untrusted` con `scope = "untrusted_field"` en el evento; `run` usa `scope = "user_text"`.

**Envoltura de `scan_untrusted` (rev. 2):** antes de escanear, `scan_untrusted` quita solo la envoltura exterior exacta de M7, `<datos_no_confiables fuente="...">...</datos_no_confiables>` (la envoltura es del propio motor; si se escaneara, `fake-delimiter` marcaría todo campo envuelto). Un delimitador falso dentro del contenido sigue marcándose. Cualquier otra forma (sin la envoltura exacta, con texto antes o después, atributos distintos) se escanea completa, y entonces la propia etiqueta dispara `fake-delimiter`. Consecuencias:

- `fuente` debe ser una ruta de campo propia del motor, nunca derivada de datos del usuario o de terceros, porque no se escanea.
- `scan_untrusted` no limita el tamaño del texto (costo lineal, reglas acotadas); el tope `max_input_chars` solo aplica a `run`.

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

- La salida de guardas viaja en `turn_started` (`GuardResult.to_output()`); **lo emite M4** (índice §6). M6 solo construye `injection_flagged {signals, ruleset, scope}`.

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

- [x] `detect_language` y `scan_injection` puras con T-M6-01…10 en verde.
- [x] Ruleset de injection inicial y sus casos como fixture (`testing/injection_fixtures.py`); publicación en `agent-registry` pendiente (repo externo).
- [x] Latencia p95 de las guardas < 20 ms en nuestro entorno (medido: 0,33 ms, p50 0,20 ms, 400 corridas con lingua caliente sobre ES/PT/EN/corto; `AGENT_CORE_PERF=1 uv run pytest tests/m06/test_latency.py -s`). Nota: en Windows `time.monotonic_ns` tiene resolución de ~15,6 ms, así que la prueba con `SystemClock` imprime 0,00 ms; la cifra de arriba se midió con `time.perf_counter_ns` fuera del repo. La prueba solo garantiza que no se superen 20 ms.

## 11. Abiertos

Ninguno de M6. Resueltos en rev. 2 (2026-09-29, decisiones confirmadas por el usuario): método de injection (§3.3), distancia mínima calibrada (§3.1). Dependencias de integración: quién invoca `scan_untrusted` (M2/M5), quién construye `calibrations` (M9/CLI con las corridas de la unidad 6) y la publicación del ruleset en `agent-registry`.

## Cambios

- rev. 2 (2026-09-29): `LangThresholds`; `GuardService(registry, clock, ids, calibrations)`; `turn_id` en `run`; `scan_untrusted`; `validate_release`; `GuardResult.to_output`; normalización de injection; umbral 1.0 = desactivado; `size_ok = false` omite idioma e injection.
- rev. 2 (2026-09-29): §3.3 documenta la envoltura exacta que `scan_untrusted` quita antes de escanear, que `fuente` debe ser un campo propio del motor (no se escanea) y que `scan_untrusted` no tiene tope de tamaño.
