# M8 — Validador de respuesta y `respond(generate)`

- Estado: borrador · rev. 3 (2026-09-30: comprobaciones 6 y 7 de M12) · rev. 2 (2026-09-29: decisiones P1–P7 aprobadas) · Fase 4
- Paquete: `agent_core.response`
- Origen: spec general §8.3, §5 (`respond`), §4.8, §10 (fallas del validador), §13.7
- ADRs: 0011 (validación numérica por locale, hechos `compute`), 0012 (idioma del texto), 0008 (validador sobre vista `model`)
- Usa: M0, M6 (`detect_language`), M7 (tokens, PII en claro), M12 (solo sus tipos de M0; M8 no importa `agent_core.knowledge`) · Lo usan: M2 (`respond`), M10 (resumen del handoff, si es generado)

## 1. Propósito y límites

Decide, de forma determinista, si un texto que va a ver una persona está respaldado por hechos: citas válidas, cifras que coinciden, sin PII en claro y en el idioma correcto. Orquesta además la cadena de `respond(generate)`: generar → validar → regenerar una vez → plantilla de respaldo → escalar.

**No hace:** generar texto (unidad 5 vía `LLMGateway`), renderizar tokens (M7), guardar el transcript (M11), ni el análisis estático de reclamos (M1).

## 2. Interfaz pública

```python
class Draft:            text: str; citations: list[str]            # fact_id | page_ref
class NumberFormat:     thousands: str; decimal: str               # p. ej. (".", ",") o (",", ".")
@dataclass(frozen=True, repr=False)   # el repr no muestra hechos, vault ni cierres
class ValidationContext:
    facts_model_view: Mapping[str, JsonValue]    # por fact_id, ya en vista model
    fact_sources: Mapping[str, FactSource]       # por fact_id
    allowed: frozenset[str]                      # fact_ids que el nodo permite citar (las páginas van en `page_refs`)
    pages_model_view: Mapping[str, JsonValue]    # `page_ref` → contenido en vista model (M12)
    vault: TokenVault
    locale: str
    lang_cfg: LanguageDetection
    lang_thresholds: LangThresholds              # M6
    supported_locales: tuple[str, ...]
    find_clear_pii: Callable[[str], list[str]]   # cierre ViewService + hechos full; devuelve rutas, nunca valores
    number_format: NumberFormat | None = None    # dato opcional producido fuera de M8 (3.3)
    page_refs: frozenset[str] = frozenset()      # M12: `ruta@snapshot#ancla` de los `save_as` de `knowledge_from`
    pages_meta: Mapping[str, PageMeta] = {}      # M12: metadatos de esas páginas
    customer_facing: bool = False                # M12: la lee un cliente (comprobación 7)
    now: datetime | None = None                  # M12: instante del `Clock` para la vigencia
class Failure:          check: Literal["format", "citations", "numbers", "tokens_pii", "language", "page_citations", "page_audience"]; detail: str
class ValidationResult: ok: bool; failures: list[Failure]

def parse_draft(output: JsonValue) -> Draft | Failure                            # comprobación 1
def validate(draft: Draft, ctx: ValidationContext) -> ValidationResult          # función pura

class ToolEntry:        ref: str; exact: str; description: str; args_schema: dict   # una tool del catálogo del nodo `suggest`
class SuggesterContext: gateway; clock; prompt: EntityRef; locale; goal; inputs; citable: Mapping[str, str]
                        tools: tuple[ToolEntry, ...]; actions: tuple[ToolEntry, ...]
                        escalation: SuggestEscalation | None; max_items: int; validation: ValidationContext
                        degraded: bool = False; max_regenerations: int = 1      # el repr no muestra datos del cliente
class SuggestOutcome:   suggestions: list[Suggestion]; failures: list[str]; regenerations: int; llm: LlmUsage | None
class Suggester:
    def generate(self, ctx: SuggesterContext) -> SuggestOutcome                 # ADR 0026, §3.4
SUGGESTIONS_SCHEMA: dict[str, JsonValue]                                        # la salida plana que se le pide al modelo

