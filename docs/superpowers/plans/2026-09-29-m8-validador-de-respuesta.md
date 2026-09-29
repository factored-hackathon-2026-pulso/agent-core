# M8 — Validador de respuesta y `respond(generate)`: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Nota de autoría:** la skill `superpowers:writing-plans` no está instalada en este entorno; este plan sigue el formato de `2026-09-29-m7-vistas-y-tokenizacion.md`.

**Goal:** Construir `agent_core.response`: `validate(draft, ctx)` (función pura, cinco comprobaciones en lista ordenada `check_x(draft, ctx)`), `Responder` (`template` y la cadena `generate → validar → regenerar 1 vez → plantilla → escalar`), el acumulador de `LlmUsage`, el evento `response_emitted`, el doble `ScriptedGateway` con su suite de contrato, y T-M8-01…11 en verde.

**Architecture:** M8 solo importa `agent_core.domain`, `agent_core.ports`, `agent_core.guards`, `agent_core.views` y `agent_core.knowledge` (contrato `response` de `.importlinter`; `flows`, `interpreter`, `actions`, `turn`, `handoff`, `audit`, `decision`, `adapters`, `registry` están **prohibidos**). Cada comprobación es una función `check_x(draft, ctx) -> list[Failure]` registrada en `CHECKS` (orden fijo). `validate` las ejecuta todas y agrega sus fallas (se reportan todas). El parser numérico es mecánica pura parametrizada por un `NumberFormat` (dato); la tabla país→formato es dato y **está bloqueada por un punto de PARADA** (Abierto §11.1). `Responder` orquesta con puertos inyectados (`LLMGateway`, `Clock`, `IdSource`, `RegistryPort`) y no lee hora ni aleatoriedad por su cuenta.

**Tech Stack:** Python 3.12, Pydantic v2, `Decimal`, `re` de la stdlib, `lingua` vía `agent_core.guards.detect_language` (M6, ya instalado), pytest + hypothesis, ruff, mypy strict, import-linter.

**Spec:** `docs/specs/motor/m08-validador-de-respuesta.md`. Contexto: `docs/specs/motor/00-indice.md` (§3 dependencias, §4 puertos —`ScriptedGateway` lo entrega M8—, §5 dueños del estado, §6 eventos), ADR 0011 (validación numérica, hechos `compute`), ADR 0012 (idioma, mismo detector que M6), ADR 0008 (validador sobre vista `model`), spec general §8.3 y §5 (`respond`). Léelos antes de empezar: el spec manda sobre este plan; si el código real difiere, gana el código verificado y se deja constancia en la Task 1.

---

## Puntos de PARADA (leer antes de empezar)

CLAUDE.md: "Si el spec es ambiguo, contradice un ADR o toca un 'Abierto': detente y pregunta. No lo resuelvas por tu cuenta." Este plan **no resuelve** ninguno de los siguientes; cada uno bloquea las tareas indicadas y termina en una pregunta literal al usuario. Las "propuestas" son solo la opción que el spec o este plan sugieren, no una decisión.

| ID | Tema | Origen | Bloquea |
|---|---|---|---|
| **P1** | Formato numérico por país: `number_format` = f(idioma, país); tabla `es-CO`/`es-MX`/`es-AR`/`pt` frente a `locale` `es`/`pt`; de dónde sale el país; qué hacer con `1.234` sin contexto | Abierto §11.1 (y índice §10, "nuevo") | Task 9, Task 10 (parte de T-M8-01), Task 15 |
| **P2** | Números que no son cifras de negocio ("paso 2", "24 horas"): ¿cuentan igual? | Abierto §11.2 | Task 10, Task 15 |
| **P3** | `ValidationContext` no trae lo que las comprobaciones 4 y 5 necesitan (ver D1) | Discrepancia spec/código | Tasks 3, 6, 7 |
| **P4** | `Responder.generate(node_config, state, ctx)` del spec vs `ResponderPort.generate(request, state) -> GenerateResult` que M2 ya consume (ver D2) | Discrepancia spec/código | Task 12+ |
| **P5** | Semántica de la comprobación de idioma frente a `LangDecision` (ver D3) | Spec ambiguo | Task 7 |
| **P6** | `allowed_facts` (rutas `facts.<nombre>`) vs citas por `fact_id` (ver D4) | Spec ambiguo | Task 5, Task 12 |
| **P7** | `Responder.template` sin poder importar `flows`/`interpreter` (ver D5) | Discrepancia spec/código | Task 11 |

La **Task 1** es un gate: reúne las respuestas y sube el spec a rev. 2. Las tareas independientes de las paradas (Task 2 `ScriptedGateway`, Task 8 mecánica del parser numérico, Task 4 formato) pueden hacerse mientras se espera respuesta.

## Global Constraints

