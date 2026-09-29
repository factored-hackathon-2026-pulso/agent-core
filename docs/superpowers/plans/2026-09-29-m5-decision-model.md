# M5 — DecisionModel y Understand: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PARADAS:** las secciones "Puntos de PARADA" y las Tasks marcadas `PARADA` exigen preguntar al usuario antes de seguir (CLAUDE.md: "Si el spec es ambiguo, contradice un ADR o toca un Abierto: detente y pregunta"). Este plan **no resuelve** ninguna; solo propone una opción por defecto para que el usuario la confirme o cambie.

**Goal:** Construir `agent_core.decision`: `DecisionService.decide` (cadena de proveedores con fallback, reintento por esquema, calibración, tabla de umbrales, validación de tokens, evento `decision_made`), `UnderstandService.run`, los proveedores `rule`, `classifier`, `llm_structured` y `jev` (adaptador sobre transporte inyectable), `ScriptedProvider`, el artefacto de calibración con `calibrate` determinista y el informe de métricas por comando, con T-M5-01…10 en verde.

**Architecture:** M5 solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de `agent_core.views` (contrato `decision` de `.importlinter`, ya presente; **no** puede importar `agent_core.interpreter`, así que el adaptador hacia `DecisionPort` de M2 no vive aquí, ver Discrepancia D1). La regla de negocio es dato (`DecisionModelDef`, `CalibrationArtifact`); el código solo trae mecanismos. Un archivo por responsabilidad: tipos, validación de esquema, calibración (artefacto, isotónica, umbrales, métricas), un archivo por proveedor, servicio de decisión y servicio de Understand.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib (`urllib` solo en la PARADA de JEV), `rfc8785` vía `canonical_bytes`/`dumps`/`loads` de M0, pytest + hypothesis, ruff, mypy strict, import-linter. **Sin dependencias nuevas** (isotónica/PAV, TF-IDF+LR en runtime y métricas en Python puro; ver PARADA P4 si el usuario prefiere `scikit-learn`).

**Spec:** `docs/specs/motor/m05-decision-model.md` (manda sobre este plan). Contexto: `docs/specs/motor/00-indice.md` (§3, §4, §6, §10), ADR 0005 (contrato, JEV, campos calibrados, tabla de umbrales), ADR 0012 (calibración por idioma, datos PT sintéticos de otro equipo), ADR 0008 (vistas), spec general §4.4 y §7, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` §12 (política de `recent_turns`). Léelos antes de empezar.

## Global Constraints

- Python `>=3.12,<3.13`. Sin colas, Redis ni vector DB (ADR 0001).
- `agent_core.decision` solo importa `agent_core.domain`, `agent_core.ports` y `agent_core.views` (interfaz pública, `__init__.py`). Nunca internos de `views` (`agent_core.views.vault`, etc.). `uv run lint-imports`.
- Nunca `datetime.now()`, `time.time()`, `time.perf_counter()`, `uuid4()`, `random`, `secrets`, `os.urandom`: la hora y las duraciones salen de `Clock` (`now`, `monotonic_ns`), los IDs de `IdSource.new_id(IdKind.decision | IdKind.event)` (lo verifica `ruff` TID251).
- Determinismo: mismos puertos + mismo `Clock` ⇒ mismos eventos. Los desempates (orden de proveedores, orden de claves, empates de umbral) son por orden estable; `decision_made.latency_ms` es campo de medición (`MEASURED_FIELDS`) y se excluye al comparar réplicas.
- Dinero y cifras: `cost_usd` es `Decimal`, nunca `float`. Las probabilidades son `float` en `[0, 1]` porque así las fija M0 (`Probability`, `Decision.p_cal`); ninguna se compara con `==` en pruebas (usa `pytest.approx`).
- Todo JSON entra con `agent_core.domain.loads`; salida y hashes con `dumps`/`canonical_bytes`/`sha256_hex` (M0).
- Datos: fixtures y pruebas solo sintéticos (textos inventados, dominio `example.test`, documentos inventados). Nunca datos reales del dataset ni credenciales del diccionario de datos. Los datos PT son sintéticos y se marcan `synthetic=True`.
- PII: la entrada a cualquier proveedor externo (`jev`, `llm_structured`) está en vista `model`; nada `full` en eventos, logs ni `repr`. Nunca se registra razonamiento del modelo, solo la salida tipada. La API key de JEV entra por un callable inyectado, nunca en repo, evento ni `repr`.
- Solo los campos de `calibrated_fields` tienen `above_threshold`. Ningún umbral se edita a mano: todo sale de un `CalibrationArtifact` con `split_hash`.
- No tocar otros módulos. Si hace falta algo fuera de una interfaz pública, detente y pregunta. Excepciones acordadas con el usuario (ninguna todavía): ver PARADAS P1, P2, P8.
- Comandos: `uv run pytest tests/m05`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`.
- `ruff`: `line-length = 110`. Si solo marca `I001` o `RUF022`, corrige con `uv run ruff check --fix`; cualquier otro hallazgo se corrige a mano sin cambiar la semántica del plan.
- Commits en español, prefijo `feat(m5):` / `test(m5):` / `docs(m5):`, y terminan exactamente con:

```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof
```

## Interfaces consumidas (verificadas contra el código de la rama `claude/m5-m8-m4-parallel-8z4wxx`)

**M0 `agent_core.domain`** (todo exportado desde `agent_core.domain`):

| Símbolo | Forma real |
|---|---|
| `DecisionModelDef` | `id: EntityId`, `version: ExactVersion`, `output_schema: dict[str, JsonValue]`, `calibrated_fields: list[str]`, `input_view: list[str] = []`, `providers: list[ProviderSpec]` (min 1), `calibration: CalibrationRef`, `thresholds_from: str \| None` |
| `ProviderSpec` | `provider: Literal["jev","classifier","llm_structured","rule"]`, `config: dict[str, JsonValue] = {}` |
| `CalibrationRef` | `method: Literal["none","isotonic","platt","temperature"]`, `run: str \| None` |
| `Decision` | `decision_id: str`, `value: dict[str, JsonValue]`, `p_cal: dict[str, Probability \| None]`, `provider_used: str`, `model_version: str` (no lleva `above_threshold`) |
| `DecisionMadePayload` / `DecisionMade` | payload: `decision_id, model: EntityRef, provider_used, model_version, fallback_depth: NonNegativeInt, value, p_cal, p_raw: dict[str, Probability\|None], top_k: dict[str, list[LabelScore]], above_threshold: dict[str, bool], latency_ms: NonNegativeInt, tokens: NonNegativeInt, cost_usd: Cost(Decimal>=0), locale`. Evento: `type = "decision_made"`, hereda de `EngineEvent` |
| `EngineEvent` | `event_id, run_id, turn_id \| None, session_id \| None, release, ts: UtcDatetime, seq/prev_hash/hash = None` (los asigna M11) |
| `LabelScore` | `label: str`, `p: Probability` |
| `EVENT_EMITTERS["decision_made"]` | `{"M5"}`; `MEASURED_FIELDS["decision_made"] = {"latency_ms"}` |
| `Command` (StrEnum) | `start_flow, continue_ ("continue"), affirm, deny, clarify, cancel, handoff, out_of_scope, interrupt` |
| `EntityRef` | `id`, `version`; `EntityRef.parse("id@1.0.0")`; `Locale = ^[a-z]{2}$`; `Probability` (float 0..1, sin NaN) |
| `TranscriptEntry` | `run_id, turn_id, role: user\|assistant\|rejected_draft, text_model: str, reason` |
| `loads`, `dumps`, `canonical_bytes`, `sha256_hex`, `JsonValue`, `Model` | helpers de JSON canónico |

**M0 `agent_core.ports`:** `Clock.now() -> UtcDatetime`, `Clock.monotonic_ns() -> int`; `IdSource.new_id(kind: IdKind) -> str` (`IdKind.decision`, `IdKind.event`), `IdSource.secret_token()`; `RegistryPort.get[T](ref: EntityRef, kind: type[T]) -> T`; `LLMGateway.generate(prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale, schema: dict[str, JsonValue] | None = None) -> GenerationResult(output, tokens_in, tokens_out, cost_usd: Decimal, model)`, falla con `GatewayError` (M0 §2.11); `TranscriptStore.recent_turns(run_id, n) -> list[TranscriptEntry]`.