class Responder:
    def template(self, template_ref, locale, facts_model_view) -> Message
    def generate(self, node_config, state, ctx: ResponderContext) -> tuple[Message | EscalationRequest, list[RejectedDraft], list[EngineEvent]]
# EscalationRequest y RejectedDraft son tipos de M0 (los consumen M10 y M11, que no pueden importar M8)
```

- `LlmUsage` es el de M0: `calls, latency_ms, tokens_in, tokens_out, cost_usd, cost_known, models`. `cost_known = false` si alguna llamada falló con `GatewayError` sin costo.
- M8 no importa `agent_core.interpreter`: el adaptador hacia el `ResponderPort`/`GenerateResult` de M2 (que toma `model_calls`, `tokens` y `cost_usd` de `response_emitted.llm`) vive fuera de M8, en `agent_core.composition.ResponderAdapter` (implementado 2026-09-29; recibe una fábrica que arma el `ResponderContext` de cada nodo); ese adaptador toma el uso de `response_emitted.llm` o de `response_failed.llm`.
- `ResponderContext` (puertos `LLMGateway`, `Clock`, `IdSource`, `RegistryPort`, `resolve_ref` inyectado por M2, `ValidationContext` armado por quien llama, `claims`, `max_regenerations`) lo construye quien cablea; M8 no lee hora ni aleatoriedad.
- `M8` emite `response_emitted` solo desde `generate`; para `respond(template_ref)` directo lo emite M2 (D6, 2026-09-30).

## 3. Comportamiento

### 3.1 Comprobaciones (en orden; se reportan todas las que fallan)

1. **Formato:** la salida del gateway parsea como `{text, citations}`.
2. **Citas a hechos:** cada cita existe en `facts` **y** está en `allowed`. Las citas y `allowed` van por `fact_id`; `Responder` traduce `generate.allowed_facts` (rutas de autoría `facts.<nombre>[.value…]`) a `{state.facts[nombre].fact_id}`. Una cita con forma de página (`ruta@snapshot#ancla`, `parse_page_ref` de M0) no se juzga aquí: es de la 6 y la 7.
3. **Cifras:**
   - Se extraen del texto números, montos, porcentajes y fechas con el parser del `number_format` del contexto (`1.234,56` o `1,234.56`); sin `number_format`, ver 3.3.
   - Cada cifra debe ser **numéricamente igual** a un valor de un hecho o página citados, con la precisión de la moneda (`Decimal`, sin tolerancia relativa).
   - Si la cifra no aparece en ningún hecho de origen `tool`/`identity`, debe venir de un hecho `compute`. Una cifra calculada sin su `compute` falla.
   - Se ignoran los dígitos dentro de tokens de M7.
   - `ValidationContext.fact_sources` se conserva en el contexto pero esta comprobación no lo usa: el criterio es de origen del número (aparece o no en un hecho citado), no de su tipo. Un hecho `compute` respalda cifras como cualquier otro, y una cifra calculada sin su `compute` citado falla porque no está en ningún hecho citado. Filtrar por `source.kind` no cambiaría ningún resultado y dejaría sin definir el caso `knowledge` (M12).
   - Las cifras del texto de una página citada cuentan como las de un hecho (M12): el texto de la página se lee con el mismo parser de cifras, con el `number_format` del contexto.
   - Todo número del texto cuenta igual ("paso 2", "24 horas"): debe estar en un hecho citado. Una cifra ambigua sin `number_format` se rechaza aunque un hecho la respalde. Las fechas se comparan con valores ISO (`aaaa-mm-dd`) de los hechos; los valores de hecho numéricos son `int`, `Decimal` o cadenas `-?dígitos(.dígitos)?`.