- Python `>=3.12,<3.13` (ADR 0001). Sin colas, Redis ni vector DB.
- `agent_core.response` solo importa `agent_core.domain`, `agent_core.ports`, `agent_core.guards`, `agent_core.views`, `agent_core.knowledge` (`uv run lint-imports`, contrato `response`). **Nunca** `agent_core.interpreter`, `agent_core.flows` ni `agent_core.turn`. Si necesitas algo que no está en una interfaz pública, detente y pregunta.
- Nunca `datetime.now()`, `time.time()`, `time.monotonic*`, `time.perf_counter*`, `uuid4()`, `random`, `secrets`: la hora sale de `Clock.now()`, la latencia de `Clock.monotonic_ns()` y los IDs de `IdSource.new_id(IdKind.event)` (lo verifica `ruff`, TID251).
- Cifras en `Decimal`, **nunca `float`**; sin tolerancia relativa (invariante §4). Todo JSON entra con `agent_core.domain.loads`; toda canonización (`canonical_bytes`) es de M0. Ningún `float()` ni `json.loads` en `agent_core/response`.
- `validate` es pura y determinista: sin reloj, sin IDs, sin I/O, sin mutar `draft` ni `ctx` (entra al replay `fixture`).
- Sin PII: ni `Failure.detail`, ni `RejectedDraft.reason`, ni eventos, ni `repr` incluyen valores `full`; solo rutas de campo (`find_clear_pii` ya devuelve rutas, nunca valores), tokens y `check` ids. Nada en vista `full` sale a modelos: el gateway solo recibe vista `model`.
- Fixtures y pruebas solo con datos sintéticos (`example.test`, documentos inventados, montos inventados). Nunca datos reales del dataset ni credenciales del diccionario de datos.
- No tocar otros módulos ni `agent_core/domain`. Si algo de M0 debe cambiar, es cambio de interfaz: detente y avisa (regla 7: `uv run agentcore contracts`). El plan **no** requiere cambiar M0.
- Comandos: `uv run pytest tests/m08`, `uv run pytest tests/contracts`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore contracts --check`.
- `ruff`: `line-length = 110`. Si solo marca `I001`/`RUF022`, corrige con `uv run ruff check --fix`.
- Commits: mensajes en español, prefijo `feat(m8):` / `test(m8):` / `docs(m8):`, y terminan con:

```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof
```

(En este plan el commit es una tarea; quien ejecute el plan lo hace solo cuando el usuario lo pida, según las reglas de la sesión.)

## Interfaces consumidas (verificadas contra el código real, 2026-09-29)

| Símbolo | Firma real | Notas |
|---|---|---|
| `agent_core.ports.LLMGateway.generate` | `(prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale, schema: dict[str, JsonValue] \| None = None) -> GenerationResult` | falla con `GatewayError` |
| `agent_core.ports.GenerationResult` | `output: JsonValue; tokens_in: int; tokens_out: int; cost_usd: Decimal; model: str` | el spec habla de `tokens`; el real separa `tokens_in`/`tokens_out` (coincide con `LlmUsage`) |
| `agent_core.domain.GatewayError` | `(kind: GatewayErrorKind, *, tokens_in, tokens_out, cost_usd, model)` opcionales; atributos con el mismo nombre | uso parcial → `cost_known` |
| `agent_core.ports.Clock` | `now() -> UtcDatetime`; `monotonic_ns() -> int` | latencia solo con `monotonic_ns` |
| `agent_core.ports.IdSource` | `new_id(kind: IdKind) -> str`; `IdKind.event` | ids de `response_emitted` |
| `agent_core.ports.RegistryPort.get` | `get[T](ref: EntityRef, kind: type[T]) -> T` | `Template`, `Prompt` |
| `agent_core.domain.Message` | `kind: "template"\|"generated"; text: str; locale: Locale` | |
| `agent_core.domain.EscalationRequest` | `reason_code: ReasonCodeStr; target_queue: str; priority: str` | `validation_failed` existe en `ReasonCode` |
| `agent_core.domain.RejectedDraft` | `text_model: str; reason: str; failures: list[str]` | `failures` = ids de comprobación |
| `agent_core.domain.LlmUsage` | `calls, latency_ms, tokens_in, tokens_out, cost_usd, cost_known: bool, models: list[str]` | el spec dice `calls, tokens, cost_usd, latency_ms, models`; el real agrega `cost_known` y separa tokens |
| `agent_core.domain.ValidatorOutcome` | `ok: bool; failures: list[str]; regenerations: NonNegativeInt` | |
| `agent_core.domain.ResponseEmittedPayload` | `node_id, kind, validator, fallback_used, claims: list[str], transcript_fp: Fingerprint\|None, llm: LlmUsage\|None` | |
| `agent_core.domain.ResponseEmitted` | `EngineEvent` con `event_id, run_id, turn_id, session_id, release, ts` + `payload`; `seq/prev_hash/hash` los pone M11 | mismo patrón que `interpreter/events.py` |
| `agent_core.domain.GenerateConfig` | `prompt_ref: RefSpec; allowed_facts: list[str]; knowledge_refs: list[str]; fallback_template_ref: RefSpec` | **no** tiene `knowledge_from`/`purpose` (M12 los agrega, tema #10) |
| `agent_core.domain.Fact` / `FactSource` | `Fact(fact_id, value, source, ts)`; `FactSource(kind: "tool"\|"compute"\|"identity"\|"knowledge", ref, inputs)` | `fact_sources` del spec sale de `state.facts[*].source` |
| `agent_core.domain.LanguageDetection` | `id, version, detector, candidates, unsupported, min_letters, min_letters_unsupported, thresholds_from` | |
| `agent_core.guards.detect_language` | `(text_model_view: str, cfg: LanguageDetection, thresholds: LangThresholds, supported: list[str], prior: str \| None) -> LangDecision` | requiere `thresholds` y `supported` que `ValidationContext` del spec **no** trae (P3) |
| `agent_core.guards.LangDecision` | `decision: "kept"\|"switched"\|"short"\|"undetermined"\|"unsupported"; locale; locale_prior; letters; top2: list[tuple[str, float]]; detector` | |
| `agent_core.guards.LangThresholds` / `UNCALIBRATED` | `switch_threshold, unsupported_threshold, min_distance`; con `UNCALIBRATED` (1.0) nunca hay `switched`/`unsupported` | P5 |
| `agent_core.views.TokenVault` | `exists(token) -> bool`; `lookup(token) -> TokenEntry\|None`; `resolve(token)` | comprobación 4 |
| `agent_core.views.TOKEN_PATTERN` | `⟦([a-z]{1,12}):([1-9][0-9]*)⟧` (str; **`TOKEN_RE` no está exportado**: compilar `re.compile(TOKEN_PATTERN)` localmente) | comprobación 3 y 4 |
| `agent_core.views.ViewService.find_clear_pii` | `(text: str, facts_full: Mapping[str, JsonValue]) -> list[str]` (rutas, `pattern:email`; nunca valores) | requiere `facts_full` y una instancia de `ViewService` que `ValidationContext` del spec no trae (P3) |
| `agent_core.knowledge` | paquete vacío (docstring "pendiente, tema #10"); `PageView` **no existe** en ningún módulo | `pages_model_view` queda vacío hasta M12 |

**Referencia del intérprete (no importable desde M8):** `agent_core.interpreter.ports` define hoy `GenerateRequest(node_id, config: GenerateConfig, claims: frozenset[str])`, `GenerateResult(message, escalation, rejected, events, model_calls, tokens, cost_usd)` y `ResponderPort.generate(request, state) -> GenerateResult`; `handlers/respond.py` lo invoca y `testing/fakes/responder.py` (`ScriptedResponder`) lo dobla. No hay ningún import ni stub de `agent_core.response` en el repo (`git grep "agent_core.response"` solo aparece en `.importlinter`).

## Discrepancias spec/código y decisiones propuestas (todas van a la Task 1)

| # | Discrepancia | Opción propuesta (a confirmar) |
|---|---|---|
| **D1** (P3) | `validate` debe llamar a `ViewService.find_clear_pii(text, facts_full)` y a `detect_language(text, cfg, thresholds, supported, prior)`, pero `ValidationContext` (spec §2) solo trae `vault`, `locale`, `number_format`, `lang_cfg`. Faltan `facts_full`/`ViewService`, `LangThresholds`, `supported_locales`. Meter `facts_full` en el contexto pondría vista `full` en una estructura del validador | Añadir a `ValidationContext`: `find_clear_pii: Callable[[str], list[str]]` (cierre que liga `ViewService` + `facts_full`, sin exponer `full`), `lang_thresholds: LangThresholds`, `supported_locales: list[str]`. `validate` sigue siendo pura si el cierre lo es |
| **D2** (P4) | Spec: `Responder.generate(node_config, state, ctx) -> tuple[Message \| EscalationRequest, list[RejectedDraft], list[EngineEvent]]`. Real: M2 espera `ResponderPort.generate(GenerateRequest, RunState) -> GenerateResult` y cobra presupuestos con `model_calls/tokens/cost_usd`. M8 **no puede** importar `agent_core.interpreter` (contrato `response`), así que el adaptador `Responder → ResponderPort` no puede vivir en M8 | M8 implementa la firma del spec. El adaptador (que además extrae `model_calls/tokens/cost_usd` de `response_emitted.llm`) es un cambio de M2 (o del cableado de M4), **fuera de este plan**; se documenta y se pregunta. Nota (d) de M2 §Abiertos (`GenerateRequest` sin presupuesto restante) queda abierta |
| **D3** (P5) | El spec dice "`detect_language(text)` con los mismos candidatos que M6 debe dar el `locale` del turno. `short`/`undetermined` no rechazan". Con `UNCALIBRATED` (1.0) `detect_language` **nunca** devuelve `switched`/`unsupported`: siempre `kept` con el `prior`, así que "el `locale` que da" coincidiría siempre y la comprobación jamás rechazaría | Rechazar si `decision ∉ {short, undetermined}` y `top2[0][0] != ctx.locale` (idioma de mayor puntaje, independiente de umbrales de cambio); `prior = ctx.locale`. Pide confirmación y si se calibra distinto |
| **D4** (P6) | `GenerateConfig.allowed_facts` son rutas de autoría `facts.<nombre>` (M1 §3.2; el test de M2 usa `facts.pqr.value.id`), `<nombre>` = clave de `state.facts`. Las citas y `ctx.allowed`/`facts_model_view` del spec están por `fact_id` (`Fact.fact_id`) | `Responder` traduce `allowed_facts` → `{state.facts[nombre].fact_id}` (acepta con o sin `.value…`). Para plantillas `facts_model_view` va por **nombre** (las plantillas usan `{{ facts.<nombre>.value.… }}`); para `validate` se re-indexa por `fact_id` |
| **D5** (P7) | `Responder.template(template_ref, locale, facts_model_view)` debe resolver y renderizar `{{ ruta }}`; hoy eso vive en `interpreter/templates.py`/`resolve.py` y en `flows.template_vars`, ambos prohibidos para M8 | M8 trae un renderizador mínimo propio (regex `{{ facts.<n>.value(.campo)* }}` sobre `facts_model_view` por nombre; ruta ausente → `TemplateUnavailable`, que la cadena traduce a `validation_failed`). Alternativa a preguntar: M2 inyecta un `render` en `ResponderContext` |
| **D6** | El spec usa `ValidationContext.fact_sources: dict[str, FactSource]` y `facts_model_view: dict[str, Any]`; `Any` no pasa `mypy` strict con el estilo del repo | Tipar como `Mapping[str, JsonValue]` y `Mapping[str, FactSource]` |
| **D7** | `pages_model_view: dict[str, PageView]` — `PageView` no existe (M12) | `Mapping[str, JsonValue]`, siempre vacío hasta M12 (tema #10); la comprobación 2 solo acepta `fact_id`. Al llegar M12 se cambia el tipo en el mismo PR que M12 |
| **D8** | `llm.calls/tokens/cost_usd/latency_ms/models` del spec vs `LlmUsage` real con `tokens_in/tokens_out/cost_known` | Usar `LlmUsage` real. `cost_known = False` si alguna llamada falló con `GatewayError` sin `cost_usd` |
| **D9** | M2 D6: hoy M2 renderiza las plantillas de `respond(template_ref)` y no emite `response_emitted`. El spec §6 dice que M8 emite `response_emitted kind: template|generated` | M8 expone `Responder.template` y emite `response_emitted` **solo desde `generate`**; quién lo emite para `template_ref` directo sigue siendo abierto de M2 (§Abiertos). No se resuelve aquí |

## Lista ordenada de comprobaciones (`CHECKS`)

| Orden | ID de `Failure.check` | Función | Depende de |
|---|---|---|---|
| 1 | `format` | `check_format(draft, ctx)` | salida del gateway ya parseada (`parse_draft`) |
| 2 | `citations` | `check_citations(draft, ctx)` | `ctx.facts_model_view`, `ctx.pages_model_view`, `ctx.allowed` |
| 3 | `numbers` | `check_numbers(draft, ctx)` | parser + `NumberFormat`, `ctx.fact_sources`, P1, P2 |
| 4 | `tokens_pii` | `check_tokens_pii(draft, ctx)` | `ctx.vault`, `ctx.find_clear_pii` (P3) |
| 5 | `language` | `check_language(draft, ctx)` | `detect_language`, `ctx.lang_*` (P3, P5) |

Nota de `format`: `validate(draft, ctx)` recibe un `Draft` ya construido; el fallo de formato (comprobación 1) nace en `parse_draft(output: JsonValue) -> Draft | Failure` (la salida del gateway no parsea como `{text, citations}`). La cadena convierte ese `Failure` en un `ValidationResult` con `check="format"` y **no** llama al resto (no hay texto que validar). `check_format` valida además invariantes del `Draft` (texto no vacío, citas `str`). Añadir una comprobación nueva (M12) = una función `check_x` + su ID en `Failure.check` + una entrada en `CHECKS`.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `docs/specs/motor/m08-validador-de-respuesta.md` | spec rev. 2 (decisiones de las paradas; Task 1) |
| `docs/specs/motor/00-indice.md` | §10: marcar el tema "formato numérico por país" como resuelto (solo tras P1) |
| `agent_core/response/types.py` | `Draft`, `Failure`, `CheckId`, `ValidationResult`, `ValidationContext`, `parse_draft` |
| `agent_core/response/checks.py` | `check_format`, `check_citations`, `check_tokens_pii`, `check_language`, `CHECKS`, `Check` |
| `agent_core/response/numbers.py` | `NumberFormat`, `scan_figures`, `parse_amount`, `parse_percent`, `parse_date`, `Figure` (mecánica, sin tabla) |
| `agent_core/response/formats.py` | tabla `NUMBER_FORMATS` y `resolve_number_format` (**bloqueada por P1**) |
| `agent_core/response/check_numbers.py` | `check_numbers` (bloqueada por P1/P2) |
| `agent_core/response/validate.py` | `validate` |
| `agent_core/response/usage.py` | `UsageMeter` (acumula `LlmUsage` con `Clock.monotonic_ns`) |
| `agent_core/response/templates.py` | renderizador mínimo `render_template_text`, `TemplateUnavailable` (D5) |
| `agent_core/response/responder.py` | `ResponderContext`, `Responder` (`template`, `generate`), armado de `response_emitted` |
| `agent_core/response/__init__.py` | interfaz pública |
| `testing/fakes/gateway.py` | `ScriptedGateway` (guionable, registra requests en `RequestCapture`) |
| `tests/contracts/test_gateway_contract.py` | suite de contrato de `LLMGateway` contra `ScriptedGateway` |
| `tests/m08/helpers.py` | constructores sintéticos (`make_ctx`, `make_state`, `make_responder`) |
| `tests/m08/test_*.py` | pruebas por archivo; T-M8-01…11 y trazabilidad |
| `tests/m08/labeled_set.py`, `tests/m08/test_labeled_set.py` | 30+ respuestas etiquetadas ES/PT y medición de falsos rechazos |

---

### Task 1: Gate de decisiones (PARADA) y spec rev. 2

**Files:**
- Modify: `docs/specs/motor/m08-validador-de-respuesta.md`
- Modify (solo tras P1): `docs/specs/motor/00-indice.md` §10

**Interfaces:**
- Consumes: nada.
- Produces: el spec rev. 2 con las firmas exactas que usan las Tasks 3–15.

- [ ] **Step 1: Verificar la base**

Run: `git status --short && git log --oneline -3 && uv sync && uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: árbol limpio en `claude/m5-m8-m4-parallel-8z4wxx`, todo en verde, contrato `M8 (response)…` en `KEPT`.