**M7 `agent_core.views`** (exportado en `__all__`: `TOKEN_PATTERN`, `TokenVault`, `TokenEntry`, `ViewService`, `FieldClassifier`, `DEFAULT_CATALOG`, `FieldRule`, `Views`, …): M5 usa **solo** `TOKEN_PATTERN` (`⟦([a-z]{1,12}):([1-9][0-9]*)⟧`) y `TokenVault.exists(token: str) -> bool` (el `token` es el texto completo `⟦doc:1⟧`). Para pruebas T-M5-10 usa `ViewService.project(data_full, source, untrusted_fields, vault) -> Views(.model)` y `TokenVault(run_id, keys, ids)` como en `tests/m02/harness.py`. M7 no expone `TOKEN_RE` públicamente: compila `re.compile(TOKEN_PATTERN)` en M5.

**Utilidades de prueba existentes (reusar, no duplicar):** `testing/fakes/{clock,ids,keys,registry,decision}.py` (`FakeClock.advance(timedelta)` mueve `now()` y `monotonic_ns()`; `FakeIds` da `<kind>-0001`; `FakeKeyProvider.default()`; `InMemoryRegistry`), `testing/builders.py` (`NOW`, `run_state`, `principal`), `testing/capture.py` (`RequestCapture.record(payload)`, `.leaks(clear_values) -> list[tuple[int,int]]`, "la conectan `ScriptedGateway` (M8) y el adaptador de JEV (M5)"), `tests/m07/helpers.py` (catálogo sintético). `testing/fakes/decision.py::ScriptedDecision` es el doble del `DecisionPort` de M2; **no se toca** (M5 entrega `ScriptedProvider`, otro doble, en `testing/fakes/provider.py`).

**Consumidor M2 (solo lectura, no se toca):** `agent_core.interpreter.DecisionPort.decide(model: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale) -> DecisionResult(decision, above_threshold, events, model_calls=1, tokens, cost_usd)`; `handle_decide` hace `above = result.above_threshold.get(branch_on, not calibrated)`. `StepContext` ya trae `vault: TokenVault`, `ids`, `clock`, `registry`, `turn_id`. **Consumidor M4** (`m04 §3.1.8`): `understand.run(model_view_text, state, locale) -> UnderstandResult`, con `p_cal` y `below_threshold`.

## Discrepancias spec / código (a confirmar; no se resuelven aquí)

- **D1 — `DecisionPort` de M2 vs `decide` de M5.** M2 declara `decide(model, inputs, locale) -> DecisionResult` (sin `token_vault`, y `DecisionResult` vive en `agent_core.interpreter`). M5 spec §2: `decide(model_ref, inputs_model_view, locale, token_vault) -> (DecisionOutput, EngineEvent)`. `decision` **no puede** importar `interpreter` (`.importlinter`), y M2 sí puede importar `decision`. El adaptador `DecisionOutput → DecisionResult` (con `vault = ctx.vault`) debe vivir en M2 o en la raíz de composición de M4. Este plan solo entrega `DecisionOutput.as_decision() -> Decision` y campos suficientes (`model_calls`, `tokens`, `cost_usd`). → PARADA P1.
- **D2 — Contexto del evento.** `DecisionMade` exige `run_id`, `release`, `ts`, `event_id` (y opcionales `turn_id`, `session_id`), pero `decide` del spec no recibe run ni release. Propuesta: parámetro keyword-only `scope: EventScope(run_id, release, turn_id, session_id)`. → PARADA P2.
- **D3 — `UnderstandContext` no está definido** en ningún documento (`UnderstandResult` tampoco en M0) y M4 llama `run(model_view_text, state, locale)` con `state`, no con `context`. Además `UnderstandResult` del spec no trae `p_cal`, que M4 §3.2 lee, y M4 lo llama `below_threshold` mientras M5 lo llama `above_threshold`. → PARADA P3.
- **D4 — Vault en Understand.** `UnderstandService.run` no recibe `token_vault`, pero los slots pueden traer tokens y §3.1.5 exige validarlos. Propuesta: campo `token_vault` (y `scope`) dentro de `UnderstandContext`. → PARADA P3.
- **D5 — Umbral por recall.** §3.4.3 dice "umbral **mínimo** que cumple el objetivo" para precisión y recall. Con recall, subir el umbral solo baja el recall, así que el mínimo que cumple es trivialmente el más bajo (cualquiera) y el criterio útil es el **máximo** umbral con recall ≥ objetivo. → PARADA P5.
- **D6 — Origen del artefacto.** `DecisionModelDef.calibration.run` y `thresholds_from` son IDs de corrida, pero no existe puerto para leer artefactos (M0 no lo define). → PARADA P6.
- **D7 — `classifier`.** Spec: "artefacto entrenado (ref + hash de datos) que produce el científico de datos; TF-IDF + regresión logística". Formato del artefacto y librería de runtime no están fijados; el repo no tiene `scikit-learn`. → PARADA P4.
- **D8 — `rule` provider.** El spec solo dice "p ∈ {0, 1}"; no define su `config`. Este plan propone tabla de casos (Task 9) para confirmar. → PARADA P7 (menor).
- **D9 — Registro de intents.** M2 `DecisionResult.model_calls` (default 1): una decisión con reintentos o fallback consume más de una llamada a modelo. El spec de M5 no lo expone. Propuesta: `DecisionOutput.model_calls`. Se incluye en P1.

## Puntos de PARADA (requieren preguntar al usuario; no resolver)

| # | Punto | Origen | Opción por defecto propuesta (solo para confirmar) | Bloquea |
|---|---|---|---|---|
| P0 | **Prueba de humo de JEV** (50 ES + 50 PT: precisión, latencia desde nuestro entorno, cobertura de idioma; bloqueante antes del miércoles 30/09): resultado y quién la corre | spec §11 Abierto 1, ADR 0005 | No conectar JEV a ninguna cadena hasta tener el resultado. Se construye el adaptador contra un transporte inyectable y se prueba con doble (T-M5-10) | Task 15 |
| P0b | **Formato real del request/response de JEV** y dónde procesa los datos (no hay doc en el repo; ADR 0005: "no se ha verificado") | ADR 0005 | Forma provisional interna documentada; no se firma contrato real | Task 15 |
| P0c | **Mínimo de muestra PT** por campo/valor (lo fija la unidad 6) | spec §11 Abierto 2, ADR 0012 | `calibrate` exige el parámetro `min_samples` sin valor por defecto | Task 12 (valor real), Task 16 |
| P1 | Quién escribe el adaptador `DecisionService → DecisionPort` (D1, D9) y cómo entra el `vault` | D1 | Vive en M2 (`agent_core/interpreter`) o en composición de M4; M5 no toca M2 | Integración, no Tasks 1–14 |
| P2 | `EventScope` para armar `DecisionMade` (D2) | D2 | `decide(..., token_vault, *, scope)` con `EventScope` en la interfaz pública; actualizar spec rev. 2 | Task 5 |
| P3 | Forma de `UnderstandContext`, `UnderstandResult` (`p_cal`, nombre `above_threshold`), entrada `run(...)` (D3, D4) y política de `recent_turns` (n fijo, ver TEMAS-ABIERTOS §12) | D3/D4 | `UnderstandContext(model_ref, flows, interrupts, current_node, confirm_pending, recent_turns, token_vault, scope)`; `UnderstandResult` agrega `p_cal`; `recent_turns` lo arma M4 con `n` fijo | Task 11 |
| P4 | Formato del artefacto `classifier` y librería de runtime (D7) | D7 | JSON propio `tfidf-logreg-v1` evaluado en Python puro (sin `scikit-learn`); quién lo entrena/exporta es el científico de datos | Task 9 (formato) |
| P5 | Regla de umbral para `recall` (D5) y soporte mínimo por `(field, value)` | D5 | Máximo umbral con recall ≥ objetivo; soporte mínimo como parámetro explícito | Task 12 |
| P6 | Dónde vive el `CalibrationArtifact` y cómo se referencia por `run` (D6) | D6 | Protocolo interno `CalibrationSource.get(run_id)` + implementaciones en memoria y directorio; no es puerto de M0 | Task 6 |
| P7 | `config` del proveedor `rule` (D8) | D8 | `{"cases": [{"when": {"path", "equals"}, "value"}], "default"}` | Task 9 |
| P8 | Comando del informe: `python -m agent_core.decision report` (no toca `cli.py`) o subcomando en `agentcore` (toca `agent_core/cli.py`, otro módulo) | DoD "por un comando" | `python -m agent_core.decision report ...` | Task 13 |
| P9 | Datos etiquetados reales de Understand ES (y PT) para el artefacto real de DoD | spec §10 | Los produce otro equipo; este plan entrega herramienta + fixtures sintéticos | Task 16 |