4. **Tokens y PII:** todo token del texto existe en el `token_map`; `ViewService.find_clear_pii` no encuentra identificadores `pii_direct` en claro.
5. **Idioma:** `detect_language(text)` con la configuración, los umbrales y los idiomas soportados de M6 (`prior = locale`). Si la decisión es `short` o `undetermined` no rechaza; en otro caso rechaza si el idioma de mayor puntaje (`top2[0]`) difiere del `locale` del turno. No depende de los umbrales de cambio (con `UNCALIBRATED` igual compara `top2[0]`). Un idioma no soportado también rechaza.
6. **`page_citations` (M12):** cada cita con forma de página está en `page_refs` (las páginas de los `save_as` listados en `generate.knowledge_from`). Una falla por cita, con su posición y sin su texto (`página_no_listada`).
7. **`page_audience` (M12):** en una respuesta al cliente, cada página citada es `public` + `approved` y vigente al instante `now` del `Clock` (`valid_from`/`valid_to` inclusivos); una falla por cita (`página_no_aprobada`, `audiencia_no_pública`, `página_no_vigente`). Falla cerrado: sin metadatos (`página_desconocida`) o sin `now` (`sin_reloj`). Una respuesta es **al cliente** si el nodo declara `purpose: customer_answer` **o** el principal del run es `customer` (así un `respond` que se declara `advisor_view` en un run de cliente no cita una página interna). No se aplica a las respuestas para asesores.

### 3.2 Cadena de `respond(generate)`

```
draft = gateway.generate(prompt_ref@locale, inputs vista model, schema={text, citations})
si validate falla → 1 regeneración (el motivo se agrega a la entrada)
si vuelve a fallar → fallback_template_ref
si la plantilla no puede renderizarse → EscalationRequest(validation_failed)
```

- Modo degradado: salta directo a `fallback_template_ref`.
- **Uso del LLM:** M8 acumula en `LlmUsage` cada llamada a `gateway.generate` del nodo (generación y regeneración): `calls`, `tokens` y `cost_usd` de `GenerationResult`, `latency_ms` medido con `Clock.monotonic_ns()` alrededor de cada llamada, y `models`. Va en `response_emitted.llm`, también si al final se usó la plantilla. Si no se llamó al gateway, `llm = None`.
- Cada borrador rechazado y su motivo se devuelve como `RejectedDraft` para que M11 lo mande al transcript. Si el borrador falló por PII en claro (`tokens_pii`), `text_model` va vacío: el texto no se conserva; `reason` solo lleva ids y rutas.
- **Detalles de falla sin texto del modelo:** `Failure.detail` (y por tanto `RejectedDraft.reason` y `validation_feedback`) solo lleva ids de comprobación, motivos fijos, rutas de campo, patrones (`pattern:email`), idiomas detectados y posiciones (`cita 2`, `cifra 1`, `token desconocido en la posición 1`). Nunca repite una cita, un token o una cifra escritos por el modelo, para que el prompt de regeneración no reintroduzca ese texto.
- **Falla del gateway** (2026-09-30, unidad 5; gateway spec §3.3): cualquier excepción de `gateway.generate` cuenta como una llamada, con `cost_known = false` salvo el uso que informe un `GatewayError`. Qué sigue depende del `kind`:

  | `GatewayError.kind` (u otra excepción) | Comportamiento |
  |---|---|
  | `invalid_output` | falla de la comprobación de formato: **1 regeneración** y luego la plantilla de respaldo |
  | `timeout`, `unavailable`, `rate_limited`, `refused` | directo a la plantilla de respaldo, sin regenerar |
  | otra excepción (p. ej. del cliente HTTP) | como `unavailable` |
- **Páginas de M12:** `Responder.generate` arma `page_refs`, `pages_meta` y `pages_model_view` desde `RunState.pages` de los `save_as` de `knowledge_from`, `customer_facing` (regla de la comprobación 7) y `now = Clock.now()`, y agrega a la entrada del modelo `pages: {save_as: [{ref, content}]}` en vista `model`. Sin `knowledge_from` no hay `pages` en la entrada.
- `Responder.template` trae un renderizador mínimo propio de `{{ facts.<nombre>.value(.campo)* }}` (M8 no importa `flows`); una ruta ausente, un locale ausente o una ruta que no sea de hechos lanza `TemplateUnavailable`, que la cadena traduce a `validation_failed`.
- Las plantillas (`respond.template_ref`) no pasan por las comprobaciones 2 y 3 (son texto fijo con variables de hechos), pero sí por la 4.