- [ ] **Step 2: Confirmar las dependencias antes de escribir nada**

Run: `uv run python -c "import agent_core.guards as g, agent_core.views as v; print(g.detect_language, g.LangThresholds, v.TokenVault, v.ViewService.find_clear_pii, v.TOKEN_PATTERN)"`
Expected: imprime los objetos sin error.
Run: `git grep -n "agent_core.response" -- '*.py'` → Expected: sin resultados (M8 no tiene consumidores aún).
Run: `git grep -n "PageView" -- agent_core` → Expected: sin resultados (D7).
Run: `git grep -n "ScriptedGateway" -- '*.py'` → Expected: sin resultados (lo crea la Task 2).

- [ ] **Step 3: PARADA — preguntar al usuario (todas juntas, sin resolver)**

Pregunta literal al usuario (usa AskUserQuestion o mensaje directo) y **espera respuesta** antes de las tareas bloqueadas:

1. **P1 — formato numérico por país (Abierto §11.1).** "El `locale` del run es `es`/`pt`, pero ADR 0011 parsea por `es-CO`/`es-MX`/`es-AR`/`pt`. La propuesta del spec §3.3 es: `number_format` = f(idioma, país del subject) con el país en `principal.attrs.country` o en el hecho de identidad; `es-CO` y `es-AR` → `1.234,56`; `es-MX` → `1,234.56`; `pt` → `1.234,56`; sin país, se aceptan ambos solo si la lectura es inequívoca y `1.234` sin contexto se rechaza. ¿La apruebas tal cual, la cambias, o prefieres solo `es`/`pt` (¿cuál formato para cada uno?)? ¿De dónde sale el país exactamente (`principal.attrs.country`, hecho de identidad, ambos y en qué orden)? ¿Quién calcula `number_format` (M8 o quien construye el contexto: M2/M4)?"
2. **P2 — números que no son cifras de negocio (Abierto §11.2).** "'Paso 2', '24 horas', '3 días': la propuesta es que cuenten igual y deban venir de un hecho o de la plantilla, y medir el falso rechazo antes de relajar. ¿Se aprueba, o se excluye alguna clase (ordinales, duraciones, listas numeradas)?"
3. **P3 (D1).** "¿Aprobado añadir a `ValidationContext` `find_clear_pii: Callable[[str], list[str]]`, `lang_thresholds` y `supported_locales`?"
4. **P4 (D2).** "M2 ya consume `ResponderPort`/`GenerateResult`; M8 no puede importarlo. ¿Implemento la firma del spec y dejo el adaptador a M2/M4 (que lea `model_calls/tokens/cost_usd` de `response_emitted.llm`), o cambiamos primero la firma en el spec?"
5. **P5 (D3).** "Comprobación de idioma: rechazar si el idioma de mayor puntaje (`top2[0]`) difiere del `locale` y la decisión no es `short`/`undetermined`. ¿De acuerdo?"
6. **P6 (D4).** "Traducir `allowed_facts` (`facts.<nombre>[.value…]`) a `fact_id` dentro de `Responder`. ¿De acuerdo?"
7. **P7 (D5).** "`Responder.template` con renderizador mínimo propio de `{{ facts.<n>.value… }}` (M8 no puede importar `flows`). ¿De acuerdo, o M2 inyecta el render?"

Si el usuario responde solo algunas, continúa únicamente con las tareas no bloqueadas y vuelve a preguntar el resto cuando toque.

- [ ] **Step 4: Subir el spec a rev. 2 con las respuestas**

Edita `m08-validador-de-respuesta.md`: estado `rev. 2 (2026-09-29)`; §2 con `ValidationContext` real (tipos de D1, D6, D7), `LlmUsage` real (D8) y `Responder.generate` tal como se apruebe (D2); §3.1 con la semántica de idioma (D3) y de citas (D4); §3.3 con la tabla aprobada en P1; §11 marcar cada Abierto como resuelto con la decisión y fecha. **No inventes** ninguna decisión que el usuario no dio: lo no respondido queda en §11 como abierto.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/motor/m08-validador-de-respuesta.md docs/specs/motor/00-indice.md
git commit -m "docs(m8): spec rev. 2 con las decisiones de interfaz y los abiertos resueltos

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof"
```

---

### Task 2: `ScriptedGateway` y su suite de contrato

Independiente de las paradas. El doble no valida texto; devuelve `GenerationResult` guionados o lanza `GatewayError`, registra los requests (solo vista `model`) y los pasa por `RequestCapture` (cierra el pendiente de M7, T-M7-01).

**Files:**
- Create: `testing/fakes/gateway.py`
- Create: `tests/contracts/test_gateway_contract.py`

**Interfaces:**
- Consumes: `LLMGateway`, `GenerationResult`, `GatewayError`, `GatewayErrorKind`, `EntityRef`, `JsonValue`, `Locale`, `dumps`, `testing.capture.RequestCapture`.
- Produces: `ScriptedGateway(script=(), *, capture=None)` con `push(*items)`, `calls: list[GatewayCall]`; `GatewayCall(prompt, inputs, locale, schema)`; `script_item = GenerationResult | GatewayError`; helper `gen(text, citations, *, tokens_in=10, tokens_out=5, cost="0.001", model="scripted-1") -> GenerationResult` con `output={"text": text, "citations": citations}`.

- [ ] **Step 1: Escribir la suite de contrato (falla)**

`tests/contracts/test_gateway_contract.py`:

```python
"""Contrato de `LLMGateway` (M0 §2.9) contra `ScriptedGateway`, más sanidad negativa y propias del doble."""