Antes de empezar la Task 5 confirma con el usuario P2; antes de la Task 9, P4 y P7; antes de la Task 11, P3; antes de la Task 12, P5 y P0c; antes de la Task 15, P0 y P0b. Las Tasks 1–4 no dependen de ninguna.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `docs/specs/motor/m05-decision-model.md` | spec rev. 2 con lo que el usuario confirme en P1–P8 |
| `docs/specs/motor/00-indice.md` | §10: estado de los Abiertos de M5 |
| `agent_core/decision/types.py` | `RawPrediction`, `DecisionProvider`, `DecisionOutput`, `EventScope`, `UnderstandContext`, `UnderstandResult`, errores `ProviderTimeout`, `ProviderError`, `DecisionConfigError` |
| `agent_core/decision/schema.py` | validador de subconjunto de JSON Schema (struct cerrado) |
| `agent_core/decision/calibration/artifact.py` | `IsotonicMap`, `CalibrationArtifact`, `CalibrationSource`, `InMemoryCalibrationSource`, `DirectoryCalibrationSource` |
| `agent_core/decision/calibration/isotonic.py` | ajuste PAV determinista y evaluación monótona |
| `agent_core/decision/calibration/thresholds.py` | elección de umbral por precisión o recall |
| `agent_core/decision/calibration/metrics.py` | ECE, macro-F1, precisión al umbral, cobertura |
| `agent_core/decision/calibration/calibrate.py` | `DevExample`, `calibrate` |
| `agent_core/decision/calibration/report.py` | informe de métricas (artefacto y eventos) |
| `agent_core/decision/calibration/__init__.py` | interfaz pública offline |
| `agent_core/decision/providers/rule.py` | `RuleProvider` |
| `agent_core/decision/providers/classifier.py` | `ClassifierProvider`, `ClassifierArtifact` |
| `agent_core/decision/providers/llm_structured.py` | `LlmStructuredProvider` (vía `LLMGateway`) |
| `agent_core/decision/providers/jev.py` | `JevProvider`, `JevTransport` (Protocol) |
| `agent_core/decision/service.py` | `DecisionService` |
| `agent_core/decision/understand.py` | `UnderstandService` |
| `agent_core/decision/__main__.py` | comando `report` (P8) |
| `agent_core/decision/__init__.py` | interfaz pública |
| `testing/fakes/provider.py` | `ScriptedProvider` (lo entrega M5, índice §4) |
| `tests/m05/helpers.py` | `DecisionModelDef` sintéticos, artefactos pequeños, constructor del servicio |
| `tests/m05/fixtures/*.json` | artefactos y splits sintéticos |
| `tests/m05/test_*.py` | una por archivo de producción + `test_traceability.py` (T-M5-01…10) |

---

### Task 0: Preflight y confirmación de PARADAS iniciales

**Files:** ninguno.

- [ ] **Step 1: Verificar rama y base en verde**

Run: `git status --short && git branch --show-current && uv sync && uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: rama `claude/m5-m8-m4-parallel-8z4wxx`, árbol limpio (o solo este plan), pruebas verdes, contratos `KEPT`, mypy y ruff sin hallazgos. Si falla algo ajeno a M5, detente y avisa (no corregir otros módulos).

- [ ] **Step 2: Comprobar que M11 está disponible si ya llegó a la rama**

Run: `ls agent_core/audit testing/fakes | head -30`
Expected: solo informativo. M5 no depende de M11 en código; el `decision_made` lo persiste M4/M11.

- [ ] **Step 3: Releer las interfaces (deben coincidir con la sección "Interfaces consumidas")**

Run: `grep -n "class DecisionModelDef\|class ProviderSpec\|class CalibrationRef" -A9 agent_core/domain/entities.py | head -50 && grep -n "class DecisionMadePayload" -A18 agent_core/domain/events.py && uv run python -c "import agent_core.views as v; print(v.TOKEN_PATTERN); print(v.TokenVault.exists)"`
Expected: coincide. Si difiere, detente y actualiza este plan antes de seguir.

- [ ] **Step 4: Preguntar al usuario P1, P2, P3, P4, P5, P6, P7, P8 y P0c en un solo mensaje** con las opciones por defecto de la tabla. Registrar las respuestas en la Task 1 (spec rev. 2). Las Tasks 1–4 pueden empezar sin respuestas.

---

### Task 1: Esqueleto del paquete, tipos y errores

**Files:**
- Create: `agent_core/decision/types.py`, `agent_core/decision/providers/__init__.py`, `agent_core/decision/calibration/__init__.py` (vacíos con docstring)
- Modify: `agent_core/decision/__init__.py`
- Create: `tests/m05/__init__.py`, `tests/m05/helpers.py`, `tests/m05/test_types.py`

**Interfaces:**
- Consumes: `agent_core.domain` (`EntityRef`, `Decision`, `JsonValue`, `Locale`, `Probability`), `agent_core.views.TokenVault` (solo tipo).
- Produces: los tipos de la sección 2 del spec.

```python
@dataclass(frozen=True)
class RawPrediction:            # spec §2
    value: dict[str, JsonValue]
    p_raw: dict[str, float | None]
    top_k: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    latency_ms: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")

class DecisionProvider(Protocol):
    name: str
    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction: ...

@dataclass(frozen=True)
class DecisionOutput:
    value; p_cal; p_raw; top_k; above_threshold; provider_used; model_version; fallback_depth
    latency_ms; tokens; cost_usd; decision_id; model_calls: int   # model_calls: ver D9
    def as_decision(self) -> Decision: ...

class ProviderTimeout(Exception); class ProviderError(Exception); class DecisionConfigError(Exception)
```

- [ ] **Step 1: Escribir pruebas que fallan** (`tests/m05/test_types.py`)

```python
from decimal import Decimal

import pytest

from agent_core.domain import Decision
from agent_core.decision.types import DecisionOutput, RawPrediction


def _output(**over: object) -> DecisionOutput:
    base = dict(value={"command": "affirm"}, p_cal={"command": 0.9}, p_raw={"command": 0.8}, top_k={},
                above_threshold={"command": True}, provider_used="classifier", model_version="clf-1",
                fallback_depth=0, latency_ms=3, tokens=0, cost_usd=Decimal("0"), decision_id="decision-0001",
                model_calls=1)
    return DecisionOutput(**{**base, **over})  # type: ignore[arg-type]


def test_as_decision_drops_above_threshold_and_keeps_provenance() -> None:
    decision = _output().as_decision()
    assert isinstance(decision, Decision)
    assert decision.decision_id == "decision-0001" and decision.provider_used == "classifier"
    assert decision.p_cal == {"command": pytest.approx(0.9)}


def test_raw_prediction_defaults_are_neutral() -> None:
    raw = RawPrediction(value={}, p_raw={})
    assert raw.tokens == 0 and raw.cost_usd == Decimal("0") and raw.top_k == {}


def test_cost_is_decimal_not_float() -> None:
    assert isinstance(_output().cost_usd, Decimal)