### 3.3 Formato numérico (decidido 2026-09-29, Abierto resuelto)

M8 **no** se acopla a teléfono, canal ni `principal.attrs.country`, y no contiene ninguna tabla país→formato.

- El formato llega como dato opcional `number_format` (`NumberFormat(thousands, decimal)`) dentro del `ValidationContext`. Lo produce un nodo o tool fuera de M8 (la correspondencia `es-CO`/`es-MX`/`es-AR`/`pt` de ADR 0011 es responsabilidad de quien construye el contexto).
- Con `number_format`, las cifras se leen con ese formato (`1.234,56` con punto/coma; `1,234.56` con coma/punto).
- Sin `number_format`, el parser acepta **solo lecturas inequívocas** y rechaza el resto: `1.234,56`, `1,234.56`, `1.234.567` y `12,5` se leen sin ambigüedad; `1.234` y `1,234` (un solo separador seguido de exactamente 3 dígitos) son ambiguos y se rechazan (`cifra_ambigua`).
- **No** se implementa la mejora "aceptar una cifra ambigua si alguna lectura coincide exactamente con un hecho citado": no está aprobada.
### 3.4 Cadena del nodo `suggest` (ADR 0026; `response/suggester.py`)

Generar → validar → regenerar una vez → fallar. Una llamada por intento (sin ReAct), `schema = SUGGESTIONS_SCHEMA` (plano, sin `oneOf`; el prompt va en `prompted`). El modelo devuelve `{suggestions: [...]}` y cada elemento solo lleva los campos de su tipo. Se comprueba, en este orden y reportando todas las fallas:

| Comprobación (id) | Qué exige |
|---|---|
| `format` | la salida cumple `SUGGESTIONS_SCHEMA`; cada elemento solo lleva los campos de su tipo; el elemento cumple el tipo de M0 (longitudes de la plataforma) |
| `too_many` | a lo sumo `max_items` |
| `reply` | las siete comprobaciones de §3.1 sobre el borrador (`allowed` = los hechos de `reads`); `language` es el del run |
| `tool_not_allowed`, `args_invalid` | `tool` ∈ catálogo del nodo (se acepta `id@MAYOR` o la versión exacta; sale `id@MAYOR`) y `args` cumplen su `args_schema`. El sujeto nunca es argumento (ningún esquema lo declara y `additionalProperties` es falso) |
| `action_not_allowed` | una `action` solo si el nodo declara `actions_allowed`; hoy vacío, así que nunca |
| `escalate_not_allowed`, `escalate_missing` | **el modelo no crea la escalación.** Solo existe si el flow la declaró; entonces el modelo redacta únicamente `motive_draft` y debe haber exactamente una. El `reason_code` y la evidencia son los del flow (un elemento que los traiga falla `format`) |
| `duplicate` | a lo sumo una `reply` y una `escalate` (la plataforma conserva una de cada una: un segundo borrador se perdería sin aviso) |
| `args_invalid` (también) | un argumento que el esquema de la tool no declara se rechaza aunque el esquema no ponga `additionalProperties: false`; `args` sin tokens, sin PII en claro y sin enlaces. Las cotas salen del esquema (`enum`; el subconjunto no tiene `maximum`) |
| `url` | ningún texto de sugerencia ni `args` lleva `esquema://` ni `www.` (diseño mínimo, ADR 0026 §6) |
| texto libre (`why`, `summary`, `motive_draft`) | `format`, `numbers` (toda cifra respaldada por algún hecho de `reads`) y `tokens_pii`; ningún texto de sugerencia lleva un token (`⟦…⟧`) |

El resultado va ordenado (`escalate`, `reply`, `tool`, `action`). Un `GatewayError` distinto de `invalid_output` termina sin regenerar (`gateway_<kind>`); `invalid_output` regenera. **Ninguna falla repite el texto del modelo:** `detail` lleva la posición (los mensajes del esquema, que pueden traer un nombre de propiedad, se reemplazan por uno genérico). `degraded` no llama al modelo (`degraded`). Sin PII en claro: `find_clear_pii` del contexto (hechos `pii_direct`) más `ViewService.find_tokenized_echo` (lo que M7 ocultó del texto del cliente, m07 §3.7.1) —el cierre lo arma la composición.