from decimal import Decimal

import pytest

from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind
from agent_core.ports import GenerationResult, LLMGateway
from testing.capture import RequestCapture
from testing.fakes.gateway import ScriptedGateway, gen

PROMPT = EntityRef.parse("resumen@1.0.0")
INPUTS = {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}


def check_returns_generation_result(gateway: LLMGateway) -> None:
    out = gateway.generate(PROMPT, INPUTS, "es", {"type": "object"})
    assert isinstance(out, GenerationResult)
    assert out.tokens_in >= 0 and out.tokens_out >= 0
    assert isinstance(out.cost_usd, Decimal) and out.cost_usd >= 0
    assert out.model


def check_failure_is_gateway_error(gateway: LLMGateway) -> None:
    with pytest.raises(GatewayError) as caught:
        gateway.generate(PROMPT, INPUTS, "es")
    assert isinstance(caught.value.kind, GatewayErrorKind)


def check_does_not_mutate_inputs(gateway: LLMGateway) -> None:
    before = {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}
    gateway.generate(PROMPT, before, "es")
    assert before == {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}


@pytest.fixture
def ok_gateway() -> LLMGateway:
    return ScriptedGateway([gen("hola", []), gen("hola", []), gen("hola", [])])


@pytest.fixture
def failing_gateway() -> LLMGateway:
    return ScriptedGateway([GatewayError(GatewayErrorKind.unavailable, model="scripted-1")])


def test_returns_generation_result(ok_gateway: LLMGateway) -> None:
    check_returns_generation_result(ok_gateway)


def test_failure_is_gateway_error(failing_gateway: LLMGateway) -> None:
    check_failure_is_gateway_error(failing_gateway)


def test_does_not_mutate_inputs(ok_gateway: LLMGateway) -> None:
    check_does_not_mutate_inputs(ok_gateway)


class _FloatCostGateway:
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, object], locale: str,
                 schema: dict[str, object] | None = None) -> GenerationResult:
        return GenerationResult.model_construct(output=None, tokens_in=1, tokens_out=1, cost_usd=0.1, model="m")


def test_contract_detects_non_decimal_cost() -> None:
    with pytest.raises(AssertionError):
        check_returns_generation_result(_FloatCostGateway())  # type: ignore[arg-type]


def test_scripted_records_calls_as_copies_and_in_order() -> None:
    gateway = ScriptedGateway([gen("uno", []), gen("dos", [])])
    inputs: dict[str, object] = {"a": {"b": 1}}
    gateway.generate(PROMPT, inputs, "es")  # type: ignore[arg-type]
    inputs["a"] = "mutado"
    gateway.generate(PROMPT, {"z": 2}, "pt")
    assert [c.locale for c in gateway.calls] == ["es", "pt"]
    assert gateway.calls[0].inputs == {"a": {"b": 1}}


def test_scripted_without_script_fails_loudly() -> None:
    with pytest.raises(AssertionError, match="sin resultado guionado"):
        ScriptedGateway().generate(PROMPT, {}, "es")


def test_scripted_feeds_request_capture() -> None:
    capture = RequestCapture()
    gateway = ScriptedGateway([gen("ok", [])], capture=capture)
    gateway.generate(PROMPT, {"doc": "⟦doc:1⟧"}, "es")
    assert capture.leaks(["1023456789"]) == [] and len(capture.requests) == 1


def test_gen_output_is_the_draft_shape() -> None:
    assert gen("t", ["f1"]).output == {"text": "t", "citations": ["f1"]}
```

- [ ] **Step 2: Confirmar que falla**

Run: `uv run pytest tests/contracts/test_gateway_contract.py -q`
Expected: FAIL (`ModuleNotFoundError: testing.fakes.gateway`).

- [ ] **Step 3: Implementar `testing/fakes/gateway.py`**

Contrato del archivo (mismo estilo que `testing/fakes/decision.py`): `deque` de `GenerationResult | GatewayError`; `generate` copia `inputs` con `deepcopy`, registra `GatewayCall`, llama `capture.record({"gateway": "llm", "prompt": str(prompt), "locale": locale, "inputs": inputs})` si hay `capture`, y si el siguiente elemento es `GatewayError` lo **lanza** (también lo registra en `calls`); sin script → `AssertionError("ScriptedGateway sin resultado guionado")`. `gen(...)` arma `GenerationResult(output={"text": text, "citations": citations}, tokens_in, tokens_out, cost_usd=Decimal(cost), model=model)` (nunca `float`). Al final, bloque `if TYPE_CHECKING:` con `_conforms(x: ScriptedGateway) -> LLMGateway`.

- [ ] **Step 4: Confirmar que pasa**

Run: `uv run pytest tests/contracts/test_gateway_contract.py -q && uv run mypy && uv run ruff check .`
Expected: PASS y sin hallazgos.

- [ ] **Step 5: Commit**

```bash
git add testing/fakes/gateway.py tests/contracts/test_gateway_contract.py
git commit -m "feat(m8): ScriptedGateway y su suite de contrato en tests/contracts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof"
```

---

### Task 3: Tipos públicos (`Draft`, `Failure`, `ValidationContext`) — requiere P3, P6, P7 resueltas

**Files:**
- Create: `agent_core/response/types.py`
- Create: `tests/m08/__init__.py`, `tests/m08/helpers.py`, `tests/m08/test_types.py`

**Interfaces:**
- Consumes: `FactSource`, `LanguageDetection`, `JsonValue`, `Model` (`agent_core.domain.base`), `TokenVault`, `LangThresholds`.
- Produces (tal como quede el spec rev. 2; forma propuesta):

```python
CheckId = Literal["format", "citations", "numbers", "tokens_pii", "language"]

class Draft(Model):            text: str; citations: list[str]                 # fact_id | page_ref
class Failure(Model):          check: CheckId; detail: str
class ValidationResult(Model): ok: bool; failures: list[Failure]               # ok == (failures == [])

@dataclass(frozen=True, slots=True, repr=False)
class ValidationContext:
    facts_model_view: Mapping[str, JsonValue]        # por fact_id
    fact_sources: Mapping[str, FactSource]           # por fact_id
    allowed: frozenset[str]
    pages_model_view: Mapping[str, JsonValue]        # vacío hasta M12
    vault: TokenVault
    locale: str
    number_format: str
    lang_cfg: LanguageDetection
    lang_thresholds: LangThresholds                  # P3
    supported_locales: tuple[str, ...]               # P3
    find_clear_pii: Callable[[str], list[str]]       # P3

def parse_draft(output: JsonValue) -> Draft | Failure   # `format` (comprobación 1)
```

`ValidationContext.__repr__` no muestra `facts_model_view`, `vault` ni el cierre (PII/tokens).

- [ ] **Step 1: Pruebas (fallan)** — `tests/m08/test_types.py`:

```python
from agent_core.response.types import Draft, Failure, ValidationResult, parse_draft


def test_parse_draft_accepts_text_and_citations() -> None:
    assert parse_draft({"text": "hola", "citations": ["f1"]}) == Draft(text="hola", citations=["f1"])


def test_parse_draft_rejects_bad_shapes_as_format_failure() -> None:
    for bad in (None, "texto", {"text": "x"}, {"text": 1, "citations": []}, {"text": "x", "citations": "f1"},
                {"text": "x", "citations": [1]}, {"text": "x", "citations": [], "extra": 1}):
        out = parse_draft(bad)  # type: ignore[arg-type]
        assert isinstance(out, Failure) and out.check == "format"


def test_failure_detail_never_echoes_the_payload() -> None:
    out = parse_draft({"text": 1234567890, "citations": []})
    assert isinstance(out, Failure) and "1234567890" not in out.detail


def test_result_ok_is_derived_from_failures() -> None:
    assert ValidationResult(ok=True, failures=[]).ok
    assert not ValidationResult(ok=False, failures=[Failure(check="numbers", detail="x")]).ok
