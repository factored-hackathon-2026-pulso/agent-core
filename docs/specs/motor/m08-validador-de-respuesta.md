# M8 — Validador de respuesta y `respond(generate)`

- Estado: borrador · rev. 2 parcial (2026-09-29: decisiones P1 y P2; el resto de los abiertos de interfaz sigue pendiente) · Fase 4
- Paquete: `agent_core.response`
- Origen: spec general §8.3, §5 (`respond`), §4.8, §10 (fallas del validador), §13.7
- ADRs: 0011 (validación numérica por locale, hechos `compute`), 0012 (idioma del texto), 0008 (validador sobre vista `model`)
- Usa: M0, M6 (`detect_language`), M7 (tokens, PII en claro) · Lo usan: M2 (`respond`), M10 (resumen del handoff, si es generado)

## 1. Propósito y límites

Decide, de forma determinista, si un texto que va a ver una persona está respaldado por hechos: citas válidas, cifras que coinciden, sin PII en claro y en el idioma correcto. Orquesta además la cadena de `respond(generate)`: generar → validar → regenerar una vez → plantilla de respaldo → escalar.

**No hace:** generar texto (unidad 5 vía `LLMGateway`), renderizar tokens (M7), guardar el transcript (M11), ni el análisis estático de reclamos (M1).

## 2. Interfaz pública

```python
class Draft:            text: str; citations: list[str]            # fact_id | page_ref
class ValidationContext:
    facts_model_view: dict[str, Any]      # por fact_id
    fact_sources: dict[str, FactSource]   # para saber si una cifra viene de compute
    allowed: set[str]                     # fact_ids/page_refs que el nodo permite citar
    pages_model_view: dict[str, PageView] # vacío hasta M12
    vault: TokenVault; locale: str; lang_cfg: LanguageDetection
    number_format: NumberFormat | None    # dato opcional; lo produce quien construye el contexto (ver 3.3)
class NumberFormat:     thousands: str; decimal: str               # p. ej. (".", ",") o (",", ".")
class Failure:          check: Literal["format", "citations", "numbers", "tokens_pii", "language"]; detail: str
class ValidationResult: ok: bool; failures: list[Failure]

def validate(draft: Draft, ctx: ValidationContext) -> ValidationResult          # función pura

class Responder:
    def template(self, template_ref, locale, facts_model_view) -> Message
    def generate(self, node_config, state, ctx) -> tuple[Message | EscalationRequest, list[RejectedDraft], list[EngineEvent]]
# EscalationRequest y RejectedDraft son tipos de M0 (los consumen M10 y M11, que no pueden importar M8)
```

## 3. Comportamiento

### 3.1 Comprobaciones (en orden; se reportan todas las que fallan)

1. **Formato:** la salida del gateway parsea como `{text, citations}`.
2. **Citas:** cada cita existe en `facts` o en páginas recuperadas en el run **y** está en `allowed` (de `generate.allowed_facts` o `knowledge_from` de M12).
3. **Cifras:**
   - Se extraen del texto números, montos, porcentajes y fechas con el parser del `number_format` del contexto (`1.234,56` o `1,234.56`); sin `number_format`, ver 3.3.
   - Cada cifra debe ser **numéricamente igual** a un valor de un hecho o página citados, con la precisión de la moneda (`Decimal`, sin tolerancia relativa).
   - Si la cifra no aparece en ningún hecho de origen `tool`/`identity`, debe venir de un hecho `compute`. Una cifra calculada sin su `compute` falla.
   - Se ignoran los dígitos dentro de tokens de M7.
   - Todo número del texto cuenta igual ("paso 2", "24 horas"): debe estar en un hecho citado. Una cifra ambigua sin `number_format` se rechaza aunque un hecho la respalde. Las fechas se comparan con valores ISO (`aaaa-mm-dd`) de los hechos; los valores de hecho numéricos son `int`, `Decimal` o cadenas `-?dígitos(.dígitos)?`.
4. **Tokens y PII:** todo token del texto existe en el `token_map`; `ViewService.find_clear_pii` no encuentra identificadores `pii_direct` en claro.
5. **Idioma:** `detect_language(text)` con los mismos candidatos que M6 debe dar el `locale` del turno. `short` o `undetermined` no rechazan.

### 3.2 Cadena de `respond(generate)`

```
draft = gateway.generate(prompt_ref@locale, inputs vista model, schema={text, citations})
si validate falla → 1 regeneración (el motivo se agrega a la entrada)
si vuelve a fallar → fallback_template_ref
si la plantilla no puede renderizarse → EscalationRequest(validation_failed)
```

- Modo degradado: salta directo a `fallback_template_ref`.
- **Uso del LLM:** M8 acumula en `LlmUsage` cada llamada a `gateway.generate` del nodo (generación y regeneración): `calls`, `tokens` y `cost_usd` de `GenerationResult`, `latency_ms` medido con `Clock.monotonic_ns()` alrededor de cada llamada, y `models`. Va en `response_emitted.llm`, también si al final se usó la plantilla. Si no se llamó al gateway, `llm = None`.
- Cada borrador rechazado y su motivo se devuelve como `RejectedDraft` para que M11 lo mande al transcript.
- Las plantillas (`respond.template_ref`) no pasan por las comprobaciones 2 y 3 (son texto fijo con variables de hechos), pero sí por la 4.