Pruebas: `tests/m08/test_suggester.py`.

## 4. Invariantes

- `validate` es pura y determinista (entra al replay `fixture`).
- Ningún texto generado llega al usuario sin pasar `validate` o sin ser reemplazado por una plantilla.
- Nunca se aplica tolerancia numérica.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Validador rechaza | 1 regeneración → plantilla → `escalate(validation_failed)` |
| `GatewayError` `invalid_output` | 1 regeneración → plantilla (§3.2) |
| `GatewayError` `timeout`, `unavailable`, `rate_limited` o `refused`, u otra excepción | directo a la plantilla de respaldo, sin regenerar |
| Cifra ambigua | rechazo (medido como falso rechazo en eval) |

## 6. Eventos que emite

`response_emitted {node_id, kind: template|generated, validator: {ok, failures, regenerations}, fallback_used, claims (de M1), transcript_fp, llm}`. La huella la calcula M11 y la agrega antes de encadenar.

`response_failed {node_id, reason_code: validation_failed, validator: {ok = false, failures, regenerations}, claims, llm}` (decidido el 2026-09-29, opción A; evento nuevo de M0): lo emite `generate` cuando termina en `EscalationRequest` (plantilla imposible o con PII en claro), en vez de `response_emitted`. Lleva el mismo `llm` (`calls`, `tokens`, `cost_usd`, `cost_known`), así que el gasto de una cadena que escala queda en la auditoría y M2 lo cobra como en `response_emitted`. `llm = None` en modo degradado. Sin texto de borradores.

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
| T-M8-12 | Errores del gateway por `kind`: `invalid_output` regenera una vez y luego usa la plantilla; `timeout`, `unavailable`, `rate_limited` y `refused` van directo a la plantilla; `cost_known = false` si el error no informa costo (`tests/m08/test_responder_generate.py`) | — |
| T-M8-13 | Comprobación 6: una cita a una página de un `save_as` no listado se rechaza; una listada pasa; los `fact_id` y las cadenas sin forma de página se ignoran (T-M12-03, `tests/m12/test_answer_checks.py`) | — |
| T-M8-14 | Comprobación 7: en una respuesta al cliente, `draft`, `internal`, `agent_only`, vencida o futura se rechazan y la pública aprobada vigente pasa; falla cerrado sin metadatos ni reloj; no aplica a respuestas de asesor (T-M12-02) | — |
| T-M8-15 | Las cifras de una página citada respaldan el texto; `Responder.generate` cablea las páginas desde `RunState.pages` y aplica la regla de cliente a todo run de un `customer` | — |

## 8. Evaluación

- **Principal:** afirmaciones sin fuente que se escapan (auditoría muestral de respuestas aprobadas).
- **Secundarias:** tasa de regeneración, tasa de uso de plantilla de respaldo, falsos rechazos (sobre un conjunto etiquetado de respuestas correctas), por idioma y por comprobación.
- **Costo de generar:** tokens, costo y latencia p50/p95 por respuesta (`response_emitted.llm`), por `prompt@v` y modelo; costo de las respuestas que terminaron en plantilla.

## 9. Puntos de iteración

- Comprobación nueva = función `check_x(draft, ctx)` en la lista ordenada + ID de `Failure`. Así entran las de conocimiento de M12.
- Formato numérico: dato `number_format` del contexto (sin tabla en M8).
- Número de regeneraciones: parámetro (1 en la spec).

## 10. Definición de terminado