```

- [ ] **Step 2:** `uv run pytest tests/m08/test_types.py -q` → FAIL (módulo inexistente).
- [ ] **Step 3: Implementar** `types.py` (`Model` de M0 es `extra="forbid"`; `parse_draft` no lanza: atrapa `ValidationError` y devuelve `Failure(check="format", detail="la salida del gateway no es {text, citations}")` sin eco del contenido). `helpers.py` construye `make_ctx(**over)` con `TokenVault("run-0001", FakeKeyProvider.default(), FakeIds())`, `LanguageDetection` sintético (patrón de `tests/m06/helpers.py::make_lang`, detector `lingua@<versión instalada>`), `find_clear_pii=lambda text: []`, `locale="es"`, `number_format` provisional documentado en el helper hasta P1.
- [ ] **Step 4:** `uv run pytest tests/m08 -q && uv run mypy && uv run ruff check .` → PASS.
- [ ] **Step 5: Commit** — `feat(m8): tipos Draft, Failure, ValidationContext y parse_draft` (+ trailers).

---

### Task 4: Comprobación 1 — `format`

**Files:** Modify `agent_core/response/checks.py` (crear); Create `tests/m08/test_check_format.py`.

**Interfaces:** Produces `Check = Callable[[Draft, ValidationContext], list[Failure]]`; `check_format`.

- [ ] **Step 1: Pruebas** — texto vacío o solo espacios → `Failure("format")`; texto válido → `[]`; citas duplicadas se permiten (no es falla); no muta el draft.
- [ ] **Step 2:** correr → FAIL.
- [ ] **Step 3:** implementar `check_format` (solo invariantes del `Draft`; el parseo del gateway es `parse_draft`).
- [ ] **Step 4:** `uv run pytest tests/m08/test_check_format.py -q` → PASS.
- [ ] **Step 5: Commit** — `feat(m8): comprobación de formato`.

---

### Task 5: Comprobación 2 — `citations` (T-M8-05) — requiere P6

**Files:** Modify `agent_core/response/checks.py`; Create `tests/m08/test_check_citations.py`.

**Interfaces:** Produces `check_citations`: por cada cita `c`: falla si `c not in facts_model_view and c not in pages_model_view` (motivo `cita_inexistente`), o si `c not in allowed` (`cita_no_permitida`). El `detail` lleva la cita (un `fact_id`/`page_ref`, no PII) y el motivo; una falla por cita, en el orden del draft. Sin citas: no falla (una respuesta sin cifras puede no citar; las cifras se exigen en `numbers`).

- [ ] **Step 1: Pruebas**
  - **T-M8-05:** cita a un hecho que existe pero no está en `allowed` → `Failure(check="citations")`.
  - cita a un `fact_id` que no existe → falla; cita permitida y existente → ok.
  - `pages_model_view` vacío: una `page_ref` cualquiera falla (`cita_inexistente`) hasta M12.
  - varias citas malas → una `Failure` por cita.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5: Commit** — `feat(m8): comprobación de citas (T-M8-05)`.

---

### Task 6: Comprobación 4 — `tokens_pii` (T-M8-06) — requiere P3

**Files:** Modify `agent_core/response/checks.py`; Create `tests/m08/test_check_tokens_pii.py`.

**Interfaces:** Produces `check_tokens_pii`: (a) todo `TOKEN_RE.findall` del texto debe cumplir `ctx.vault.exists(token)`; token inexistente → `Failure("tokens_pii", "token desconocido: ⟦x:9⟧")` (el token no es PII; nunca imprimir valores del vault); (b) `ctx.find_clear_pii(draft.text)` no vacío → una `Failure` con las **rutas** devueltas (`cliente.document_number`, `pattern:email`), nunca valores. `TOKEN_RE = re.compile(TOKEN_PATTERN)` local (no exportado por M7).

- [ ] **Step 1: Pruebas** (datos sintéticos; usar `ViewService` real de M7 con `FakeKeyProvider`/tabla de `tests/m07/helpers.py` como referencia, o un cierre simulado):
  - **T-M8-06a:** `"Tu documento es ⟦doc:7⟧"` con vault sin ese token → falla; con `vault.tokenize("1023456789", "document_number", "doc")` y el texto usando el token devuelto → ok.
  - **T-M8-06b:** documento en claro (`"tu documento 1023456789"`) con `find_clear_pii=lambda t: service.find_clear_pii(t, facts_full)` (ViewService real y hecho `{"cliente": {"document_number": "1023456789"}}`) → falla con ruta `cliente.document_number`; `detail` no contiene `1023456789`.
  - correo en claro → `pattern:email`.
  - texto sin tokens ni PII → `[]`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5: Commit** — `feat(m8): comprobación de tokens y PII en claro (T-M8-06)`.

---

### Task 7: Comprobación 5 — `language` (T-M8-04) — requiere P3 y P5

**Files:** Modify `agent_core/response/checks.py`; Create `tests/m08/test_check_language.py`.

**Interfaces:** Produces `check_language`: `decision = detect_language(draft.text, ctx.lang_cfg, ctx.lang_thresholds, list(ctx.supported_locales), ctx.locale)`; ok si `decision.decision in {"short", "undetermined"}`; en otro caso ok iff `decision.top2[0][0] == ctx.locale` (semántica D3, a confirmar en P5). El `detail` lleva idiomas (`esperado es, detectado pt`), nunca el texto.

- [ ] **Step 1: Pruebas** (lingua real, mismas frases sintéticas que `tests/m06/test_detect_language.py`: `ES`, `PT`, `EN`):
  - **T-M8-04a:** `PT` con `locale="es"` → falla; `ES` con `locale="es"` → ok; `PT` con `locale="pt"` → ok.
  - **T-M8-04b:** respuesta corta (`"Listo, gracias"`, `"Sí"`) con `locale="pt"` → ok (`short`).
  - `EN` con `locale="es"` → falla (idioma no soportado también rechaza).
  - con `UNCALIBRATED` el resultado es el mismo (regresión de D3: la comprobación no depende de `switch_threshold`).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS (`uv run pytest tests/m08/test_check_language.py -q`).
- [ ] **Step 5: Commit** — `feat(m8): comprobación de idioma con el detector de M6 (T-M8-04)`.

---

### Task 8: Mecánica del parser numérico (sin tabla país→formato)

Independiente de P1/P2: solo mecanismos, con `NumberFormat` explícito. No decide qué formato usa un país ni qué números cuentan.

**Files:** Create `agent_core/response/numbers.py`; Create `tests/m08/test_numbers.py`.

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class NumberFormat:  thousands: str; decimal: str          # p. ej. (".", ",") o (",", ".")

@dataclass(frozen=True)
class Figure:
    raw: str; kind: Literal["amount", "percent", "date"]
    value: Decimal | date | None                # None = ilegible con este formato
    ambiguous: bool                             # p. ej. "1.234" con ambos formatos válidos

def strip_tokens(text: str) -> str              # sustituye cada TOKEN_RE por espacio (T-M8-10)
def scan_figures(text: str, fmt: NumberFormat) -> list[Figure]
```

Reglas de la mecánica (todas con `Decimal(str)`, nunca `float`; usar `re` y `decimal`, sin `float()`):
1. Antes de escanear, `strip_tokens(text)`: los dígitos dentro de `⟦tag:n⟧` no son cifras (T-M8-10).
2. Montos/cantidades: secuencias `\d[\d.,]*\d|\d` con grupos de miles solo si el separador de miles del formato aparece con grupos de 3; decimales con el separador decimal del formato; símbolo de moneda/`%` adyacentes se consumen como parte del `Figure` (`$`, `USD`, `COP`, `MXN`, `ARS`, `%`).
3. Porcentajes: número seguido de `%` → `kind="percent"`, valor en `Decimal` sin dividir (comparación contra el hecho tal cual: `15%` ↔ `15`).
4. Fechas: `dd/mm/yyyy`, `yyyy-mm-dd`, y `dd de <mes> de yyyy` / `dd de <mês> de yyyy` (ES y PT) → `kind="date"` con `datetime.date` (sin `date.today()`).
5. Una lectura `x.yyy` / `x,yyy` con exactamente 3 dígitos tras el separador y un solo separador es **ambigua**: `ambiguous=True` cuando el formato del contexto no basta para decidir (la política de qué hacer con ella es P1; aquí solo se marca).

- [ ] **Step 1: Pruebas (fallan)**