```

- [ ] **Step 2: Confirmar que fallan.** Run: `uv run pytest tests/m05/test_types.py` — Expected: `ModuleNotFoundError: agent_core.decision.types`.
- [ ] **Step 3: Implementar `types.py`** (dataclasses congeladas, `slots=True`; `DecisionOutput.__repr__` sin `value` para no volcar contenido).
- [ ] **Step 4: Verificar.** Run: `uv run pytest tests/m05/test_types.py && uv run mypy && uv run lint-imports` — Expected: 3 passed, mypy y contratos limpios.
- [ ] **Step 5: Commit.** `git add agent_core/decision tests/m05 && git commit` con mensaje `feat(m5): tipos, errores y esqueleto del paquete decision` + trailers.

---

### Task 2: Validador de esquema de salida (struct cerrado)

**Files:** Create `agent_core/decision/schema.py`, `tests/m05/test_schema.py`.

**Interfaces:**
- Produces: `validate_output(value: JsonValue, schema: dict[str, JsonValue]) -> list[str]` (lista de rutas con error; vacía = válido; nunca incluye valores) y `check_schema_supported(schema) -> None` (lanza `DecisionConfigError` si usa una palabra clave fuera del subconjunto).
- Subconjunto: `type` (`object`, `string`, `array`, `boolean`, `number`, `null`), `enum`, `properties`, `required`, `additionalProperties: false` (obligatorio en cada objeto: struct cerrado), `items`. Sin `$ref`, `oneOf`, `pattern`. `number` acepta `int`/`Decimal`, no `bool`.

- [ ] **Step 1: Pruebas** (fallan): objeto válido; campo faltante; campo extra (cerrado); valor fuera de `enum`; tipo incorrecto; `bool` no es `number`; anidado (`slots` objeto libre con `additionalProperties: true` **solo** si el esquema lo declara explícitamente para `slots`, ver Task 11); esquema con `$ref` → `DecisionConfigError`; el mensaje de error no contiene el valor inválido (usa un valor sintético `SECRETO-XYZ` y asegura `"SECRETO-XYZ" not in " ".join(errors)`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_schema.py` — Expected: falla por módulo inexistente.
- [ ] **Step 3: Implementar** recursivo, rutas tipo `$.slots.amount`.
- [ ] **Step 4:** `uv run pytest tests/m05/test_schema.py && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): validador de esquema de salida (struct cerrado)`.

---

### Task 3: `CalibrationArtifact` y `IsotonicMap` (formato, serialización, aplicación)

**Files:** Create `agent_core/decision/calibration/artifact.py`, `tests/m05/test_artifact.py`, `tests/m05/fixtures/calibration_small.json`.

**Interfaces:**
- Produces (spec §2 offline):

```python
class IsotonicMap(Model):        # puntos (x, y) monótonos no decrecientes; apply(p) interpola en escalones/segmentos
    xs: list[float]; ys: list[float]
    def apply(self, p: float) -> float: ...
class Target(Model):             metric: Literal["precision", "recall"]; value: float
class CalibrationArtifact(Model):
    run_id: str; split_hash: str; method: Literal["none","isotonic","platt","temperature"]
    calibrators: dict[tuple[str, str, str], IsotonicMap]              # (field, provider, lang)
    thresholds: dict[tuple[str, str, str, str], float]                # (field, value, provider, lang)
    target: dict[str, Target]
    limitations: list[str] = []                                       # p. ej. "pt: calibración copiada de es"
    metrics: dict[str, JsonValue] = {}                                # llena Task 13
    def threshold(self, field, value, provider, lang) -> float        # ausente => 1.0 (T-M5-01)
    def calibrator(self, field, provider, lang) -> IsotonicMap | None
    def to_json(self) -> str; @classmethod from_json(text) -> CalibrationArtifact
```

- Las claves tupla se serializan como cadenas `field|provider|lang` y `field|value|provider|lang` con separador `\x1f`… **no**: usa listas de objetos `[{"key": [...], "value": ...}]` ordenadas por clave para que la salida sea determinista y sin ambigüedad si un valor contiene `|`. Entrada solo con `loads`, salida con `dumps` (JCS).

- [ ] **Step 1: Pruebas** (fallan): `threshold` de combinación ausente = `1.0`; ida y vuelta `from_json(to_json(a)) == a`; `to_json` es idéntico para dos construcciones con distinto orden de inserción (determinismo); `IsotonicMap.apply` monótona (hypothesis: para `p1 <= p2`, `apply(p1) <= apply(p2)`; resultado en `[0, 1]`); rechaza `xs` no crecientes, `ys` decrecientes, NaN e infinitos (`ValidationError`); un umbral fuera de `[0, 1]` se rechaza; el fixture `calibration_small.json` (sintético, ES y PT, `split_hash` de 64 hex) carga.
- [ ] **Step 2:** `uv run pytest tests/m05/test_artifact.py` — Expected: falla.
- [ ] **Step 3: Implementar** (Pydantic v2 con `Model` de M0; validadores para la monotonía).
- [ ] **Step 4:** `uv run pytest tests/m05/test_artifact.py && uv run mypy` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): artefacto de calibración con serialización determinista`.

---

### Task 4: `ScriptedProvider` y helpers de prueba

**Files:** Create `testing/fakes/provider.py`, `tests/m05/helpers.py` (completar), `tests/m05/test_scripted_provider.py`.

**Interfaces:**
- Produces:

```python
class ScriptedProvider:                     # cumple DecisionProvider
    name: str
    def __init__(self, name: str, script: Sequence[Step] = (), clock: FakeClock | None = None) -> None
    # Step = RawPrediction | Timeout(after_ms) | Failure(message) | latencia guionada: Step(raw, latency_ms)
    def push(self, *steps) -> None
    def predict(self, spec, inputs_model_view, schema, locale) -> RawPrediction
    calls: list[tuple[ProviderSpec, dict, str]]      # deepcopy de las entradas, como ScriptedDecision
```

- `Timeout` lanza `ProviderTimeout`; `Failure` lanza `ProviderError`; si hay `clock`, `predict` la avanza `latency_ms` antes de devolver o lanzar. Script agotado → `AssertionError("ScriptedProvider sin salida guionada")`. Guarda `calls` para que las pruebas afirmen la vista `model` recibida. Incluye `if TYPE_CHECKING` de conformidad con `DecisionProvider` (mismo patrón que `testing/fakes/decision.py`).
- `tests/m05/helpers.py`: `model_def(providers, calibrated=("command",), calibration_method="isotonic", thresholds_from="cal-1")` devuelve un `DecisionModelDef` sintético (`output_schema` cerrado con `command` enum y `slots` libre); `artifact(...)` arma un `CalibrationArtifact` mínimo; `make_service(...)` compone `DecisionService` con `FakeClock`, `FakeIds`, `InMemoryRegistry`, `InMemoryCalibrationSource` (se completa en las Tasks 5–6); `vault()` construye un `TokenVault` con `FakeKeyProvider.default()`/`FakeIds()`.

- [ ] **Step 1: Pruebas** (fallan): devuelve salidas en orden; `Timeout` lanza `ProviderTimeout` y avanza el reloj; `Failure` lanza `ProviderError`; script agotado falla con `AssertionError`; `calls` guarda copia (mutar la entrada original no cambia `calls`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_scripted_provider.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `test(m5): ScriptedProvider y helpers de prueba`.

---

### Task 5: `decide`, cadena de proveedores y fallback (T-M5-02, T-M5-03)  — requiere P2

**Files:** Create `agent_core/decision/service.py`, `tests/m05/test_chain.py`.

**Interfaces:**
- Consumes: `RegistryPort.get(ref, DecisionModelDef)`, `Clock`, `IdSource`, `DecisionProvider` por nombre (`Mapping[str, DecisionProvider]`), `CalibrationSource` (Task 6), `TokenVault`.
- Produces (firma sujeta a P2):

```python
@dataclass(frozen=True)
class EventScope:  run_id: str; release: str; turn_id: str | None = None; session_id: str | None = None

class DecisionService:
    def __init__(self, registry: RegistryPort, providers: Mapping[str, DecisionProvider],
                 calibrations: CalibrationSource, clock: Clock, ids: IdSource) -> None
    def decide(self, model_ref: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
               token_vault: TokenVault, *, scope: EventScope) -> tuple[DecisionOutput, DecisionMade]
```

Comportamiento de esta task (spec §3.1 pasos 1, 2 y 6):
1. `input_view`: `inputs_model_view` es lo que M2 ya proyectó en vista `model`; M5 verifica que las claves de `inputs_model_view` estén en `model_def.input_view` cuando este no es vacío; un path ajeno → `DecisionConfigError` (error de configuración, no `low_confidence`).
2. Recorre `providers` en orden. `ProviderTimeout`/`ProviderError` → siguiente, `fallback_depth += 1`. Salida fuera de `output_schema` (Task 2) → **1** reintento con el mismo proveedor; si vuelve a fallar → siguiente (`fallback_depth += 1`). Un proveedor sin adaptador registrado → `DecisionConfigError`.
3. Cadena agotada → `DecisionOutput` con `value = {}`, `p_cal/p_raw` con `None` por campo calibrado, `above_threshold` todo `False`, `provider_used = "none"`, `model_version = "none"`.
4. `model_calls` = número de llamadas `predict` realizadas; `tokens` y `cost_usd` acumulan **todas** las llamadas (también las fallidas con costo); `latency_ms` = `(clock.monotonic_ns() delta) // 1_000_000` total de la decisión.
5. `decision_id = ids.new_id(IdKind.decision)`; el evento se arma en la Task 8.