- [x] `validate` con las cinco comprobaciones y la cadena de `generate` con T-M8-01…11 en verde (`uv run pytest tests/m08`).
- [x] Conjunto de 30+ respuestas etiquetadas (ES y PT) para medir falsos rechazos: `tests/m08/labeled_set.py` (34 casos sintéticos). Falso rechazo del conjunto principal: 0/26 (ES 0, PT 0). Sondeo de números que no son cifras de negocio (P2): 8/8 rechazados por la comprobación `numbers` (ES 4/4, PT 4/4); se mide y no se relaja la regla.
- [x] `ScriptedGateway` y su suite de contrato en `tests/contracts/test_gateway_contract.py`; `RequestCapture` conectado.
- [x] `pages_model_view` vacío hasta M12 y sin importar `agent_core.knowledge`. **rev. 3:** lo llena `Responder.generate` desde `RunState.pages`; M8 sigue sin importar `agent_core.knowledge` (solo tipos de M0).
- [x] Comprobaciones 6 y 7 (rev. 3): T-M8-13…15 en verde.
- [x] Interfaz pública exportada y tipada (`mypy` strict), `import-linter` y `ruff` en verde.
- [x] Reglas duras: sin hora, aleatoriedad, `float()` ni `json.loads` en `agent_core/response`; sin PII en `Failure.detail`, `RejectedDraft.reason` ni eventos.
- [x] Abiertos de M8 resueltos (§11); quedan fuera de M8 los de M2 y M12.
- [x] Spec en rev. 2 en el mismo cambio.
- [x] Revisión de `revisor-spec` atendida: borrador con PII sin texto en `RejectedDraft`, detalles de falla sin texto del modelo (también en `validation_feedback`), falla de gateway distinta de `GatewayError` definida, `fact_sources` documentado.

Notas de la implementación (rev. 2): si `generate` termina en `EscalationRequest` no se emite `response_emitted` sino `response_failed` (§6), con el uso del LLM de la cadena. En modo degradado el evento lleva `validator.ok = true` (no hubo borrador rechazado); tras rechazos o gateway caído, `ok = false` con los ids de la última falla.

## 11. Abiertos

- ~~**Formato numérico por país**~~ **Resuelto 2026-09-29:** `number_format` es un dato opcional del `ValidationContext` que produce un nodo/tool fuera de M8; sin él solo se aceptan lecturas inequívocas y `1.234` se rechaza (3.3). Queda sin aprobar, y sin implementar, aceptar una cifra ambigua cuando alguna lectura coincida exactamente con un hecho citado.
- ~~**Números que no son cifras de negocio** ("paso 2", "24 horas")~~ **Resuelto 2026-09-29:** cuentan igual que cualquier cifra y deben venir de un hecho o de la plantilla. No se añade `allowed_literals` (tocaría M0). El falso rechazo se mide con el conjunto etiquetado (10) antes de considerar cualquier relajación.
- ~~**Campos de `ValidationContext` para PII e idioma**~~ **Resuelto 2026-09-29:** `find_clear_pii`, `lang_thresholds`, `supported_locales` (§2).
- ~~**Firma de `Responder.generate` frente a `ResponderPort` de M2**~~ **Resuelto 2026-09-29:** M8 implementa la firma del §2; el adaptador vive fuera de M8.
- ~~**Semántica de idioma**~~ **Resuelto 2026-09-29:** compara `top2[0]` con `locale` (§3.1.5).
- ~~**`allowed_facts` frente a `fact_id`**~~ **Resuelto 2026-09-29:** `Responder` traduce (§3.1.2).
- ~~**Renderizado de plantillas**~~ **Resuelto 2026-09-29:** renderizador mínimo propio (§3.2).
- **Abiertos que siguen fuera de M8:** ~~quién emite `response_emitted` para `respond(template_ref)`~~ (resuelto: M2, D6); ~~`PageView`, `knowledge_from` y su comprobación de citas de páginas (M12, tema #10)~~ **resuelto 2026-09-30:** comprobaciones 6 y 7 (rev. 3).
- ~~**Uso del LLM cuando `generate` escala**~~ **Resuelto 2026-09-29 (opción A):** evento nuevo `response_failed` (M0 §2.10; `contracts/` regenerado). El adaptador de M2 debe tomar `model_calls`, `tokens` y `cost_usd` de `response_emitted.llm` o de `response_failed.llm`.
- **Nota (`.importlinter`):** el contrato `response` se llama "M8 (response) solo usa domain, ports y guards, views, knowledge". Es exacto como lista de dependencias permitidas (el contrato solo prohíbe el resto); M8 hoy no importa `knowledge`. No se toca.