```python
from datetime import date
from decimal import Decimal

from agent_core.response.numbers import NumberFormat, scan_figures, strip_tokens

DOT_COMMA = NumberFormat(thousands=".", decimal=",")   # 1.234,56
COMMA_DOT = NumberFormat(thousands=",", decimal=".")   # 1,234.56


def _values(text: str, fmt: NumberFormat) -> list[object]:
    return [f.value for f in scan_figures(text, fmt)]


def test_amount_in_both_formats_is_the_same_decimal() -> None:
    assert _values("Son $1.234,56 en total", DOT_COMMA) == [Decimal("1234.56")]
    assert _values("Son $1,234.56 en total", COMMA_DOT) == [Decimal("1234.56")]


def test_values_are_decimal_never_float() -> None:
    (fig,) = scan_figures("0,10", DOT_COMMA)
    assert isinstance(fig.value, Decimal) and fig.value == Decimal("0.10")


def test_percent_and_date() -> None:
    (pct,) = scan_figures("una tasa del 15,5%", DOT_COMMA)
    assert pct.kind == "percent" and pct.value == Decimal("15.5")
    assert _values("vence el 03/10/2026", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence el 3 de octubre de 2026", DOT_COMMA) == [date(2026, 10, 3)]
    assert _values("vence em 3 de outubro de 2026", DOT_COMMA) == [date(2026, 10, 3)]


def test_digits_inside_tokens_are_not_figures() -> None:  # T-M8-10
    text = "Tu documento ⟦doc:1⟧ y tu teléfono ⟦tel:23⟧"
    assert strip_tokens(text).count("1") == 0 and scan_figures(text, DOT_COMMA) == []


def test_three_digit_group_is_flagged_ambiguous() -> None:
    (fig,) = scan_figures("1.234", DOT_COMMA)
    assert fig.ambiguous is True


def test_scan_is_pure_and_deterministic() -> None:
    text = "1.234,56 y 2.000,00"
    assert scan_figures(text, DOT_COMMA) == scan_figures(text, DOT_COMMA)
```

- [ ] **Step 2:** `uv run pytest tests/m08/test_numbers.py -q` → FAIL.
- [ ] **Step 3: Implementar** `numbers.py` (nombres de meses ES/PT como tabla `MESES: dict[str, int]`; `TOKEN_RE` local).
- [ ] **Step 4:** PASS + `uv run ruff check . && uv run mypy` (`git grep -n "float(" agent_core/response` debe salir vacío).
- [ ] **Step 5: Commit** — `feat(m8): mecánica del parser numérico con Decimal y fechas ES/PT (T-M8-10)`.

---

### Task 9: Tabla de formatos por locale/país — **BLOQUEADA POR P1**

**No empezar sin la respuesta de P1.** Si el usuario aún no respondió, detente y pregunta de nuevo con el texto de la Task 1 Step 3 (punto 1). No escribas una tabla "provisional" en `agent_core`: solo el helper de pruebas puede usar formatos explícitos.

**Files:** Create `agent_core/response/formats.py`; Create `tests/m08/test_formats.py`.

**Interfaces:**
- Consumes: `NumberFormat` (Task 8).
- Produces (forma dependiente de la respuesta): `NUMBER_FORMATS: Mapping[str, NumberFormat]` con las claves que el usuario apruebe (candidatas: `es-CO`, `es-MX`, `es-AR`, `pt`, o solo `es`/`pt`) y `resolve_number_format(key: str) -> NumberFormat` que lanza `UnknownNumberFormat` para claves ausentes (falla cerrado). Si P1 dice que M8 también calcula la clave desde idioma y país: `number_format_for(locale: str, country: str | None) -> str`, donde el país viene de `principal.attrs.country` o del hecho de identidad **según lo aprobado**; sin país, la política de ambigüedad aprobada.

- [ ] **Step 1: Pruebas** con la tabla aprobada: cada clave resuelve al `NumberFormat` esperado; clave desconocida → `UnknownNumberFormat`; `1.234` sin país sigue la política aprobada (rechazo si la propuesta se acepta).
- [ ] **Step 2:** FAIL. **Step 3:** implementar la tabla como **dato** (un diccionario, sin ramas por país en el código). **Step 4:** PASS.
- [ ] **Step 5:** actualizar el spec §3.3 y el índice §10 (marcar resuelto) con lo aprobado.
- [ ] **Step 6: Commit** — `feat(m8): tabla de formatos numéricos por locale y país (Abierto §11.1)`.

---

### Task 10: Comprobación 3 — `numbers` (T-M8-01, T-M8-02, T-M8-03, T-M8-10) — **BLOQUEADA POR P1 y P2**

**No empezar sin P1 y P2.** El alcance de "qué número cuenta" (P2) define el filtro de `scan_figures`; no se resuelve aquí.

**Files:** Create `agent_core/response/check_numbers.py`; Create `tests/m08/test_check_numbers.py`.

**Interfaces:**
- Consumes: `scan_figures`, `resolve_number_format`, `ctx.facts_model_view`, `ctx.fact_sources`, `Draft.citations`.
- Produces `check_numbers(draft, ctx) -> list[Failure]`, con esta lógica (ADR 0011, sin tolerancia):
  1. `fmt = resolve_number_format(ctx.number_format)` (clave desconocida → `Failure("numbers", "formato numérico desconocido")`, no excepción).
  2. Para cada `Figure` que cuente (P2): si `ambiguous` y el formato no la desambigua → falla (`cifra_ambigua`, medido como falso rechazo en eval; spec §5); si `value is None` → falla (`cifra_ilegible`).
  3. Indexar los valores numéricos de los hechos **citados** (y páginas citadas, vacío hasta M12) recorriendo su `model` view: `Decimal` de números, de cadenas numéricas parseadas con el formato y de fechas ISO. Comparar con `==` exacto sobre `Decimal`; con la "precisión de la moneda" = comparar tras `quantize` al número de decimales del hecho (nunca tolerancia relativa).
  4. Cifra que coincide solo con hechos `source.kind in {"tool","identity"}` → ok; cifra que no aparece en ninguno de ellos pero sí en un hecho `compute` citado → ok (T-M8-02); cifra que no aparece en ningún hecho citado → `Failure("numbers", "cifra sin fuente: <raw>")` (T-M8-03). Una cifra **calculada** solo puede respaldarla un `compute`: si el número coincide con un `tool` pero el texto lo presenta como derivado no se detecta (fuera de alcance: el criterio es de origen del número, no de semántica).
  5. Devuelve una `Failure` por cifra fallida, en orden de aparición. `detail` lleva el `raw` de la cifra (dato de negocio, no PII).

- [ ] **Step 1: Pruebas (con la tabla aprobada en P1)**
  - **T-M8-01:** el hecho `{"monto": "1234.56"}` (`tool`) se cita; `"Tu cargo es de $1.234,56"` pasa con la clave del formato punto/coma y `"Tu cargo es de $1,234.56"` pasa con la del coma/punto (las claves concretas, p. ej. `es-CO` y `es-MX`, son las de P1) contra **el mismo** hecho.
  - **T-M8-02:** hecho `tool` `{"monto_usd": "250.00"}` y hecho `compute` `{"monto_cop": "1000000.00"}` (`FactSource(kind="compute", ref="convertir_moneda@1.0.0", inputs=[fact_id_tool])`), ambos citados; el texto con `250,00` USD y `1.000.000,00` COP pasa.
  - **T-M8-03:** el texto afirma `1.000.000,00` pero solo se cita el hecho `tool` en USD (sin el `compute`) → `Failure(check="numbers")`.
  - **T-M8-10:** `"Tu documento ⟦doc:1⟧"` y `"⟦tel:23⟧"` no producen `Failure` (dígitos de tokens ignorados) aunque no haya hechos.
  - Cifra ambigua `1.234` sin contexto → rechazo, según P1.
  - "paso 2" / "24 horas" → según P2 (el caso de prueba se escribe con la decisión aprobada; nunca antes).
  - Propiedad (hypothesis): para todo `Decimal` con 2 decimales, formatear con el formato y parsear devuelve el mismo `Decimal` (`round-trip`), sin `float`.
  - Sin tolerancia: `1234.57` no respalda `1.234,56`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** `uv run pytest tests/m08 -q` → PASS.
- [ ] **Step 5: Commit** — `feat(m8): comprobación numérica exacta con hechos compute (T-M8-01/02/03/10)`.

---

### Task 11: `validate` y la lista ordenada `CHECKS`

Requiere las Tasks 4–7 y 10.

**Files:** Create `agent_core/response/validate.py`; Modify `agent_core/response/checks.py` (registrar `CHECKS`); Create `tests/m08/test_validate.py`.

**Interfaces:** Produces `CHECKS: tuple[tuple[CheckId, Check], ...]` en el orden de la tabla anterior y `validate(draft: Draft, ctx: ValidationContext) -> ValidationResult` (ejecuta **todas** y concatena fallas en orden de `CHECKS`; `ok = not failures`).

- [ ] **Step 1: Pruebas**
  - un draft con cita no permitida + cifra sin fuente + token inexistente + idioma equivocado devuelve las cuatro `Failure` en el orden `citations, numbers, tokens_pii, language`.
  - **Pureza/determinismo:** `validate(d, ctx) == validate(d, ctx)`; no muta `d` (comparar `model_dump` antes/después) ni `ctx.facts_model_view`; ni `Clock` ni `IdSource` participan (el módulo no importa `datetime`/`random`: `git grep -nE "datetime|random|secrets|uuid|time\." agent_core/response/validate.py agent_core/response/checks.py agent_core/response/numbers.py` vacío).
  - extensibilidad: añadir una comprobación falsa a una copia de `CHECKS` la ejecuta en su posición (verifica la lista ordenada; no modifica `CHECKS` global).
  - `Failure.check` cubre solo los cinco IDs del spec.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS.