- [ ] **Step 1: Pruebas** (fallan). T-M5-02: primero `Timeout`, segundo devuelve valor → `provider_used == "classifier"`, `fallback_depth == 1`, `model_calls == 2`. T-M5-03: primero devuelve un valor fuera de esquema dos veces (`predict` invocado 2 veces con el mismo proveedor: verificar con `ScriptedProvider.calls`), luego el segundo proveedor responde bien → `fallback_depth == 1`; caso "primero falla una vez y acierta en el reintento" → `fallback_depth == 0`, `model_calls == 2`. Además: cadena agotada (todos fallan) → `above_threshold` todo `False` y `value == {}`; path fuera de `input_view` → `DecisionConfigError`; la entrada vista por el proveedor es exactamente `inputs_model_view` (afirmar contra `calls`); `latency_ms` sale del `FakeClock` (proveedor guionado con 40 ms → `latency_ms == 40`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_chain.py` — Expected: falla.
- [ ] **Step 3: Implementar** solo lo de esta task (sin calibración; `p_cal = p_raw` provisional detrás de `method = none`).
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run lint-imports && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): decide con cadena de proveedores, fallback y reintento por esquema`.

---

### Task 6: Calibración en runtime y tabla de umbrales (T-M5-01, T-M5-05, T-M5-06) — requiere P6

**Files:** Modify `agent_core/decision/service.py`, `agent_core/decision/calibration/artifact.py` (agrega `CalibrationSource`, `InMemoryCalibrationSource`, `DirectoryCalibrationSource`); Create `tests/m05/test_thresholds.py`.

**Interfaces:**
- `CalibrationSource(Protocol).get(run_id: str) -> CalibrationArtifact | None`. `DirectoryCalibrationSource(path)` lee `<run_id>.json` con `CalibrationArtifact.from_json` (solo `loads`); `InMemoryCalibrationSource(dict)` para pruebas.

Comportamiento (spec §3.1 pasos 3 y 4):
- Calibradores del artefacto de `calibration.run`; umbrales del artefacto de `thresholds_from`. `calibration.run is None` o artefacto ausente: `p_cal = p_raw` si `method = none`, si no `None`.
- Para cada `field` en `calibrated_fields`: `p_cal = calibrator(field, provider_used, locale).apply(p_raw[field])`; sin mapa → regla anterior; `p_raw[field] is None` → `p_cal = None`.
- `umbral = artifact.threshold(field, str(value[field]), provider_used, locale)`; ausente → `1.0`. `above_threshold[field] = p_cal is not None and p_cal >= umbral`. Con `thresholds_from = None` o sin artefacto → todo `1.0` (seguro; nota: el CI de `agent-registry` debe exigir `thresholds_from`, no lo hace M5).
- Solo los campos de `calibrated_fields` aparecen en `p_cal` y `above_threshold` (invariante). Un campo calibrado ausente en `value` cuenta como bajo umbral.

- [ ] **Step 1: Pruebas** (fallan).
  - T-M5-01: artefacto sin la combinación `(command, "affirm", "classifier", "es")` con `p_cal = 0.99` → `above_threshold["command"] is False`.
  - T-M5-05: `p_raw = None` (proveedor `llm_structured` sin logprobs) → `p_cal is None` y bajo umbral, aunque el artefacto tenga umbral `0.0`.
  - T-M5-06: mismo `p_raw`, artefacto con umbral ES `0.6` y PT `0.9`; con `p_cal = 0.8` → `above` `True` en `locale="es"` y `False` en `locale="pt"`.
  - `method = none`: `p_cal == p_raw`; `method = isotonic` sin mapa para el idioma: `p_cal is None` (y bajo umbral); `thresholds_from = None`: todo bajo umbral.
  - Invariante: un campo no calibrado (`slots`) no aparece en `above_threshold` ni en `p_cal`.
- [ ] **Step 2:** `uv run pytest tests/m05/test_thresholds.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): calibración en runtime y tabla de umbrales por (campo, valor, proveedor, idioma)`.

---

### Task 7: Validación de tokens (T-M5-04)

**Files:** Modify `agent_core/decision/service.py`; Create `tests/m05/test_tokens.py`.

Comportamiento (spec §3.1 paso 5): tras validar esquema y antes de calibrar, recorrer recursivamente **todos los strings** de `value` (valores y claves de objetos libres como `slots`), buscar `re.compile(TOKEN_PATTERN)` y verificar `token_vault.exists(match.group(0))`. Un token desconocido invalida la salida: todos los campos calibrados quedan bajo umbral (`above_threshold` todo `False`, `p_cal` conservado para diagnóstico), `value` se conserva (M2 no ramifica), y se registra el número de tokens desconocidos **sin** el token en el evento (Task 8 añade el contador solo si el payload lo admite; si no, no se agrega campo: el payload es de M0). No consulta ningún otro origen para "parecer token" (solo el patrón de M7).

- [ ] **Step 1: Pruebas** (fallan). T-M5-04: `vault.tokenize("1234567890", "document_number", "doc")` produce `⟦doc:1⟧`; una salida con `slots: {"documento": "⟦doc:1⟧"}` conserva su umbral; con `⟦doc:9⟧` (desconocido) todos los campos calibrados quedan `False` aunque `p_cal = 0.99` y umbral `0.1`. Token dentro de texto largo, dentro de lista anidada y como clave: detectado. Texto que solo se parece a un token (`⟦DOC:1⟧`, `⟦doc:0⟧`) no dispara (no coincide con el patrón). La salida de la prueba no imprime el valor en claro (usa datos inventados).
- [ ] **Step 2:** `uv run pytest tests/m05/test_tokens.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run lint-imports` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): tokens desconocidos invalidan la salida de decide`.

---

### Task 8: Evento `decision_made` completo (T-M5-08)

**Files:** Modify `agent_core/decision/service.py`; Create `tests/m05/test_event.py`.

Comportamiento (spec §3.1 paso 7 y §6): construir `DecisionMade` con `event_id = ids.new_id(IdKind.event)`, `run_id/turn_id/session_id/release` de `scope`, `ts = clock.now()`, y `DecisionMadePayload(decision_id, model=model_ref, provider_used, model_version, fallback_depth, value, p_cal, p_raw, top_k (tuplas → LabelScore), above_threshold, latency_ms, tokens, cost_usd, locale)`.
- `value` en vista `audit`: como el input ya es vista `model`, el `value` que devuelve el proveedor contiene tokens (`⟦doc:1⟧`) y nunca `full`; **no se resuelven** tokens. No incluir razonamiento del modelo (`RawPrediction` no lo transporta; probar que un `value` con clave extra fuera de esquema no llega al evento porque se rechaza en la Task 5).
- `model_version`: `spec.config["model"]` (JEV, llm), `artifact` ref/hash del `classifier`, o `"rule-1"`; lo entrega el proveedor. **Ampliar `RawPrediction`** con `model_version: str = "unknown"` (cambio interno de M5; actualizar Task 1 y spec).

- [ ] **Step 1: Pruebas** (fallan). T-M5-08: tras una decisión con fallback, el payload tiene **todas** las claves de M0 (`set(payload.model_fields) == set(DecisionMadePayload.model_fields)` y ningún campo `None` salvo `p_cal/p_raw` nulos por diseño); `event.type == "decision_made"`; `event.release`, `run_id`, `ts` provienen de `scope` y `FakeClock`; `isinstance(cost_usd, Decimal)`; `top_k` es `list[LabelScore]`; `EVENT_EMITTERS["decision_made"] == {"M5"}`; `DecisionMade.model_validate(event.model_dump())` funciona (ida y vuelta); el evento serializado con `dumps` no contiene ningún valor `full` sintético que se le pasó al vault (usa `RequestCapture.leaks`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_event.py` — Expected: falla.
- [ ] **Step 3: Implementar** más `DecisionOutput.as_decision()` ya probado en Task 1.
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): evento decision_made con la salida completa`.

---

### Task 9: Proveedores `rule` y `classifier` — requiere P4 y P7

**Files:** Create `agent_core/decision/providers/rule.py`, `agent_core/decision/providers/classifier.py`, `tests/m05/test_rule_provider.py`, `tests/m05/test_classifier_provider.py`, `tests/m05/fixtures/classifier_tiny.json`.

**`RuleProvider`** (`name = "rule"`, p ∈ {0, 1}, propuesta P7): `spec.config = {"cases": [{"when": {"path": "<clave de inputs>", "equals": <json>}, "value": {...}}], "default": {...}}`. Primer caso que coincide gana → `p_raw = {campo: 1.0}` para cada campo calibrado presente; sin caso y con `default` → `p_raw = 0.0`; sin `default` → `ProviderError`. `model_version = "rule-1"`. Sin JSON Logic (M2 lo tiene y no se puede importar).

**`ClassifierProvider`** (`name = "classifier"`, propuesta P4): carga un artefacto JSON `tfidf-logreg-v1` (`{"format", "data_hash", "vocab": {tok: idx}, "idf": [...], "classes": {campo: [valor,…]}, "coef": {campo: [[...]]}, "intercept": {campo: [...]}}`) por `spec.config["artifact"]` a través de un `ArtifactLoader` inyectado (Protocol `load(ref: str) -> str`; sin acceso a disco directo en el servicio); evalúa softmax en Python puro con `Decimal` no requerido (probabilidades `float`); tokenización: minúsculas, NFKC, `re.findall(r"\w+")`, **el texto lo toma de `inputs_model_view["text"]`** (una sola clave; otra entrada → `ProviderError`). Devuelve `value[campo] = argmax`, `p_raw[campo] = p(argmax)`, `top_k[campo]` ordenado por `(-p, valor)` (desempate estable). `model_version = f"classifier:{data_hash[:12]}"`. Rechaza artefactos con `format` desconocido (`DecisionConfigError`).

- [ ] **Step 1: Pruebas** (fallan). `rule`: caso que coincide → `p_raw == 1.0`; ningún caso + `default` → `0.0`; sin default → `ProviderError`. `classifier` con el fixture diminuto sintético (vocabulario de 6 palabras ES/PT inventadas, 3 clases): predice la clase esperada para 3 frases; `top_k` ordenado y suma ≤ 1; mismo input → misma salida (determinismo, `==` exacto de la tupla); artefacto con `format` desconocido → `DecisionConfigError`; entrada sin `text` → `ProviderError`; ambos cumplen `DecisionProvider` (`TYPE_CHECKING`); dentro de `DecisionService` con fallback desde un `Timeout` guionado.
- [ ] **Step 2:** `uv run pytest tests/m05/test_rule_provider.py tests/m05/test_classifier_provider.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): proveedores rule y classifier`.

---

### Task 10: Proveedores `llm_structured` y `jev` sobre transporte inyectable (T-M5-10)

**Files:** Create `agent_core/decision/providers/llm_structured.py`, `agent_core/decision/providers/jev.py`, `tests/m05/test_llm_structured.py`, `tests/m05/test_jev_adapter.py`, `tests/m05/test_leaks.py`.

**`LlmStructuredProvider`** (`name = "llm_structured"`, solo baseline en evaluación): `__init__(gateway: LLMGateway)`; `spec.config["prompt"]` es un `EntityRef` textual (`id@1.0.0`); llama `gateway.generate(prompt, inputs_model_view, locale, schema)`; `GatewayError` → `ProviderError`; `tokens = tokens_in + tokens_out`, `cost_usd` tal cual (`Decimal`); `p_raw = {campo: None}` para cada campo calibrado (logprobs no disponibles en el puerto) → siempre bajo umbral. `model_version = result.model`. Prueba con un `ScriptedGateway` mínimo local del test (M8 aún no lo entrega en `testing/fakes`; no crear uno compartido).

**`JevProvider`** (`name = "jev"`): `__init__(transport: JevTransport, capture: RequestCapture | None = None)` con

```python
class JevTransport(Protocol):
    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]: ...