### 3.3 Formato numérico (decidido 2026-09-29, Abierto resuelto)

M8 **no** se acopla a teléfono, canal ni `principal.attrs.country`, y no contiene ninguna tabla país→formato.

- El formato llega como dato opcional `number_format` (`NumberFormat(thousands, decimal)`) dentro del `ValidationContext`. Lo produce un nodo o tool fuera de M8 (la correspondencia `es-CO`/`es-MX`/`es-AR`/`pt` de ADR 0011 es responsabilidad de quien construye el contexto).
- Con `number_format`, las cifras se leen con ese formato (`1.234,56` con punto/coma; `1,234.56` con coma/punto).
- Sin `number_format`, el parser acepta **solo lecturas inequívocas** y rechaza el resto: `1.234,56`, `1,234.56`, `1.234.567` y `12,5` se leen sin ambigüedad; `1.234` y `1,234` (un solo separador seguido de exactamente 3 dígitos) son ambiguos y se rechazan (`cifra_ambigua`).
- **No** se implementa la mejora "aceptar una cifra ambigua si alguna lectura coincide exactamente con un hecho citado": no está aprobada.

## 4. Invariantes

- `validate` es pura y determinista (entra al replay `fixture`).
- Ningún texto generado llega al usuario sin pasar `validate` o sin ser reemplazado por una plantilla.
- Nunca se aplica tolerancia numérica.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Validador rechaza | 1 regeneración → plantilla → `escalate(validation_failed)` |
| Gateway caído | directo a la plantilla de respaldo |
| Cifra ambigua | rechazo (medido como falso rechazo en eval) |

## 6. Eventos que emite

`response_emitted {node_id, kind: template|generated, validator: {ok, failures, regenerations}, fallback_used, claims (de M1), transcript_fp, llm}`. La huella la calcula M11 y la agrega antes de encadenar.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M8-01 | `1.234,56` con `number_format` punto/coma y `1,234.56` con coma/punto pasan contra el mismo hecho; sin `number_format`, `1.234` se rechaza | 7 |
| T-M8-02 | Conversión con hecho `compute` citado pasa | 7 |
| T-M8-03 | Cifra calculada sin hecho `compute` se rechaza | 7 |
| T-M8-04 | Respuesta en un idioma distinto del `locale` se rechaza; una corta no | 7, 8 |
| T-M8-05 | Cita a un hecho no permitido por el nodo se rechaza | — |
| T-M8-06 | Token inexistente o documento en claro se rechaza | 6 |
| T-M8-07 | Dos rechazos → plantilla; plantilla imposible → `validation_failed` | — |
| T-M8-08 | Modo degradado no llama al gateway | — |
| T-M8-09 | Los borradores rechazados salen en `RejectedDraft` con motivo | — |
| T-M8-10 | Dígitos dentro de tokens no cuentan como cifras | — |
| T-M8-11 | Generación rechazada + regeneración rechazada + plantilla: `llm.calls = 2` con tokens y costo sumados; modo degradado: `llm = None` | — |

## 8. Evaluación

- **Principal:** afirmaciones sin fuente que se escapan (auditoría muestral de respuestas aprobadas).
- **Secundarias:** tasa de regeneración, tasa de uso de plantilla de respaldo, falsos rechazos (sobre un conjunto etiquetado de respuestas correctas), por idioma y por comprobación.
- **Costo de generar:** tokens, costo y latencia p50/p95 por respuesta (`response_emitted.llm`), por `prompt@v` y modelo; costo de las respuestas que terminaron en plantilla.

## 9. Puntos de iteración

- Comprobación nueva = función `check_x(draft, ctx)` en la lista ordenada + ID de `Failure`. Así entran las de conocimiento de M12.
- Formato numérico: dato `number_format` del contexto (sin tabla en M8).
- Número de regeneraciones: parámetro (1 en la spec).

## 10. Definición de terminado

- `validate` con las cinco comprobaciones y la cadena de `generate` con T-M8-01…11 en verde.
- Conjunto de 30+ respuestas etiquetadas (ES y PT) para medir falsos rechazos.

## 11. Abiertos

- ~~**Formato numérico por país**~~ **Resuelto 2026-09-29:** `number_format` es un dato opcional del `ValidationContext` que produce un nodo/tool fuera de M8; sin él solo se aceptan lecturas inequívocas y `1.234` se rechaza (3.3). Queda sin aprobar, y sin implementar, aceptar una cifra ambigua cuando alguna lectura coincida exactamente con un hecho citado.
- ~~**Números que no son cifras de negocio** ("paso 2", "24 horas")~~ **Resuelto 2026-09-29:** cuentan igual que cualquier cifra y deben venir de un hecho o de la plantilla. No se añade `allowed_literals` (tocaría M0). El falso rechazo se mide con el conjunto etiquetado (10) antes de considerar cualquier relajación.
- **Pendientes de decisión (plan de implementación, Task 1, P3–P7):** campos de `ValidationContext` para PII en claro e idioma (`find_clear_pii`, umbrales, idiomas soportados); firma de `Responder.generate` frente a `ResponderPort` de M2; semántica de la comprobación de idioma frente a `LangDecision` con `UNCALIBRATED`; `allowed_facts` (`facts.<nombre>`) frente a citas por `fact_id`; renderizado de plantillas dentro de M8.