- [ ] **Step 5: Commit** — `feat(m8): validate con las cinco comprobaciones en orden`.

---

### Task 12: `Responder.template` — requiere P7

**Files:** Create `agent_core/response/templates.py`; Create `agent_core/response/responder.py` (parte 1); Create `tests/m08/test_template.py`.

**Interfaces:**
- Consumes: `RegistryPort.get(ref, Template)`, `Template.locales`, `Message`.
- Produces: `TemplateUnavailable(Exception)` (locale ausente en la plantilla, ruta de hecho ausente, `{{` malformado); `render_template_text(text, facts_model_view_by_name) -> str` (solo `{{ facts.<n>.value(.<campo>)* }}` como M1 §3.3; valores `Decimal`/`str`/`int` a texto; nunca `float`); `ResponderContext` (dataclass congelado, `repr=False`) con `registry: RegistryPort`, `gateway: LLMGateway`, `clock: Clock`, `ids: IdSource`, `resolve_ref: Callable[[EntityKind, RefSpec], EntityRef]` (M2 inyecta `exact_ref`; M8 no importa `flows`), `locale`, `degraded: bool`, `release: str`, `turn_id: str | None`, `default_target_queue: str`, `priority: str`, `facts_model_view_by_name: Mapping[str, JsonValue]`, `validation: ValidationContext` (construido por quien llama con hechos ya en vista `model`), `claims: frozenset[str]`, `max_regenerations: int = 1` (parámetro §9), y `Responder(ctx_defaults…)` con `template(template_ref, locale, facts_model_view) -> Message` (`kind="template"`, `locale` el pedido).
- **Nota:** las plantillas no pasan por las comprobaciones 2 y 3 pero **sí por la 4** (spec §3.2): `Responder.template` no valida; la cadena (Task 14) aplica `check_tokens_pii` al texto renderizado de una plantilla.

- [ ] **Step 1: Pruebas**
  - plantilla `"Tu disputa {{ facts.pqr.value.id }} quedó radicada."` con `{"pqr": {"value": {"id": "pqr-1"}}}` renderiza `"Tu disputa pqr-1 quedó radicada."`, `Message.kind == "template"`.
  - locale ausente en la plantilla → `TemplateUnavailable`; ruta ausente → `TemplateUnavailable`; `Decimal("1234.56")` se renderiza sin pasar por `float`.
  - `{{ slots.x }}`/`{{ decisions.x }}` no se admiten en M8 (`TemplateUnavailable`): la plantilla de respaldo solo lee hechos (M1 §3.3 / `Template.reads`). Si una plantilla de la release usa otras rutas, es un hallazgo → detente y pregunta.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS. **Step 5: Commit** — `feat(m8): Responder.template con render mínimo de hechos`.

---

### Task 13: Acumulador de uso del LLM (`UsageMeter`) — T-M8-11 (parte)

**Files:** Create `agent_core/response/usage.py`; Create `tests/m08/test_usage.py`.

**Interfaces:**
- Consumes: `Clock.monotonic_ns`, `GenerationResult`, `GatewayError`, `LlmUsage`.
- Produces: `UsageMeter(clock)` con `call(fn: Callable[[], GenerationResult]) -> GenerationResult` que mide con `clock.monotonic_ns()` antes/después (latencia = `(t1 - t0) // 1_000_000` ms, entero), acumula `calls += 1`, `tokens_in/out`, `cost_usd` (`Decimal`) y `models` (sin duplicados, en orden); si `fn` lanza `GatewayError`, acumula la llamada y el uso parcial informado (`tokens_in/out`, `cost_usd`, `model` si no son `None`) y marca `cost_known = False` cuando `cost_usd is None`; relanza el error. `usage() -> LlmUsage | None` (`None` si `calls == 0`, spec §3.2).

- [ ] **Step 1: Pruebas** (con `FakeClock.advance`):
  - dos llamadas de 150 ms y 250 ms con tokens `(10,5)` y `(20,8)` y costos `0.001` y `0.002` → `LlmUsage(calls=2, latency_ms=400, tokens_in=30, tokens_out=13, cost_usd=Decimal("0.003"), cost_known=True, models=["scripted-1"])`.
  - `GatewayError` sin uso parcial: `calls == 1`, `cost_known is False`, relanza.
  - sin llamadas → `usage() is None`.
  - `cost_usd` es `Decimal` (nunca `float`); `git grep -n "float(" agent_core/response` vacío.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS. **Step 5: Commit** — `feat(m8): UsageMeter para llm.calls, tokens, costo y latencia`.

---

### Task 14: Cadena `Responder.generate` y evento `response_emitted` (T-M8-07, 08, 09, 11) — requiere P4, P6

**Files:** Modify `agent_core/response/responder.py`; Create `tests/m08/test_responder_generate.py`, `tests/m08/test_events.py`.

**Interfaces:**
- Consumes: todo lo anterior + `ScriptedGateway`, `InMemoryRegistry`, `FakeClock`, `FakeIds`.
- Produces: `Responder.generate(node_config: GenerateConfig, state: RunState, ctx: ResponderContext) -> tuple[Message | EscalationRequest, list[RejectedDraft], list[EngineEvent]]` (firma del spec; forma final según P4) con el algoritmo:

```
if ctx.degraded:                                   # T-M8-08: sin llamar al gateway
    render fallback -> emitir response_emitted(kind="template", fallback_used=True, llm=None)
prompt = ctx.resolve_ref(EntityKind.prompt, node_config.prompt_ref)
allowed = {state.facts[n].fact_id for n in nombres(node_config.allowed_facts)}     # D4
for intento in range(1 + ctx.max_regenerations):
    inputs = vista model (+ motivos del rechazo anterior en `inputs["validation_feedback"]`, solo ids de check y detail)
    try: result = meter.call(lambda: gateway.generate(prompt, inputs, ctx.locale, DRAFT_SCHEMA))
    except GatewayError: break                       # "Gateway caído → plantilla" (§5); no se reintenta
    parsed = parse_draft(result.output)
    outcome = Failure(format) si no parsea, si no validate(draft, ctx.validation)
    if outcome.ok: emitir response_emitted(kind="generated", validator={ok, failures, regenerations=intento}, fallback_used=False, llm=meter.usage()) ; return Message(kind="generated", text, locale)
    rejected.append(RejectedDraft(text_model=texto, reason="; ".join(check ids + detail), failures=[check ids]))
# rechazos agotados o gateway caído:
try: message = self.template(fallback_template_ref…) ; aplicar check_tokens_pii (comprobación 4)
except TemplateUnavailable | tokens_pii falla: return EscalationRequest("validation_failed", ctx.default_target_queue, ctx.priority)   # T-M8-07
emitir response_emitted(kind="template", fallback_used=True, validator={ok=False,…}, llm=meter.usage())
```

Reglas: `DRAFT_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}, "required": ["text", "citations"], "additionalProperties": False}`; los hechos van al gateway **solo en vista `model`** (`ctx.facts_model_view_by_name`, ya proyectada); `response_emitted` se arma como `ResponseEmitted.model_validate({event_id: ids.new_id(IdKind.event), run_id: state.run_id, turn_id, session_id, release, ts: clock.now(), payload})` (mismo patrón de `interpreter/events.py`), con `claims = sorted(ctx.claims)` (los de `derive_claims`, M1, que M2 entrega) y `transcript_fp=None` (lo agrega M11); `EscalationRequest` no lleva evento `escalated` (lo emite M10). Ningún `reason`/`detail` contiene valores `full`.