```

Forma **provisional** del request (P0b): `{"model": spec.config["model"], "input": inputs_model_view, "schema": schema, "locale": locale}`; respuesta esperada `{"value": {...}, "probabilities": {campo: float}, "top_k": {campo: [[valor, p], ...]}, "latency_ms": int}`. `timeout_ms` de `spec.config` (obligatorio, `int > 0`). Excepciones del transporte: `TimeoutError` → `ProviderTimeout`; cualquier otra → `ProviderError` (sin volcar el request ni la API key en el mensaje). Antes de enviar, `capture.record(request)` si hay captura (así T-M5-10 mide lo que saldría). **La API key nunca pasa por este adaptador**: la añade el transporte real (Task 15). Nunca registra razonamiento.

- [ ] **Step 1: Pruebas** (fallan).
  - T-M5-10: datos de cliente sintéticos con `pii_direct` (nombre y documento inventados) pasan por `ViewService.project(..., vault)`; la vista `model` alimenta `DecisionService.decide` con `JevProvider(transport=FakeTransport, capture=RequestCapture())`; se afirma `capture.leaks(["<nombre inventado>", "<documento inventado>"]) == []` y que el request contiene el token `⟦doc:1⟧`. Mismo caso parametrizado para `LlmStructuredProvider` (captura en el `ScriptedGateway` local). Contra-prueba: alimentar deliberadamente la vista `full` y comprobar que `leaks` **sí** detecta la fuga (la prueba mide algo real).
  - `jev`: `TimeoutError` del transporte → `ProviderTimeout`; excepción arbitraria → `ProviderError` cuyo `str` no contiene el request; respuesta sin `probabilities` → `p_raw = None` por campo; `timeout_ms` ausente → `DecisionConfigError`.
  - `llm_structured`: `p_raw` siempre `None`, `cost_usd` es `Decimal`, `tokens` suma entrada y salida; `GatewayError` → `ProviderError`.
- [ ] **Step 2:** `uv run pytest tests/m05/test_llm_structured.py tests/m05/test_jev_adapter.py tests/m05/test_leaks.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run lint-imports && uv run ruff check .` — Expected: verde; `lint-imports` confirma que `providers/*` no importan `interpreter`.
- [ ] **Step 5: Commit** `feat(m5): adaptadores llm_structured y jev con captura de requests (T-M5-10)`.

---

### Task 11: `UnderstandService` (T-M5-07) — requiere P3

**Files:** Create `agent_core/decision/understand.py`, `tests/m05/test_understand.py`.

**Interfaces** (sujetas a P3; forma propuesta):

```python
@dataclass(frozen=True)
class UnderstandContext:
    model_ref: EntityRef                 # de Agent.understand (RefSpec resuelto por M4)
    flows: list[str]                     # enum de flows de la release
    interrupts: list[str]                # enum de interrupciones de la release
    current_node: str | None
    confirm_pending: bool
    recent_turns: list[str]              # text_model de TranscriptEntry, vista model (M4 arma n fijo)
    token_vault: TokenVault
    scope: EventScope

class UnderstandResult:                  # dataclass congelada
    command: Command; flow: str | None; interrupt: str | None; additional_flows: list[str]
    slots: dict[str, Any]; above_threshold: dict[str, bool]; decision_id: str
    p_cal: dict[str, float | None]       # propuesta P3 (M4 §3.2 lo lee)
class UnderstandService:
    def __init__(self, decisions: DecisionService) -> None
    def run(self, model_view_text: str, context: UnderstandContext, locale: Locale
            ) -> tuple[UnderstandResult, list[DecisionMade]]
```

Comportamiento (spec §3.2): esquema cerrado construido con `command` = enum de `Command`, `flow` = enum de `context.flows`, `interrupt` = enum de `context.interrupts`, `additional_flows` = lista de enum de `flows`, `slots` = objeto libre (`additionalProperties: true` **solo** aquí). `DecisionService` recibe el esquema efectivo mediante un método interno (`_decide_with(model_def, schema_override, ...)`) para no mutar el `DecisionModelDef` del registro. Entrada al proveedor `{"text": model_view_text, "recent_turns": [...], "current_node": ..., "confirm_pending": ...}` (vista `model`). **Una sola llamada a modelo por turno** (sin reintentos extra fuera de los del spec §3.1).
- Campos calibrados y su marca: `command` siempre; `flow` solo si `command == start_flow`; `interrupt` solo si `command == interrupt`. Los demás `above_threshold` se **omiten** (no `False`), coherente con "solo campos calibrados relevantes". `additional_flows` y `slots` nunca llevan umbral.
- `slots` salen como `claimed`: `UnderstandResult` no fabrica `Slot` (eso es de M4/M2), pero el valor se entrega tal cual junto con la marca de que no está validado; documentado en el docstring y en el spec. `additional_flows` filtrados a enum de la release (valores fuera del enum ya los rechaza el esquema).
- Cadena agotada (sin salida): el spec no dice qué `command` devuelve M5. Valor neutro provisional `Command.clarify` con `above_threshold["command"] is False` (M4 decide qué hacer con un `command` bajo umbral). La prueba queda marcada `# P3` y el valor se confirma con el usuario.

- [ ] **Step 1: Pruebas** (fallan). T-M5-07: salida con `command=start_flow`, `flow`, `slots={"monto": "⟦x:1⟧"...}` con vault que conoce el token → `above_threshold` tiene `command` y `flow` pero **no** `interrupt` ni `additional_flows` ni `slots`; `additional_flows=["f2"]` sin claves de umbral; `slots` se devuelve sin validar; con `command=interrupt` solo `command` e `interrupt`; con `affirm` solo `command`. Una sola llamada a `predict` por `run` (afirmar `len(provider.calls) == 1` cuando la primera respuesta es válida). `flow` fuera del enum de la release: 1 reintento y luego siguiente proveedor (comportamiento heredado de Task 5). Token desconocido en `slots` → todos bajo umbral. Devuelve una lista con el evento `decision_made`. Umbral de `interrupt` toma la tabla de `recall` del artefacto (fixture con `target["interrupt"].metric == "recall"`). Entrada al proveedor contiene `recent_turns` y `confirm_pending`, y ningún texto `full` (usar `RequestCapture`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_understand.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run lint-imports` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): UnderstandService con esquema por release y umbrales por campo`.

---

### Task 12: Calibración offline determinista (T-M5-09) — requiere P5 y P0c

**Files:** Create `agent_core/decision/calibration/isotonic.py`, `thresholds.py`, `calibrate.py`, `tests/m05/test_isotonic.py`, `tests/m05/test_calibrate.py`, `tests/m05/fixtures/dev_split_small.json`.

**Interfaces:**

```python
@dataclass(frozen=True)
class DevExample:
    id: str; inputs: dict[str, JsonValue]      # vista model
    labels: dict[str, str]                     # verdad por campo calibrado
    lang: Locale; synthetic: bool = False      # PT: synthetic=True (ADR 0012)

def calibrate(model_def: DecisionModelDef, dev_split: Sequence[DevExample],
              providers: Mapping[str, DecisionProvider], *,
              targets: Mapping[str, Target], min_samples: Mapping[str, int],
              min_support: int) -> CalibrationArtifact
```

(La firma del spec es `calibrate(model_def, dev_split, providers)`; los tres keyword-only son necesarios para cumplir §3.4 y no tienen valor por defecto: los fija el usuario — P0c y P5.)

Comportamiento (spec §3.4):
1. Para cada `(provider, lang)` corre el proveedor sobre el split (no toca red en pruebas: `ScriptedProvider`); errores de proveedor cuentan como ejemplos sin predicción (cobertura baja), no abortan.
2. Isotónica **PAV** determinista por `(field, provider, lang)` sobre `(p_raw, acierto)`; puntos ordenados por `(p_raw, id)` para estabilidad; ejemplos con `p_raw is None` se excluyen.
3. Umbral por `(field, value, provider, lang)`: precisión para `command`/`flow`; recall para `interrupt` (según `targets`, regla exacta según P5, por defecto: el **máximo** umbral con recall ≥ objetivo; para precisión el **mínimo** umbral con precisión ≥ objetivo). Combinación sin soporte (`< min_support`) o sin umbral que cumpla el objetivo → **se omite** (queda 1.0).
4. Si la muestra de un idioma < `min_samples[lang]` → copia la calibración del idioma base (`es`) y añade `limitations += ["pt: calibración copiada de es (muestra < mínimo)"]`. Sin idioma base → error.
5. `split_hash = sha256_hex(canonical_bytes(...))` de los `DevExample` ordenados por `id`; `run_id = "cal-" + sha256_hex(canonical_bytes({model: id@version, split_hash, method, providers ordenados, targets}))[:16]` — sin reloj ni `IdSource`: es función pura del contenido (determinismo).
6. `metrics` se completa en la Task 13.

- [ ] **Step 1: Pruebas** (fallan). T-M5-09: dos llamadas con el mismo split (incluso con el split barajado) y los mismos `ScriptedProvider` → `artifact_a.to_json() == artifact_b.to_json()` y mismo `run_id`; cambiar un ejemplo cambia `split_hash` y `run_id`. PAV: monotonía (hypothesis), casos conocidos (`[(0.1,0),(0.4,1),(0.35,0)...]` → mapa esperado calculado a mano), empates. Umbral de precisión: en un split sintético donde precisión ≥ 0.9 solo desde p ≥ 0.7 → umbral `0.7`; recall: regla P5 elegida; sin soporte suficiente → combinación ausente y `artifact.threshold(...) == 1.0`. PT bajo mínimo → calibración ES copiada y `limitations` no vacío; PT con muestra suficiente → calibración propia. `ScriptedProvider` que falla en algunos ejemplos → no aborta.
- [ ] **Step 2:** `uv run pytest tests/m05/test_isotonic.py tests/m05/test_calibrate.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): calibrate determinista (isotónica PAV, umbrales, copia ES→PT)`.

---

### Task 13: Métricas e informe por comando — requiere P8

**Files:** Create `agent_core/decision/calibration/metrics.py`, `agent_core/decision/calibration/report.py`, `agent_core/decision/__main__.py`, `tests/m05/test_metrics.py`, `tests/m05/test_report.py`.

**Métricas (spec §3.4.5 y §8):** por `(field, provider, lang)`: **ECE** (10 bins de ancho igual, promedio ponderado de `|acc - conf|`), **macro-F1** (macro sobre valores del campo), **precisión al umbral** y **cobertura** (fracción de ejemplos con `p_cal ≥ umbral`), y para `interrupt` **recall al umbral**. Se guardan en `CalibrationArtifact.metrics` (y por eso entran a `to_json`); PT etiquetado `"synthetic": true`. Métricas de runtime (latencia p50/p95, costo total, tasa de respaldo = decisiones con `fallback_depth > 0`) se calculan de una secuencia de `DecisionMade` (archivo JSONL de eventos con `loads`), por idioma y proveedor.

**Comando (P8, por defecto):** `uv run python -m agent_core.decision report --artifact <ruta.json> [--events <ruta.jsonl>] [--format md|json] [--out <ruta>]`. Salida markdown con tablas por idioma y proveedor; `--format json` con orden de claves determinista (`dumps`). Código de salida 0; artefacto ilegible → 2 con mensaje sin datos. Sin `argparse` extra: stdlib. **Si el usuario elige un subcomando de `agentcore`, esa modificación de `agent_core/cli.py` se hace aparte y se avisa.**

- [ ] **Step 1: Pruebas** (fallan). ECE: distribución perfectamente calibrada → 0; siempre confiado y siempre errado → cercano a 1 (valores calculados a mano en un caso de 10 ejemplos); macro-F1 en un caso con matriz conocida; precisión al umbral y cobertura; percentiles p50/p95 con método fijo (nearest-rank) sobre latencias conocidas; `report` sobre el artefacto de `calibrate` pequeño incluye una tabla por `(lang, provider)` y marca PT como sintético; ejecutar el módulo como subproceso (`sys.executable -m agent_core.decision report ...`) devuelve 0 y `stdout` con las columnas `ECE`, `macro-F1`, `precision@thr`, `coverage`; la salida es idéntica en dos ejecuciones (determinista); no imprime textos de entrada.
- [ ] **Step 2:** `uv run pytest tests/m05/test_metrics.py tests/m05/test_report.py` — Expected: falla.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4:** `uv run pytest tests/m05 && uv run mypy && uv run ruff check .` — Expected: verde.
- [ ] **Step 5: Commit** `feat(m5): métricas de calibración e informe por comando`.

---

### Task 14: Interfaz pública, fronteras, determinismo y trazabilidad

**Files:** Modify `agent_core/decision/__init__.py`, `agent_core/decision/calibration/__init__.py`; Create `tests/m05/test_public_api.py`, `tests/m05/test_determinism.py`, `tests/m05/test_traceability.py`.

**Interfaz pública (`__all__` de `agent_core.decision`)**: `DecisionService`, `UnderstandService`, `UnderstandContext`, `UnderstandResult`, `DecisionOutput`, `DecisionProvider`, `RawPrediction`, `EventScope`, `ProviderTimeout`, `ProviderError`, `DecisionConfigError`, `RuleProvider`, `ClassifierProvider`, `LlmStructuredProvider`, `JevProvider`, `JevTransport`. Offline en `agent_core.decision.calibration`: `CalibrationArtifact`, `IsotonicMap`, `CalibrationSource`, `InMemoryCalibrationSource`, `DirectoryCalibrationSource`, `DevExample`, `calibrate`. (M2/M4 solo deben usar esto.)

- [ ] **Step 1: Pruebas** (fallan).
  - `test_public_api.py`: como `tests/m07/test_public_api.py` (`__all__` exacto, sin nombres públicos fuera de lista salvo submódulos) más subproceso que importa `agent_core.decision` y verifica que **no** entran `agent_core.interpreter`, `flows`, `guards`, `actions`, `response`, `handoff`, `audit`, `knowledge`, `turn`, `api`, `adapters`, `registry` en `sys.modules` (patrón de `tests/m02/test_public_api.py`); y que de `agent_core.views` solo se usa la interfaz pública (búsqueda de `agent_core.views.` con submódulo en `agent_core/decision` vía `ast`).
  - `test_determinism.py`: con `FakeClock`, `FakeIds` y proveedores guionados, dos ejecuciones completas de `decide` y `Understand` producen listas de eventos iguales tras `model_dump(mode="json")` quitando `latency_ms` (`MEASURED_FIELDS`).
  - `test_traceability.py`: mapa `T-M5-01…10 → nombre de test` y verificación de que cada test existe (mismo patrón que `tests/m07/test_traceability.py`).
  - Regla dura 2: prueba que busca `datetime.now|time.time|uuid4|random|secrets|perf_counter` en `agent_core/decision/**/*.py` (además de `ruff`).
- [ ] **Step 2:** `uv run pytest tests/m05/test_public_api.py tests/m05/test_determinism.py tests/m05/test_traceability.py` — Expected: falla.
- [ ] **Step 3: Implementar** `__init__.py`.
- [ ] **Step 4: Suite completa.** Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
  Expected: todo verde; ningún tipo de M0 cambió, por lo que `contracts --check` no muestra diferencias. **Si algún cambio hiciera necesario tocar un tipo de M0, detente: es un cambio de interfaz para todos los módulos (CLAUDE.md regla 7).**
- [ ] **Step 5: Commit** `feat(m5): interfaz pública, determinismo y trazabilidad T-M5-01..10`.

---

### Task 15: PARADA — Conectar JEV real (transporte HTTP y humo)

**Bloqueada** hasta responder P0 (resultado de la prueba de humo) y P0b (contrato real). No implementar por iniciativa propia.

**Files (cuando se desbloquee):** Create `agent_core/decision/providers/jev_http.py`, `tests/m05/test_jev_http.py`, `scripts/` **no** (el humo lo ejecuta el usuario o se pide un script aparte).

- [ ] **Step 1: Preguntar al usuario:** resultado de la prueba de humo (precisión, latencia p50/p95 desde nuestro entorno, cobertura ES/PT; la parte de idioma la corre M6 en la misma prueba), formato real del request/response de JEV, dónde procesa datos, cómo se inyecta la API key (variable de entorno en el adaptador de composición, nunca en el repo) y si JEV entra en la cadena de Understand o queda como respaldo/baseline.
- [ ] **Step 2 (solo si el usuario lo autoriza):** `UrllibJevTransport(base_url, api_key: Callable[[], str])` con `urllib.request` de la stdlib; la clave solo va en el header, no en `repr`, logs ni excepciones; timeout duro del `timeout_ms`; pruebas con un servidor `http.server` local en hilo (sin red externa, sin datos reales); prueba de que la excepción de red no contiene la clave ni el cuerpo.
- [ ] **Step 3:** Ajustar la forma provisional de request (Task 10) al contrato real, actualizando `tests/m05/test_jev_adapter.py`, y repetir T-M5-10 con la forma real.
- [ ] **Step 4:** Editar spec: cerrar el Abierto "prueba de humo" con el resultado y fecha. Commit `feat(m5): transporte HTTP de JEV tras la prueba de humo`.

---

### Task 16: PARADA — Artefactos reales de calibración y del clasificador (DoD)

**Bloqueada** por P9/P0c: requiere datos etiquetados que este repo no tiene (y que no pueden inventarse para el artefacto "real"; CLAUDE.md regla 5).

- [ ] **Step 1: Preguntar al usuario:** dónde está el split de desarrollo etiquetado de Understand ES (formato `DevExample`), si llega la muestra PT sintética (otro equipo, ADR 0012) y su mínimo (unidad 6), y quién entrena/exporta el artefacto `tfidf-logreg-v1` del clasificador (ref + hash de datos).
- [ ] **Step 2 (cuando lleguen):** correr `calibrate` con el `classifier` real por idioma; guardar `<run_id>.json` en la ubicación que fije P6; generar el informe con el comando de la Task 13; referenciar `calibration.run` y `thresholds_from` en el `DecisionModelDef` de Understand **(cambio de datos en el registry, no de código)**.
- [ ] **Step 3:** Commit del artefacto solo si el usuario confirma que no contiene datos reales (`docs(m5): artefacto de calibración de Understand ES`).

---

### Task 17: Cierre — spec rev. 2, índice y Definición de terminado

**Files:** Modify `docs/specs/motor/m05-decision-model.md`, `docs/specs/motor/00-indice.md`.

- [ ] **Step 1: Spec rev. 2** (CLAUDE.md: "si cambia el comportamiento acordado, actualiza el spec en el mismo cambio"): recoger solo las decisiones que el usuario haya confirmado en P1–P8 (firma de `decide` con `scope`, `UnderstandContext`/`UnderstandResult`, `RawPrediction.model_version`, `DecisionOutput.model_calls`, regla de recall, formato `tfidf-logreg-v1`, `CalibrationSource`, `rule.config`, comando del informe, `calibrate` con parámetros keyword-only, `run_id` derivado del contenido). Lo no confirmado se deja en "Abiertos", sin cerrar.
- [ ] **Step 2: Índice §10:** estado de los Abiertos de M5 (humo de JEV, mínimo PT) según lo respondido; sin marcar "resuelto" nada que el usuario no haya resuelto.
- [ ] **Step 3: Verificación final.** Run: `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check` — Expected: todo verde.
- [ ] **Step 4: Marcar la "Definición de terminado" punto por punto** (spec §10) en el mensaje final:

| Punto DoD | Cómo se cumple | Estado |
|---|---|---|
| `decide` y Understand con `classifier` + `rule` | Tasks 5–9, 11 | listo tras Task 14 |
| JEV conectado si pasa la prueba de humo | Task 10 (adaptador con doble) + Task 15 | **PARADA P0/P0b** |
| Artefacto de calibración real de Understand ES (y PT si llega) | Tasks 12, 16 | **PARADA P9/P0c** |
| T-M5-01…10 en verde | Tasks 5–12 + `test_traceability.py` | listo tras Task 14 |
| Reporte de métricas generado por un comando | Task 13 | listo tras Task 14 |

- [ ] **Step 5: Commit** `docs(m5): spec rev. 2 y estado de los abiertos` + trailers.

---

## Trazabilidad T-M5 → Task

| ID | Caso | Task | Archivo de prueba |
|---|---|---|---|
| T-M5-01 | Combinación ausente → bajo umbral | 6 | `tests/m05/test_thresholds.py` |
| T-M5-02 | Timeout del primero → segundo, `fallback_depth = 1` | 5 | `tests/m05/test_chain.py` |
| T-M5-03 | Fuera de esquema: 1 reintento y luego siguiente | 5 | `tests/m05/test_chain.py` |
| T-M5-04 | Token desconocido invalida la salida | 7 | `tests/m05/test_tokens.py` |
| T-M5-05 | `p_cal = null` bajo umbral | 6 | `tests/m05/test_thresholds.py` |
| T-M5-06 | Umbral por idioma (ES pasa, PT no) | 6 | `tests/m05/test_thresholds.py` |
| T-M5-07 | Understand: slots `claimed`, `additional_flows` sin umbral | 11 | `tests/m05/test_understand.py` |
| T-M5-08 | `decision_made` con todos los campos | 8 | `tests/m05/test_event.py` |
| T-M5-09 | `calibrate` determinista | 12 | `tests/m05/test_calibrate.py` |
| T-M5-10 | Sin `pii_direct` en claro hacia `jev` | 10 | `tests/m05/test_leaks.py` |