- [ ] **Step 1: Pruebas (fallan)** — `tests/m08/test_responder_generate.py`, con `ScriptedGateway`, `InMemoryRegistry` (plantilla de respaldo y prompt sintéticos, patrón de `tests/m02/test_respond_generate.py`), `FakeClock`, `FakeIds`:
  - **T-M8-07a:** gateway guionado con dos borradores inválidos (cita no permitida) → resultado `Message(kind="template")`, `fallback_used=True`, `validator.regenerations == 1`, `gateway.calls` de longitud 2, el segundo request incluye `validation_feedback` con el id `citations`.
  - **T-M8-07b:** dos rechazos **y** plantilla imposible (locale ausente / ruta ausente) → `EscalationRequest(reason_code="validation_failed", target_queue="general", priority="normal")`.
  - **T-M8-08:** `degraded=True` → `gateway.calls == []`, mensaje de plantilla, evento con `llm is None`, `kind="template"`, `fallback_used=True`.
  - **T-M8-09:** los dos borradores rechazados salen como `list[RejectedDraft]` con `reason` (ids de comprobación) y `failures`; `text_model` es el texto del borrador (ya vista `model`: solo tokens, sin PII).
  - **T-M8-11a:** generación rechazada + regeneración rechazada + plantilla → `llm.calls == 2`, `tokens_in`/`tokens_out`/`cost_usd` = suma de las dos, `latency_ms` = suma de las latencias medidas con `FakeClock` (el gateway guionado avanza el reloj), `models == ["scripted-1"]`.
  - **T-M8-11b:** modo degradado → `llm is None` (ya cubierto en T-M8-08; aquí se afirma sobre el payload).
  - primer borrador válido → `kind="generated"`, `regenerations == 0`, `llm.calls == 1`, `fallback_used=False`.
  - primer borrador inválido, segundo válido → `generated`, `regenerations == 1`, un `RejectedDraft`.
  - `GatewayError` en el primer intento → plantilla de respaldo directa (§5), sin regeneración, `llm.calls == 1`, `cost_known=False` si el error no traía costo.
  - salida del gateway que no parsea (`{"foo": 1}`) → falla `format`, cuenta como rechazo (consume la regeneración).
  - plantilla de respaldo con PII en claro (comprobación 4 sobre plantilla) → `validation_failed`.
  - determinismo: dos corridas con los mismos puertos y `FakeClock`/`FakeIds` sembrados producen eventos idénticos (`==`), regla dura 3.
  - `git grep` de reglas duras: ninguna lectura de hora/aleatoriedad en `agent_core/response`.
- [ ] **Step 2:** `uv run pytest tests/m08/test_responder_generate.py -q` → FAIL.
- [ ] **Step 3:** Implementar. La respuesta de P4 decide si `generate` devuelve la tupla del spec (propuesta) o el `GenerateResult` de M2; en ambos casos M8 **no** importa `agent_core.interpreter` (`uv run lint-imports`).
- [ ] **Step 4:** `uv run pytest tests/m08 tests/contracts -q && uv run lint-imports` → PASS.
- [ ] **Step 5: Commit** — `feat(m8): cadena respond(generate) con regeneración, plantilla y escalamiento (T-M8-07/08/09/11)`.

---

### Task 15: Conjunto etiquetado de 30+ respuestas ES/PT y falsos rechazos — requiere P1 y P2

Definición de terminado exige "30+ respuestas etiquetadas (ES y PT) para medir falsos rechazos". Datos **sintéticos**, etiquetados como tales; los datos PT los produce otro equipo (ADR 0012: no es responsabilidad del núcleo), así que el conjunto inicial de PT es una traducción sintética marcada `synthetic=True`.

**Files:** Create `tests/m08/labeled_set.py`; Create `tests/m08/test_labeled_set.py`.

**Interfaces:** Produces `LabeledCase(id, locale, text, citations, facts, expected_ok: bool, expected_checks: list[CheckId], synthetic: True)`; `LABELED: list[LabeledCase]` con **≥ 30 casos**: ≥ 12 ES correctas, ≥ 12 PT correctas (mismas frases traducidas), y ≥ 6 incorrectas (una por comprobación y combinaciones) para medir también la fuga. Cubre montos en ambos formatos, porcentajes, fechas, conversiones `compute`, respuestas cortas, tokens.

- [ ] **Step 1: Pruebas**
  - todo caso `expected_ok=True` pasa `validate` (falsos rechazos = 0 en el conjunto inicial) y todo caso incorrecto falla con exactamente sus `expected_checks`.
  - reporte por idioma y por comprobación: `false_reject_rate` calculado y afirmado ≤ el umbral que fije el usuario (si no lo fija: se **reporta** en la salida de la prueba, no se relaja ninguna regla; spec §11.2 "se mide el falso rechazo antes de relajar").
  - `len(LABELED) >= 30`, `{c.locale for c in LABELED} == {"es", "pt"}` y todos `synthetic`.
  - prueba de higiene: ningún caso contiene datos reales (dominio `example.test`, documentos de 10 dígitos inventados con prefijo de prueba).
- [ ] **Step 2:** FAIL. **Step 3:** escribir los casos (con el formato aprobado en P1). **Step 4:** PASS.
- [ ] **Step 5: Commit** — `test(m8): conjunto etiquetado de 30+ respuestas ES/PT para falsos rechazos`.

---

### Task 16: Interfaz pública, trazabilidad, cierre y Definición de terminado

**Files:** Modify `agent_core/response/__init__.py`; Create `tests/m08/test_public_api.py`, `tests/m08/test_traceability.py`; Modify `docs/specs/motor/m08-validador-de-respuesta.md` (§10 con casillas marcadas y LOC), `docs/specs/motor/00-indice.md` (si el spec lo pide).

**Interfaces:** Produces `agent_core.response.__all__ = ["CHECKS", "Draft", "Failure", "NumberFormat", "Responder", "ResponderContext", "TemplateUnavailable", "ValidationContext", "ValidationResult", "parse_draft", "validate"]` (más lo que el spec rev. 2 fije). `ScriptedGateway` vive en `testing/fakes/gateway.py`, no en el paquete.

- [ ] **Step 1: Pruebas**
  - `test_public_api.py`: cada símbolo de `__all__` importable; `agent_core.response` no importa `agent_core.interpreter`, `flows`, `actions`, `turn`, `handoff`, `audit`, `decision`, `adapters`, `registry` (revisar `sys.modules` tras importar en un subproceso limpio o con `importlib`; espejo del contrato `response`).
  - `test_traceability.py` (patrón de `tests/m07/test_traceability.py`): cada `T-M8-01…11` aparece nombrado en algún `tests/m08/test_*.py`.
- [ ] **Step 2:** FAIL. **Step 3:** implementar `__init__.py`. **Step 4:** PASS.
- [ ] **Step 5: Correr todas las comprobaciones del repo**

Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo en verde; `lint-imports` con el contrato `M8 (response)` en `KEPT`; `contracts --check` sin cambios (M8 no toca tipos de M0; si cambia, es un fallo del plan: revertir y preguntar).

- [ ] **Step 6: Commit** — `feat(m8): interfaz pública de agent_core.response y trazabilidad de T-M8`.

- [ ] **Step 7: Marcar la Definición de terminado punto por punto (spec §10) y reportar al usuario**

- [ ] `validate` con las cinco comprobaciones (`format`, `citations`, `numbers`, `tokens_pii`, `language`) y la cadena de `generate` con **T-M8-01…11 en verde** (`uv run pytest tests/m08`).
- [ ] T-M8-01 (Task 10) · T-M8-02 (Task 10) · T-M8-03 (Task 10) · T-M8-04 (Task 7) · T-M8-05 (Task 5) · T-M8-06 (Task 6) · T-M8-07 (Task 14) · T-M8-08 (Task 14) · T-M8-09 (Task 14) · T-M8-10 (Tasks 8 y 10) · T-M8-11 (Tasks 13 y 14).
- [ ] Conjunto de 30+ respuestas etiquetadas (ES y PT) y falsos rechazos reportados por idioma y comprobación (Task 15).
- [ ] `ScriptedGateway` y su suite de contrato en `tests/contracts/test_gateway_contract.py` (Task 2); `RequestCapture` conectado (cierra el pendiente de M7: reportarlo).
- [ ] `pages_model_view` vacío hasta M12 (D7) y sin importar `agent_core.knowledge`.
- [ ] Interfaz pública exportada y tipada (`mypy` strict), `import-linter` en verde, `ruff` en verde.
- [ ] Reglas duras verificadas: `git grep -nE "datetime\.now|time\.time|uuid4|random|secrets|float\(|json\.loads" -- agent_core/response` vacío; sin PII en `Failure.detail`/`RejectedDraft.reason`/eventos.
- [ ] Abiertos §11 marcados como resueltos con la decisión del usuario (P1, P2), o dejados abiertos y avisado si no se respondieron.
- [ ] Spec actualizado a rev. 2 en el mismo cambio (CLAUDE.md: "si cambia el comportamiento acordado, actualiza el spec").
- [ ] `git grep -n TODO -- agent_core/response testing/fakes/gateway.py tests/m08` vacío.
- [ ] **Pendientes fuera de M8 que el usuario debe conocer:** (1) adaptador `Responder → ResponderPort` y lectura de `model_calls/tokens/cost_usd` desde `response_emitted.llm` (M2 o cableado de M4; P4); (2) quién emite `response_emitted` para `respond(template_ref)` directo (M2 D6, D9); (3) construcción de `ValidationContext` (proyección `model` de hechos, `fact_sources`, `number_format`, cierre `find_clear_pii`) por quien cablee M2/M4; (4) M12: `PageView`, `knowledge_from`/`purpose` y su comprobación de `page_citations` (spec M12 §6) cambiarán `pages_model_view`.
