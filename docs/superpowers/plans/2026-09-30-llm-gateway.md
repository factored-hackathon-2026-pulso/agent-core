# LLM gateway y LLMAgentPort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar el gateway de LLM (`OpenAICompatGateway`, sobre OpenRouter) y el adaptador real del nodo `agent` (`LLMAgentPort`), con los cambios de M0, M1, M2, M8 y composition que la spec rev. 2 exige.

**Architecture:** Adaptador propio `agent_core/adapters/llm/` detrás del puerto `LLMGateway` de M0, con el SDK `openai` (`max_retries = 0`), validación local de la salida con `check_output` (movida de M2 a `domain`) y traducción de errores a `GatewayError`. `LLMAgentPort` implementa un paso del bucle del nodo `agent` sobre `generate` en modo `prompted`; `composition` lo inyecta por run. `ToolDef` gana `description` y `args_schema` para el catálogo que ve el modelo.

**Tech Stack:** Python 3.12, `openai` (versión exacta), `httpx`, `respx` (solo dev), OpenTelemetry, Pydantic v2, `uv`, `pytest`, `mypy --strict`, `ruff`, `import-linter`.

**Spec:** `docs/specs/2026-09-28-llm-gateway-design.md` (rev. 2) y `docs/adr/0016-llm-gateway-propio-compatible-openai.md`. Leer ambos antes de empezar.

## Global Constraints

- Trabajo en el worktree `.claude/worktrees/llm-gateway` (rama `feat/llm-gateway`, sobre `origin/main`). Todos los comandos se corren desde ahí con `uv run ...`.
- Dinero y cifras en `Decimal`, nunca `float`. La única excepción es el parámetro `temperature` que el SDK exige como `float` en el request.
- Prohibido `datetime.now()`, `time.time()`, `time.monotonic()`, `uuid4()`, `random` y `secrets` (lo verifica `ruff`). En pruebas se puede usar `time.perf_counter`.
- Todo JSON entra con `agent_core.domain.loads`; toda canonización usa `canonical_bytes`.
- `max_retries = 0` en todo cliente: una llamada a `generate` hace a lo sumo una request HTTP.
- Solo entran al gateway datos en vista `model`. Nunca van a spans, logs ni mensajes de error: la key, los headers ni el contenido de mensajes o respuestas.
- `agent_core/adapters/llm/` solo importa `agent_core.domain`, `agent_core.ports`, `openai`, `httpx` y `opentelemetry`. Única excepción: `agent_port.py` importa la interfaz pública de `agent_core.interpreter`.
- `mypy` estricto, `ruff` con línea de 110, comentarios y docstrings en español, datos de prueba sintéticos.
- Commits: `tipo(u5): resumen` más el trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Ids de la spec: G0-24 (regla nueva de M1), T-U5-01…18, T-M8-12, T-M1-46.

## Review Focus

Entradas o condiciones que la spec insinúa pero ninguna prueba obvia cubre, con su prueba en la tarea dueña:

1. **Un bloque de código con texto alrededor del JSON** (`Aquí va: ```json {…}``` `) en modo `prompted`: se rechaza como `invalid_output`; solo un bloque que es todo el contenido se acepta (Task 3).
2. **Respuesta exitosa sin `usage`:** tokens y costo 0, sin excepción (Task 3).
3. **`choices` vacío o `content: null`:** con esquema es `invalid_output`; sin esquema, texto vacío (Task 3 y 4).
4. **Key en blanco** (`"   "`) en la variable de entorno: `unavailable` sin request (Task 4).
5. **`tool_call` a una tool fuera de `tools_allowed`:** `LLMAgentPort` no la rechaza, la rechaza M2 (Task 10).

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `agent_core/domain/schema.py` (mover de `interpreter/schema.py`) | `check_output` y `unsupported_keyword`: subconjunto cerrado de JSON Schema |
| `agent_core/adapters/llm/config.py` | `EndpointConfig`, `load_endpoints`, `default_client` |
| `agent_core/adapters/llm/cost.py` | `price_of` |
| `agent_core/adapters/llm/output.py` | `parse_output`: bloque de código → JSON → `check_output` |
| `agent_core/adapters/llm/gateway.py` | `OpenAICompatGateway` |
| `agent_core/adapters/llm/agent_port.py` | `LLMAgentPort`, `STEP_SCHEMA` |
| `agent_core/adapters/llm/smoke.py` | `run_smoke`, `SmokeReport`, `SmokeRegistry` |
| `agent_core/adapters/llm/__init__.py` | interfaz pública |
| `agent_core/domain/entities.py` | `ToolDef.description` y `args_schema` |
| `agent_core/flows/rules/phase5.py`, `flows/validate.py` | regla G0-24 |
| `agent_core/interpreter/handlers/agent.py` | captura `GatewayError` de `AgentPort.step` |
| `agent_core/response/responder.py` | `invalid_output` regenera |
| `agent_core/composition/runtime.py` | inyecta `agents` por run |
| `agent_core/cli.py` | subcomando `llm-smoke` |
| `tests/u05/` | pruebas del gateway, del `LLMAgentPort` y del smoke |

---

### Task 0: Dependencias y línea base

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `.env.example`

- [ ] **Step 1: Sincronizar el entorno y correr la línea base**

Run: `uv sync`
Run: `uv run pytest -q -m "not integration and not perf"`
Expected: PASS. Si algo falla en la línea base, detenerse y avisar antes de seguir.

- [ ] **Step 2: Agregar las dependencias**

Run: `uv add openai`
Run: `uv add --dev respx`
Run: `uv pip show openai` y anotar la versión exacta (por ejemplo `X.Y.Z`).

- [ ] **Step 3: Fijar `openai` a versión exacta**

En `pyproject.toml`, dentro de `dependencies`, cambiar la línea `"openai>=X.Y.Z"` por `"openai==X.Y.Z"` (la versión del paso 2). Luego:

Run: `uv lock`
Expected: `uv.lock` actualizado; `grep -c "sha256" uv.lock` muestra hashes para `openai`.

- [ ] **Step 4: Documentar la configuración**

Agregar al final de `.env.example`:

```
# LLM gateway (unidad 5). Solo el nombre de la variable de la key va en el JSON; la key va en su variable.
LLM_ENDPOINTS={"openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"}}
OPENROUTER_API_KEY=REPLACE_WITH_YOUR_KEY
```

- [ ] **Step 5: Verificar y commitear**

Run: `uv run pytest -q -m "not integration and not perf" -x`
Expected: PASS.

```bash
git add pyproject.toml uv.lock .env.example
git commit -m "chore(u5): openai fijado, respx en dev y LLM_ENDPOINTS en .env.example" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 1: `check_output` pasa a `domain` (T-U5-12)

**Files:**
- Move: `agent_core/interpreter/schema.py` → `agent_core/domain/schema.py`
- Move: `tests/m02/test_output_schema.py` → `tests/m00/test_schema.py`
- Modify: `agent_core/domain/__init__.py`, `agent_core/interpreter/handlers/agent.py:35`

**Interfaces:**
- Produces: `agent_core.domain.check_output(schema: dict[str, JsonValue], value: JsonValue, path: str = "") -> str | None` (comportamiento idéntico al actual) y `agent_core.domain.unsupported_keyword(schema: dict[str, JsonValue], path: str = "") -> str | None`.

- [ ] **Step 1: Mover con git y ajustar el import de la prueba**

```bash
git mv agent_core/interpreter/schema.py agent_core/domain/schema.py
git mv tests/m02/test_output_schema.py tests/m00/test_schema.py
```

En `tests/m00/test_schema.py` reemplazar `from agent_core.interpreter.schema import check_output` por `from agent_core.domain import check_output, unsupported_keyword`, y cambiar la primera línea del docstring por `"""`check_output` y `unsupported_keyword`: subconjunto cerrado de JSON Schema (spec del gateway §3.7)."""`.

- [ ] **Step 2: Escribir las pruebas nuevas de `unsupported_keyword`**

Agregar al final de `tests/m00/test_schema.py`:

```python
def test_unsupported_keyword_accepts_the_closed_subset() -> None:
    schema = {"type": "object", "additionalProperties": False, "required": ["a"],
              "properties": {"a": {"type": "array", "items": {"type": "string", "description": "x"}}}}
    assert unsupported_keyword(schema) is None


def test_unsupported_keyword_finds_a_top_level_keyword() -> None:
    assert unsupported_keyword({"oneOf": []}) == "/: palabra clave no soportada 'oneOf'"


def test_unsupported_keyword_finds_a_nested_keyword_with_its_path() -> None:
    schema = {"type": "object", "properties": {"a": {"anyOf": []}}}
    assert unsupported_keyword(schema) == "/a: palabra clave no soportada 'anyOf'"


def test_a_property_named_like_a_keyword_is_not_a_keyword() -> None:
    schema = {"type": "object", "properties": {"type": {"type": "string"}, "enum": {"type": "string"}}}
    assert unsupported_keyword(schema) is None


def test_unsupported_keyword_looks_inside_items() -> None:
    schema = {"type": "array", "items": {"$ref": "#/x"}}
    assert unsupported_keyword(schema) == "/items: palabra clave no soportada '$ref'"
```

- [ ] **Step 3: Correr para verificar que falla**

Run: `uv run pytest tests/m00/test_schema.py -v`
Expected: FAIL con `ImportError: cannot import name 'check_output' from 'agent_core.domain'`.

- [ ] **Step 4: Ajustar `domain/schema.py` y exportar**

En `agent_core/domain/schema.py`: cambiar el docstring inicial por

```python
"""Subconjunto cerrado de JSON Schema (spec del gateway §3.7), sin dependencias nuevas.

Lo usan M2 (salida del nodo `agent`), el gateway de LLM (salida estructurada) y M1 (`args_schema`):
`type`, `enum`, `properties`, `required`, `additionalProperties` (booleano) e `items`. Las anotaciones
(`description`, `title`, ...) se ignoran; cualquier otra palabra clave falla cerrado. El mensaje describe
la ruta y la regla, nunca el valor: puede volver al modelo como motivo de regeneración."""
```

Cambiar `from agent_core.domain import JsonValue` por `from agent_core.domain.json import JsonValue` (evita el import circular dentro del paquete) y agregar al final del archivo:

```python
def unsupported_keyword(schema: dict[str, JsonValue], path: str = "") -> str | None:
    """`None` si todas las palabras clave del esquema (también las anidadas en `properties` e `items`)
    están en el subconjunto; si no, la ruta y la primera palabra no soportada."""
    where = path or "/"
    for key in schema:
        if key not in _SUPPORTED and key not in _ANNOTATIONS:
            return f"{where}: palabra clave no soportada {key!r}"
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if isinstance(child, dict):
                error = unsupported_keyword(child, f"{path}/{name}")
                if error is not None:
                    return error
    items = schema.get("items")
    if isinstance(items, dict):
        return unsupported_keyword(items, f"{path}/items")
    return None
```

En `agent_core/domain/__init__.py` agregar `from agent_core.domain.schema import check_output, unsupported_keyword` (en orden alfabético con los demás imports) y `"check_output"`, `"unsupported_keyword"` a `__all__`.

- [ ] **Step 5: Actualizar el consumidor en M2**

En `agent_core/interpreter/handlers/agent.py` borrar la línea `from agent_core.interpreter.schema import check_output` y agregar `check_output` a la lista de imports de `from agent_core.domain import (...)` que ya existe en ese archivo.

- [ ] **Step 6: Correr y verificar**

Run: `uv run pytest tests/m00 tests/m02 -q`
Run: `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo PASS.

- [ ] **Step 7: Commit**

```bash
git add -A agent_core tests
git commit -m "refactor(u5): check_output pasa de M2 a domain y se agrega unsupported_keyword (T-U5-12)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Configuración de endpoints y costo (T-U5-07)

**Files:**
- Create: `agent_core/adapters/llm/__init__.py`, `config.py`, `cost.py`
- Create: `tests/u05/__init__.py`, `tests/u05/test_config.py`, `tests/u05/test_cost.py`

**Interfaces:**
- Produces:
  - `EndpointConfig(alias: str, base_url: str, api_key_env: str)`, dataclass congelado.
  - `load_endpoints(env: Mapping[str, str]) -> dict[str, EndpointConfig]`; lanza `SchemaError` si el JSON es inválido.
  - `default_client(endpoint: EndpointConfig, api_key: str, timeout_s: int) -> OpenAI`.
  - `price_of(price: ModelPrice, tokens_in: int, tokens_out: int) -> Decimal`.

- [ ] **Step 1: Escribir las pruebas**

`tests/u05/__init__.py`: archivo vacío.

`tests/u05/test_cost.py`:

```python
"""`price_of`: costo exacto en Decimal, 6 decimales, ROUND_HALF_EVEN (spec del gateway §3.4, T-U5-07)."""

from datetime import date
from decimal import Decimal

from agent_core.adapters.llm.cost import price_of
from agent_core.domain import ModelPrice


def _price(inp: str, out: str) -> ModelPrice:
    return ModelPrice(input_per_mtok=Decimal(inp), output_per_mtok=Decimal(out), source="prueba",
                      as_of=date(2026, 9, 30))


def test_known_price_and_tokens_give_the_expected_decimal() -> None:
    cost = price_of(_price("3.00", "15.00"), 1000, 500)  # (3000 + 7500) / 1e6
    assert cost == Decimal("0.010500") and cost.as_tuple().exponent == -6


def test_zero_tokens_cost_zero() -> None:
    assert price_of(_price("3", "15"), 0, 0) == Decimal("0.000000")


def test_rounding_is_half_even_at_six_decimals() -> None:
    assert price_of(_price("0.5", "0"), 1, 0) == Decimal("0.000000")  # 0.0000005 -> 0 (par)
    assert price_of(_price("1.5", "0"), 1, 0) == Decimal("0.000002")  # 0.0000015 -> 2 (par)
    assert price_of(_price("2.5", "0"), 1, 0) == Decimal("0.000002")  # 0.0000025 -> 2 (par)


def test_result_is_a_decimal_never_a_float() -> None:
    assert isinstance(price_of(_price("3", "15"), 7, 9), Decimal)
```

`tests/u05/test_config.py`:

```python
"""`load_endpoints` y `default_client` (spec del gateway §2, T-U5-06)."""

import pytest

from agent_core.adapters.llm.config import EndpointConfig, default_client, load_endpoints
from agent_core.domain import SchemaError

OK = '{"openrouter": {"base_url": "https://openrouter.test/api/v1", "api_key_env": "OPENROUTER_API_KEY"}}'


def test_load_endpoints_reads_the_alias_map() -> None:
    endpoints = load_endpoints({"LLM_ENDPOINTS": OK})
    assert endpoints == {"openrouter": EndpointConfig(
        "openrouter", "https://openrouter.test/api/v1", "OPENROUTER_API_KEY")}


def test_missing_or_empty_variable_means_no_endpoints() -> None:
    assert load_endpoints({}) == {} and load_endpoints({"LLM_ENDPOINTS": ""}) == {}


@pytest.mark.parametrize("raw", [
    "no es json", "[]", '{"a": "x"}', '{"a": {"base_url": "u"}}', '{"a": {"api_key_env": "K"}}',
    '{"a": {"base_url": "", "api_key_env": "K"}}',
])
def test_malformed_configuration_is_a_schema_error_without_echoing_the_input(raw: str) -> None:
    with pytest.raises(SchemaError) as caught:
        load_endpoints({"LLM_ENDPOINTS": raw})
    assert raw not in str(caught.value) or raw == ""


def test_default_client_never_retries_and_uses_the_endpoint() -> None:
    client = default_client(EndpointConfig("o", "https://openrouter.test/api/v1", "K"), "clave", 8)
    assert client.max_retries == 0
    assert str(client.base_url).startswith("https://openrouter.test/api/v1")
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/u05 -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.adapters.llm`.

- [ ] **Step 3: Implementar**

`agent_core/adapters/llm/__init__.py`:

```python
"""Unidad 5 — LLM gateway (docs/specs/2026-09-28-llm-gateway-design.md). Interfaz pública."""
```

`agent_core/adapters/llm/cost.py`:

```python
"""Costo de una llamada: siempre `Decimal` con la tarifa del `ModelProfile` (spec §3.4)."""

from decimal import ROUND_HALF_EVEN, Decimal

from agent_core.domain import ModelPrice

_MILLION = Decimal(1_000_000)
_SIX_PLACES = Decimal("0.000001")


def price_of(price: ModelPrice, tokens_in: int, tokens_out: int) -> Decimal:
    total = (Decimal(tokens_in) * price.input_per_mtok + Decimal(tokens_out) * price.output_per_mtok) / _MILLION
    return total.quantize(_SIX_PLACES, rounding=ROUND_HALF_EVEN)
```

`agent_core/adapters/llm/config.py`:

```python
"""Endpoints del gateway (`LLM_ENDPOINTS`) y construcción del cliente `openai` (spec §2)."""

from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from openai import OpenAI

from agent_core.domain import SchemaError, loads

ENDPOINTS_ENV = "LLM_ENDPOINTS"


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    alias: str
    base_url: str
    api_key_env: str  # nombre de la variable, nunca la key


def load_endpoints(env: Mapping[str, str]) -> dict[str, EndpointConfig]:
    raw = env.get(ENDPOINTS_ENV)
    if not raw:
        return {}
    try:
        data = loads(raw)
    except ValueError:
        raise SchemaError(f"{ENDPOINTS_ENV} no es JSON válido") from None
    if not isinstance(data, dict):
        raise SchemaError(f"{ENDPOINTS_ENV} debe ser un objeto {{alias: {{base_url, api_key_env}}}}")
    endpoints: dict[str, EndpointConfig] = {}
    for alias, entry in data.items():
        base_url = entry.get("base_url") if isinstance(entry, dict) else None
        key_env = entry.get("api_key_env") if isinstance(entry, dict) else None
        if not isinstance(base_url, str) or not base_url or not isinstance(key_env, str) or not key_env:
            raise SchemaError(f"{ENDPOINTS_ENV}[{alias!r}] necesita base_url y api_key_env")
        endpoints[alias] = EndpointConfig(alias, base_url, key_env)
    return endpoints


def default_client(endpoint: EndpointConfig, api_key: str, timeout_s: int) -> OpenAI:
    """Cliente sin reintentos: una llamada a `generate` hace a lo sumo una request (spec §4)."""
    return OpenAI(base_url=endpoint.base_url, api_key=api_key, timeout=httpx.Timeout(float(timeout_s)),
                  max_retries=0)
```

- [ ] **Step 4: Correr y verificar**

Run: `uv run pytest tests/u05 -v && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/adapters/llm tests/u05
git commit -m "feat(u5): EndpointConfig, load_endpoints, default_client y price_of (T-U5-07)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `OpenAICompatGateway.generate` en el camino feliz (T-U5-02, 03, 10)

**Files:**
- Create: `agent_core/adapters/llm/output.py`, `agent_core/adapters/llm/gateway.py`
- Create: `tests/u05/helpers.py`, `tests/u05/test_generate.py`

**Interfaces:**
- Consumes: `EndpointConfig`, `default_client`, `price_of` (Task 2); `check_output` (Task 1).
- Produces:
  - `parse_output(content: str, schema: dict[str, JsonValue]) -> JsonValue`; lanza `OutputError(reason: str)`.
  - `OpenAICompatGateway(registry, endpoints, env, client_factory=default_client, tracer=None)` con `generate(prompt, inputs_model_view, locale, schema=None) -> GenerationResult`.
  - En `tests/u05/helpers.py`: `CHAT`, `PROMPT`, `INPUTS`, `DRAFT`, `completion(...)`, `install_llm_entities(registry, *, structured, timeout_s)`, `build_gateway(registry, ...)` y `make_world(...)`.

- [ ] **Step 1: Escribir los ayudantes de prueba**

`tests/u05/helpers.py`:

```python
"""Ayudantes de las pruebas del gateway. Solo datos sintéticos; sin red (respx sobre httpx)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from agent_core.adapters.llm.config import EndpointConfig
from agent_core.adapters.llm.gateway import OpenAICompatGateway
from agent_core.domain import EntityRef, JsonValue, ModelPrice, ModelProfile, Prompt, StructuredMode
from testing.fakes.registry import InMemoryRegistry

BASE_URL = "https://openrouter.test/api/v1"
CHAT = f"{BASE_URL}/chat/completions"
KEY = "sk-test-SECRETO-0001"
PROMPT = EntityRef.parse("resumen@1.0.0")
PROFILE = EntityRef.parse("perfil@1.0.0")
PROMPT_ES = "Resume la disputa."
INPUTS: dict[str, JsonValue] = {"cliente": {"nombre": "⟦name:1⟧"}, "asunto": "CONTENIDO-SENSIBLE"}
DRAFT: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["text", "citations"],
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}
GOOD_JSON = '{"text": "hola", "citations": []}'
ENDPOINTS = {"openrouter": EndpointConfig("openrouter", BASE_URL, "OPENROUTER_API_KEY")}
ENV = {"OPENROUTER_API_KEY": KEY}


def completion(content: str | None, *, finish: str = "stop", usage: tuple[int, int] | None = (120, 30),
               model: str = "vendor/modelo-x-2026", refusal: str | None = None) -> dict[str, Any]:
    """Cuerpo de una respuesta de chat.completions."""
    body: dict[str, Any] = {
        "id": "c1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content, "refusal": refusal}}]}
    if usage is not None:
        body["usage"] = {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                         "total_tokens": usage[0] + usage[1]}
    return body


def install_llm_entities(registry: InMemoryRegistry, *, structured: StructuredMode = StructuredMode.prompted,
                         timeout_s: int = 8) -> None:
    registry.add(
        ModelProfile(
            id="perfil", version="1.0.0", endpoint_alias="openrouter", model="vendor/modelo-x",
            temperature=Decimal("0.2"), max_tokens=300, timeout_s=timeout_s, structured=structured,
            price=ModelPrice(input_per_mtok=Decimal("3.00"), output_per_mtok=Decimal("15.00"),
                             source="prueba", as_of=date(2026, 9, 30))),
        Prompt.model_validate({"id": "resumen", "version": "1.0.0",
                               "locales": {"es": PROMPT_ES, "pt": "Resuma a disputa."},
                               "model_profile": "perfil@1.0.0"}))


def build_gateway(registry: InMemoryRegistry, **kwargs: Any) -> OpenAICompatGateway:
    return OpenAICompatGateway(registry, kwargs.pop("endpoints", ENDPOINTS), kwargs.pop("env", ENV), **kwargs)


@dataclass
class World:
    registry: InMemoryRegistry
    gateway: OpenAICompatGateway


def make_world(*, structured: StructuredMode = StructuredMode.prompted, timeout_s: int = 8,
               **gateway_kwargs: Any) -> World:
    registry = InMemoryRegistry()
    install_llm_entities(registry, structured=structured, timeout_s=timeout_s)
    return World(registry, build_gateway(registry, **gateway_kwargs))
```

- [ ] **Step 2: Escribir las pruebas del camino feliz**

`tests/u05/test_generate.py`:

```python
"""`OpenAICompatGateway.generate`: request, salida y costo (spec §3.1; T-U5-02, T-U5-03, T-U5-10)."""

import json
from decimal import Decimal

import pytest
from respx import MockRouter

from agent_core.adapters.llm.gateway import SCHEMA_INSTRUCTION
from agent_core.domain import (
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    Prompt,
    SchemaError,
    StructuredMode,
    canonical_bytes,
)
from tests.u05.helpers import CHAT, DRAFT, GOOD_JSON, INPUTS, PROMPT, PROMPT_ES, completion, make_world


def _body(router: MockRouter) -> dict:  # type: ignore[type-arg]
    return json.loads(router.calls.last.request.content)


def test_request_carries_profile_params_system_and_canonical_user(respx_mock: MockRouter) -> None:  # T-U5-02
    route = respx_mock.post(CHAT).respond(200, json=completion("hola"))
    make_world().gateway.generate(PROMPT, INPUTS, "es")
    body = _body(route)
    assert body["model"] == "vendor/modelo-x" and body["max_tokens"] == 300 and body["temperature"] == 0.2
    system, user = body["messages"]
    assert system == {"role": "system", "content": PROMPT_ES}
    assert user == {"role": "user", "content": canonical_bytes(INPUTS).decode()}


def test_the_locale_picks_the_prompt_text(respx_mock: MockRouter) -> None:
    route = respx_mock.post(CHAT).respond(200, json=completion("olá"))
    make_world().gateway.generate(PROMPT, INPUTS, "pt")
    assert _body(route)["messages"][0]["content"] == "Resuma a disputa."


def test_native_sends_a_strict_json_schema_named_output(respx_mock: MockRouter) -> None:  # T-U5-03
    route = respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON))
    make_world(structured=StructuredMode.native).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    body = _body(route)
    assert body["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "output", "schema": DRAFT, "strict": True}}
    assert body["messages"][0]["content"] == PROMPT_ES


def test_prompted_sends_no_response_format_and_appends_the_schema_instruction(  # T-U5-03
        respx_mock: MockRouter) -> None:
    route = respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON))
    make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    body = _body(route)
    assert "response_format" not in body
    assert body["messages"][0]["content"] == PROMPT_ES + SCHEMA_INSTRUCTION + canonical_bytes(DRAFT).decode()


def test_success_returns_output_tokens_decimal_cost_and_the_reported_model(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(120, 30)))
    result = make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    assert result.output == {"text": "hola", "citations": []}
    assert (result.tokens_in, result.tokens_out) == (120, 30)
    assert result.cost_usd == Decimal("0.000810") and isinstance(result.cost_usd, Decimal)  # (360 + 450) / 1e6
    assert result.model == "vendor/modelo-x-2026"


def test_without_schema_the_output_is_the_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Tu disputa quedó radicada."))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == "Tu disputa quedó radicada."


def test_null_content_without_schema_is_the_empty_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(None))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == ""


def test_decimals_in_structured_output_arrive_as_decimal(respx_mock: MockRouter) -> None:  # T-U5-10
    respx_mock.post(CHAT).respond(200, json=completion('{"monto": 12.50}'))
    schema = {"type": "object", "properties": {"monto": {"type": "number"}}}
    output = make_world().gateway.generate(PROMPT, INPUTS, "es", schema).output
    assert isinstance(output, dict) and output["monto"] == Decimal("12.50")
    assert isinstance(output["monto"], Decimal)


def test_a_single_code_block_around_the_json_is_accepted(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("```json\n" + GOOD_JSON + "\n```"))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT).output == {"text": "hola", "citations": []}


def test_text_around_the_json_is_invalid_output_even_with_a_code_block(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Aquí va:\n```json\n" + GOOD_JSON + "\n```"))
    with pytest.raises(GatewayError) as caught:
        make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    assert caught.value.kind is GatewayErrorKind.invalid_output


def test_a_success_without_usage_reports_zero_tokens_and_cost(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("hola", usage=None))
    result = make_world().gateway.generate(PROMPT, INPUTS, "es")
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (0, 0, Decimal("0"))


def test_a_missing_locale_is_a_programming_error(respx_mock: MockRouter) -> None:
    w = make_world()
    w.registry.add(Prompt.model_validate(  # un prompt sin `pt` (M1 lo impide con G0-12; aquí se salta)
        {"id": "solo-es", "version": "1.0.0", "locales": {"es": "hola"}, "model_profile": "perfil@1.0.0"}))
    with pytest.raises(SchemaError):
        w.gateway.generate(EntityRef.parse("solo-es@1.0.0"), INPUTS, "pt")
    assert respx_mock.calls.call_count == 0
```

Los imports de `Prompt` y `EntityRef` van arriba del archivo, junto a los de `agent_core.domain`.

- [ ] **Step 3: Correr para verificar que falla**

Run: `uv run pytest tests/u05/test_generate.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.adapters.llm.gateway`.

- [ ] **Step 4: Implementar `output.py`**

`agent_core/adapters/llm/output.py`:

```python
"""Interpretación de la salida estructurada del modelo (spec §3.1 paso 7)."""

import re

from agent_core.domain import JsonValue, check_output, loads

# Un único bloque de código que es todo el contenido: los modelos en modo `prompted` lo usan a menudo.
_FENCE = re.compile(r"\A```[A-Za-z]*[ \t]*\n(?P<body>.*?)\n?```\Z", re.DOTALL)


class OutputError(Exception):
    """La salida no es JSON o no cumple el esquema. `reason` describe la ruta y la regla, nunca el valor."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_output(content: str, schema: dict[str, JsonValue]) -> JsonValue:
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced is not None:
        text = fenced["body"]
    try:
        value = loads(text)
    except ValueError:
        raise OutputError("el contenido no es JSON") from None
    error = check_output(schema, value)
    if error is not None:
        raise OutputError(error)
    return value
```

- [ ] **Step 5: Implementar `gateway.py` (camino feliz, sin errores todavía)**

`agent_core/adapters/llm/gateway.py`:

```python
"""`OpenAICompatGateway`: `LLMGateway` sobre el SDK `openai` (spec del gateway §3)."""

import logging
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from openai import OpenAI

from agent_core.adapters.llm.config import EndpointConfig, default_client
from agent_core.adapters.llm.cost import price_of
from agent_core.adapters.llm.output import parse_output
from agent_core.domain import (
    EntityRef,
    JsonValue,
    Locale,
    ModelProfile,
    Prompt,
    SchemaError,
    StructuredMode,
    canonical_bytes,
)
from agent_core.ports import GenerationResult, RegistryPort

_LOG = logging.getLogger("agent_core.adapters.llm")
SCHEMA_INSTRUCTION = (
    "\n\nResponde únicamente con un objeto JSON que cumpla este JSON Schema, sin texto adicional ni "
    "bloques de código:\n")

ClientFactory = Callable[[EndpointConfig, str, int], OpenAI]


class OpenAICompatGateway:
    def __init__(self, registry: RegistryPort, endpoints: dict[str, EndpointConfig], env: Mapping[str, str],
                 client_factory: ClientFactory = default_client) -> None:
        self._registry = registry
        self._endpoints = endpoints
        self._env = env
        self._client_factory = client_factory

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        prompt_def = self._registry.get(prompt, Prompt)
        profile = self._registry.get(prompt_def.model_profile.require_exact(), ModelProfile)
        text = prompt_def.locales.get(locale)
        if text is None:
            raise SchemaError(f"el prompt {prompt} no tiene el locale {locale}")
        endpoint = self._endpoints[profile.endpoint_alias]  # los errores de alias llegan en la Task 4
        client = self._client_factory(endpoint, self._env["OPENROUTER_API_KEY"], profile.timeout_s)
        kwargs = _request(profile, text, inputs_model_view, schema)
        response = client.chat.completions.create(**kwargs)
        return _result(response, profile, schema)


def _request(profile: ModelProfile, text: str, inputs: dict[str, JsonValue],
             schema: dict[str, JsonValue] | None) -> dict[str, Any]:
    system = text
    if schema is not None and profile.structured is StructuredMode.prompted:
        system += SCHEMA_INSTRUCTION + canonical_bytes(schema).decode()
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": canonical_bytes(inputs).decode()}],
        "temperature": float(profile.temperature),  # el SDK exige float; el costo nunca usa float
        "max_tokens": profile.max_tokens,
    }
    if schema is not None and profile.structured is StructuredMode.native:
        kwargs["response_format"] = {
            "type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}
    return kwargs


def _result(response: Any, profile: ModelProfile, schema: dict[str, JsonValue] | None) -> GenerationResult:
    choice = response.choices[0]
    content = choice.message.content or ""
    output: JsonValue = parse_output(content, schema) if schema is not None else content
    usage = response.usage
    if usage is None:
        _LOG.warning("respuesta sin usage model=%s", response.model)
        tokens_in = tokens_out = 0
        cost = Decimal("0")
    else:
        tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        cost = price_of(profile.price, tokens_in, tokens_out)
    return GenerationResult(output=output, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
                            model=response.model or profile.model)
```

Este archivo es deliberadamente incompleto: `self._env["OPENROUTER_API_KEY"]` y `self._endpoints[...]` se reemplazan por la lógica real de la Task 4; los tests de la Task 3 solo cubren el camino feliz.

- [ ] **Step 6: Correr y verificar**

Run: `uv run pytest tests/u05/test_generate.py -v`
Expected: PASS salvo `test_text_around_the_json_is_invalid_output_even_with_a_code_block`, que necesita `GatewayError` (hasta la Task 4 el adaptador deja escapar `OutputError`). Marcar ese único test con `@pytest.mark.skip(reason="Task 4")` y quitar el `skip` en la Task 4.

- [ ] **Step 7: Commit**

```bash
git add agent_core/adapters/llm tests/u05
git commit -m "feat(u5): OpenAICompatGateway.generate en el camino feliz (T-U5-02, T-U5-03, T-U5-10)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Traducción de errores, alias, key y sin reintentos (T-U5-04, 05, 06, 08)

**Files:**
- Modify: `agent_core/adapters/llm/gateway.py`
- Test: `tests/u05/test_errors.py`; quitar los `skip` de `tests/u05/test_generate.py`

**Interfaces:**
- Consumes: `OutputError` (Task 3), `GatewayError`, `GatewayErrorKind` de `agent_core.domain`.
- Produces: `generate` lanza únicamente `GatewayError` (o `SchemaError` por locale faltante, error de programación). `GatewayError` lleva `tokens_in`, `tokens_out`, `cost_usd`, `model` cuando el proveedor informó el uso.

- [ ] **Step 1: Escribir las pruebas**

`tests/u05/test_errors.py`:

```python
"""Traducción de errores del gateway (spec §3.2, §5; T-U5-04, T-U5-05, T-U5-06, T-U5-08)."""

from decimal import Decimal

import httpx
import pytest
from respx import MockRouter

from agent_core.adapters.llm.config import EndpointConfig, default_client
from agent_core.domain import GatewayError, GatewayErrorKind
from tests.u05.helpers import CHAT, DRAFT, ENV, GOOD_JSON, INPUTS, PROMPT, completion, make_world


def _fail(w, schema=None) -> GatewayError:  # type: ignore[no-untyped-def]
    with pytest.raises(GatewayError) as caught:
        w.gateway.generate(PROMPT, INPUTS, "es", schema)
    return caught.value


@pytest.mark.parametrize(("status", "kind"), [
    (503, GatewayErrorKind.unavailable), (500, GatewayErrorKind.unavailable),
    (400, GatewayErrorKind.unavailable), (401, GatewayErrorKind.unavailable),
    (429, GatewayErrorKind.rate_limited)])
def test_http_status_maps_to_a_kind_with_exactly_one_request(  # T-U5-05 y T-U5-06
        respx_mock: MockRouter, status: int, kind: GatewayErrorKind) -> None:
    route = respx_mock.post(CHAT).respond(status, json={"error": {"message": "x"}})
    error = _fail(make_world())
    assert error.kind is kind and route.call_count == 1  # max_retries = 0
    assert error.tokens_in is None and error.cost_usd is None


def test_client_timeout_maps_to_timeout(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).mock(side_effect=httpx.ConnectTimeout("lento"))
    assert _fail(make_world()).kind is GatewayErrorKind.timeout


@pytest.mark.parametrize("side_effect", [httpx.ConnectError("caído"), RuntimeError("no previsto")])
def test_connection_errors_and_unforeseen_exceptions_are_unavailable(
        respx_mock: MockRouter, side_effect: Exception) -> None:
    respx_mock.post(CHAT).mock(side_effect=side_effect)
    assert _fail(make_world()).kind is GatewayErrorKind.unavailable


def test_content_filter_is_refused_with_the_reported_usage(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("", finish="content_filter", usage=(120, 30)))
    error = _fail(make_world())
    assert error.kind is GatewayErrorKind.refused
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (120, 30, Decimal("0.000810"))
    assert error.model == "vendor/modelo-x-2026"


def test_a_refusal_field_is_refused(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(None, refusal="no puedo ayudar", usage=(10, 2)))
    assert _fail(make_world()).kind is GatewayErrorKind.refused


def test_output_that_breaks_the_schema_is_invalid_output_with_usage(respx_mock: MockRouter) -> None:  # T-U5-04
    respx_mock.post(CHAT).respond(200, json=completion('{"text": 5}', usage=(120, 30)))
    error = _fail(make_world(), DRAFT)
    assert error.kind is GatewayErrorKind.invalid_output
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (120, 30, Decimal("0.000810"))


def test_not_json_is_invalid_output(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("no es json"))
    assert _fail(make_world(), DRAFT).kind is GatewayErrorKind.invalid_output


def test_truncated_output_with_a_schema_is_invalid_output(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON[:10], finish="length"))
    assert _fail(make_world(), DRAFT).kind is GatewayErrorKind.invalid_output


def test_truncated_output_without_a_schema_is_returned_as_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Tu disputa quedó", finish="length"))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == "Tu disputa quedó"


def test_empty_choices_is_invalid_output(respx_mock: MockRouter) -> None:
    body = completion("x")
    body["choices"] = []
    respx_mock.post(CHAT).respond(200, json=body)
    assert _fail(make_world()).kind is GatewayErrorKind.invalid_output


def test_unknown_alias_is_unavailable_without_a_request(respx_mock: MockRouter) -> None:  # T-U5-08
    error = _fail(make_world(endpoints={}))
    assert error.kind is GatewayErrorKind.unavailable and respx_mock.calls.call_count == 0


@pytest.mark.parametrize("env", [{}, {"OPENROUTER_API_KEY": ""}, {"OPENROUTER_API_KEY": "   "}])
def test_missing_empty_or_blank_key_is_unavailable_without_a_request(
        respx_mock: MockRouter, env: dict[str, str]) -> None:  # T-U5-08
    error = _fail(make_world(env=env))
    assert error.kind is GatewayErrorKind.unavailable and respx_mock.calls.call_count == 0


def test_default_client_is_built_without_retries() -> None:  # T-U5-06
    client = default_client(EndpointConfig("openrouter", "https://openrouter.test/api/v1", "K"), ENV["OPENROUTER_API_KEY"], 8)
    assert client.max_retries == 0
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/u05/test_errors.py -v`
Expected: FAIL (`KeyError`, excepciones del SDK sin traducir, etc.).

- [ ] **Step 3: Reemplazar la parte de `generate` y `_result` en `gateway.py`**

Reemplazar los imports y el cuerpo de `generate`/`_result` por esta versión (el resto del archivo —`_request`, constantes— queda igual). Agregar a los imports:

```python
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import openai

from agent_core.adapters.llm.output import OutputError, parse_output
from agent_core.domain import GatewayError, GatewayErrorKind
```

`generate`:

```python
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        prompt_def = self._registry.get(prompt, Prompt)
        profile = self._registry.get(prompt_def.model_profile.require_exact(), ModelProfile)
        text = prompt_def.locales.get(locale)
        if text is None:
            raise SchemaError(f"el prompt {prompt} no tiene el locale {locale}")
        endpoint = self._endpoints.get(profile.endpoint_alias)
        if endpoint is None:
            _LOG.error("alias de endpoint sin configurar alias=%s", profile.endpoint_alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        api_key = self._env.get(endpoint.api_key_env, "").strip()
        if not api_key:
            _LOG.error("variable de key vacía alias=%s", endpoint.alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        client = self._client_factory(endpoint, api_key, profile.timeout_s)
        kwargs = _request(profile, text, inputs_model_view, schema)
        response = _create(client, kwargs, profile, endpoint.alias)
        return _result(response, profile, schema)
```

Funciones de módulo (reemplazan a `_result`):

```python
def _create(client: OpenAI, kwargs: dict[str, Any], profile: ModelProfile, alias: str) -> Any:
    """Una request con plazo total de `timeout_s` y errores traducidos (spec §3.1 paso 5 y §3.2)."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-gateway")
    future = pool.submit(lambda: client.chat.completions.create(**kwargs))
    try:
        return future.result(timeout=profile.timeout_s)
    except FutureTimeout:
        raise _error(GatewayErrorKind.timeout, alias, profile, "plazo total") from None
    except openai.APITimeoutError:
        raise _error(GatewayErrorKind.timeout, alias, profile, "timeout") from None
    except openai.RateLimitError:
        raise _error(GatewayErrorKind.rate_limited, alias, profile, "429") from None
    except openai.APIStatusError as error:
        raise _error(GatewayErrorKind.unavailable, alias, profile, f"http {error.status_code}") from None
    except Exception as error:  # conexión, transporte o algo no previsto: nada más sale del adaptador
        raise _error(GatewayErrorKind.unavailable, alias, profile, type(error).__name__) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _error(kind: GatewayErrorKind, alias: str, profile: ModelProfile, why: str) -> GatewayError:
    # Solo alias, tipo y motivo corto: nunca el mensaje de la excepción (puede traer contenido o URLs).
    _LOG.warning("gateway %s alias=%s model=%s causa=%s", kind.value, alias, profile.model, why)
    return GatewayError(kind, model=profile.model)


def _result(response: Any, profile: ModelProfile, schema: dict[str, JsonValue] | None) -> GenerationResult:
    usage = response.usage
    tokens_in = usage.prompt_tokens if usage is not None else None
    tokens_out = usage.completion_tokens if usage is not None else None
    cost = price_of(profile.price, tokens_in, tokens_out) if usage is not None else None
    model = response.model or profile.model

    def fail(kind: GatewayErrorKind, why: str) -> GatewayError:
        _LOG.warning("gateway %s model=%s causa=%s", kind.value, model, why)
        return GatewayError(kind, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, model=model)

    if not response.choices:
        raise fail(GatewayErrorKind.invalid_output, "sin choices")
    choice = response.choices[0]
    if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
        raise fail(GatewayErrorKind.refused, "rechazo del modelo")
    content = choice.message.content or ""
    output: JsonValue = content
    if schema is not None:
        if choice.finish_reason == "length":
            raise fail(GatewayErrorKind.invalid_output, "salida truncada")
        try:
            output = parse_output(content, schema)
        except OutputError as error:
            raise fail(GatewayErrorKind.invalid_output, error.reason) from None
    if usage is None:
        _LOG.warning("respuesta sin usage model=%s", model)
    return GenerationResult(output=output, tokens_in=tokens_in or 0, tokens_out=tokens_out or 0,
                            cost_usd=cost if cost is not None else Decimal("0"), model=model)
```

Quitar `SCHEMA_INSTRUCTION`-independientes que ya no se usan y asegurar que `Decimal` sigue importado.

- [ ] **Step 4: Quitar los `skip` de la Task 3 y correr**

Run: `uv run pytest tests/u05 -v && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/adapters/llm tests/u05
git commit -m "feat(u5): traduccion de errores del gateway, alias y key sin request, sin reintentos (T-U5-04..06, 08)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Plazo total por llamada (T-U5-11)

**Files:**
- Test: `tests/u05/test_deadline.py` (la implementación ya está en `_create` de la Task 4)

- [ ] **Step 1: Escribir la prueba**

`tests/u05/test_deadline.py`:

```python
"""Plazo total: un endpoint que responde tarde produce `timeout` dentro de `timeout_s` (spec §3.1 paso 5)."""

import threading
import time

import httpx
import pytest
from respx import MockRouter

from agent_core.domain import GatewayError, GatewayErrorKind
from tests.u05.helpers import CHAT, INPUTS, PROMPT, completion, make_world


def test_a_slow_endpoint_times_out_within_the_total_deadline(respx_mock: MockRouter) -> None:  # T-U5-11
    release = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(3)  # cada fase HTTP estaría bajo su timeout, pero el total supera timeout_s
        return httpx.Response(200, json=completion("tarde"))

    respx_mock.post(CHAT).mock(side_effect=slow)
    started = time.perf_counter()
    with pytest.raises(GatewayError) as caught:
        make_world(timeout_s=1).gateway.generate(PROMPT, INPUTS, "es")
    elapsed = time.perf_counter() - started
    release.set()
    assert caught.value.kind is GatewayErrorKind.timeout
    assert elapsed < 2.5  # 1 s de plazo, con holgura; no espera a que el hilo termine
```

- [ ] **Step 2: Correr**

Run: `uv run pytest tests/u05/test_deadline.py -v`
Expected: PASS (~1 s). Si falla porque `generate` espera al hilo, revisar que `pool.shutdown(wait=False, cancel_futures=True)` esté en el `finally` y que no se use `with ThreadPoolExecutor(...)` (su `__exit__` espera al hilo).

- [ ] **Step 3: Commit**

```bash
git add tests/u05/test_deadline.py
git commit -m "test(u5): plazo total de la llamada al gateway (T-U5-11)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Observabilidad y sin fugas (T-U5-09)

**Files:**
- Modify: `agent_core/adapters/llm/gateway.py`
- Test: `tests/u05/test_observability.py`

**Interfaces:**
- Consumes: `tracer` opcional del constructor (Task 3 lo dejó fuera; se agrega aquí).
- Produces: un span `chat` por llamada con los atributos de la spec §3.5.

- [ ] **Step 1: Escribir las pruebas**

`tests/u05/test_observability.py`:

```python
"""Spans y logs del gateway (spec §3.5; T-U5-09): semconv GenAI y nada de contenido ni secretos."""

import logging

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from respx import MockRouter

from agent_core.domain import GatewayError
from tests.u05.helpers import CHAT, DRAFT, GOOD_JSON, INPUTS, KEY, PROMPT, completion, make_world

SECRETS = (KEY, "CONTENIDO-SENSIBLE", "TEXTO-DEL-MODELO")


def _tracer() -> tuple[object, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("prueba"), exporter


def test_a_call_emits_one_chat_span_with_genai_attributes(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(120, 30)))
    tracer, exporter = _tracer()
    make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    (span,) = exporter.get_finished_spans()
    assert span.name == "chat"
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.operation.name"] == "chat" and attrs["gen_ai.provider.name"] == "openrouter"
    assert attrs["gen_ai.request.model"] == "vendor/modelo-x"
    assert attrs["gen_ai.response.model"] == "vendor/modelo-x-2026"
    assert attrs["gen_ai.usage.input_tokens"] == 120 and attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["agentcore.prompt"] == "resumen@1.0.0" and attrs["agentcore.model_profile"] == "perfil@1.0.0"
    assert "agentcore.gateway.error_kind" not in attrs


def test_an_error_records_its_kind_on_the_span(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(429, json={"error": {"message": "TEXTO-DEL-MODELO"}})
    tracer, exporter = _tracer()
    with pytest.raises(GatewayError):
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es")
    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes or {})["agentcore.gateway.error_kind"] == "rate_limited"


@pytest.mark.parametrize("failure", ["http", "salida", "conexion"])
def test_neither_the_key_nor_the_content_reach_spans_logs_or_errors(
        respx_mock: MockRouter, caplog: pytest.LogCaptureFixture, failure: str) -> None:
    if failure == "http":
        respx_mock.post(CHAT).respond(503, json={"error": {"message": "TEXTO-DEL-MODELO CONTENIDO-SENSIBLE"}})
    elif failure == "salida":
        respx_mock.post(CHAT).respond(200, json=completion("TEXTO-DEL-MODELO", usage=(5, 5)))
    else:
        respx_mock.post(CHAT).mock(side_effect=httpx.ConnectError("TEXTO-DEL-MODELO " + KEY))
    tracer, exporter = _tracer()
    caplog.set_level(logging.INFO)  # el SDK `openai` registra el contenido de los requests solo en DEBUG
    with pytest.raises(GatewayError) as caught:
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    seen = [caplog.text, str(caught.value), repr(caught.value)]
    for span in exporter.get_finished_spans():
        seen += [str(span.name), str(dict(span.attributes or {})),
                 *(str(dict(event.attributes or {})) for event in span.events)]
    for secret in SECRETS:
        assert all(secret not in text for text in seen), secret
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/u05/test_observability.py -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'tracer'`).

- [ ] **Step 3: Implementar el span**

En `gateway.py` agregar los imports `from opentelemetry import trace` y `from opentelemetry.trace import Span, Tracer`, el parámetro `tracer: Tracer | None = None` en `__init__` con `self._tracer = tracer or trace.get_tracer("agent_core.adapters.llm")`, y reemplazar el final de `generate` (desde `endpoint = ...`) por un cuerpo envuelto:

```python
        with self._tracer.start_as_current_span("chat") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", profile.model)
            span.set_attribute("agentcore.prompt", str(prompt))
            span.set_attribute("agentcore.model_profile", str(prompt_def.model_profile.require_exact()))
            span.set_attribute("gen_ai.provider.name", profile.endpoint_alias)
            try:
                return self._call(span, profile, text, inputs_model_view, schema)
            except GatewayError as error:
                span.set_attribute("agentcore.gateway.error_kind", error.kind.value)
                raise
```

y mover lo que estaba tras `text is None` a un método:

```python
    def _call(self, span: Span, profile: ModelProfile, text: str, inputs: dict[str, JsonValue],
              schema: dict[str, JsonValue] | None) -> GenerationResult:
        endpoint = self._endpoints.get(profile.endpoint_alias)
        if endpoint is None:
            _LOG.error("alias de endpoint sin configurar alias=%s", profile.endpoint_alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        api_key = self._env.get(endpoint.api_key_env, "").strip()
        if not api_key:
            _LOG.error("variable de key vacía alias=%s", endpoint.alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        client = self._client_factory(endpoint, api_key, profile.timeout_s)
        response = _create(client, _request(profile, text, inputs, schema), profile, endpoint.alias)
        result = _result(response, profile, schema)
        span.set_attribute("gen_ai.response.model", result.model)
        span.set_attribute("gen_ai.usage.input_tokens", result.tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", result.tokens_out)
        return result
```

Para que los errores con uso (`invalid_output`, `refused`) también dejen `gen_ai.response.model` y tokens en el span, capturarlos en `generate`: dentro del `except GatewayError as error:` agregar, antes de `raise`,

```python
                for name, value in (("gen_ai.response.model", error.model),
                                    ("gen_ai.usage.input_tokens", error.tokens_in),
                                    ("gen_ai.usage.output_tokens", error.tokens_out)):
                    if value is not None:
                        span.set_attribute(name, value)
```

- [ ] **Step 4: Correr y verificar**

Run: `uv run pytest tests/u05 -v && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/adapters/llm tests/u05
git commit -m "feat(u5): span chat con semconv GenAI y sin fugas de key ni contenido (T-U5-09)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Interfaz pública, suite de contrato y fronteras (T-U5-01)

**Files:**
- Modify: `agent_core/adapters/llm/__init__.py`, `tests/contracts/test_gateway_contract.py`

**Interfaces:**
- Produces: `from agent_core.adapters.llm import EndpointConfig, OpenAICompatGateway, default_client, load_endpoints, price_of`.

- [ ] **Step 1: Escribir la prueba de la interfaz pública**

`tests/u05/test_public_api.py`:

```python
"""Interfaz pública de la unidad 5 (spec §2)."""

import agent_core.adapters.llm as llm


def test_the_public_names_are_exported() -> None:
    assert set(llm.__all__) >= {"EndpointConfig", "OpenAICompatGateway", "default_client", "load_endpoints",
                                "price_of"}
    for name in llm.__all__:
        assert hasattr(llm, name)
```

- [ ] **Step 2: Parametrizar la suite de contrato**

En `tests/contracts/test_gateway_contract.py` reemplazar las dos fixtures `ok_gateway` y `failing_gateway` por versiones parametrizadas (los tests que las usan no cambian) y agregar los imports:

```python
from respx import MockRouter

from tests.u05.helpers import CHAT, completion, make_world


@pytest.fixture(params=["scripted", "openai"])
def ok_gateway(request: pytest.FixtureRequest, respx_mock: MockRouter) -> LLMGateway:
    if request.param == "scripted":
        return ScriptedGateway([gen("hola", []), gen("hola", []), gen("hola", [])])
    respx_mock.post(CHAT).respond(200, json=completion('{"ok": true}'))
    return make_world().gateway


@pytest.fixture(params=["scripted", "openai"])
def failing_gateway(request: pytest.FixtureRequest, respx_mock: MockRouter) -> LLMGateway:
    if request.param == "scripted":
        return ScriptedGateway([GatewayError(GatewayErrorKind.unavailable, model="scripted-1")])
    respx_mock.post(CHAT).respond(503, json={"error": {"message": "x"}})
    return make_world().gateway
```

`PROMPT` del contrato ya es `resumen@1.0.0`, igual que el de `tests/u05/helpers.py`. El esquema `{"type": "object"}` que usa `check_returns_generation_result` acepta `{"ok": true}`.

- [ ] **Step 3: Exportar la interfaz**

`agent_core/adapters/llm/__init__.py`:

```python
"""Unidad 5 — LLM gateway (docs/specs/2026-09-28-llm-gateway-design.md). Interfaz pública."""

from agent_core.adapters.llm.config import EndpointConfig, default_client, load_endpoints
from agent_core.adapters.llm.cost import price_of
from agent_core.adapters.llm.gateway import OpenAICompatGateway

__all__ = ["EndpointConfig", "OpenAICompatGateway", "default_client", "load_endpoints", "price_of"]
```

- [ ] **Step 4: Correr todo y verificar las fronteras**

Run: `uv run pytest tests/contracts tests/u05 -v && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: PASS. `lint-imports` no debe quejarse: `agent_core.adapters` solo importa `domain`, `ports` y librerías externas.

- [ ] **Step 5: Commit**

```bash
git add agent_core/adapters/llm tests
git commit -m "feat(u5): interfaz publica y suite de contrato de LLMGateway sobre ambas implementaciones (T-U5-01)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: M8 distingue `invalid_output` (T-M8-12)

**Files:**
- Modify: `agent_core/response/responder.py` (bucle de `generate`)
- Test: `tests/m08/test_responder_generate.py`

**Interfaces:**
- Consumes: `GatewayError`, `GatewayErrorKind`, `Failure` (`agent_core.response.types`), `RejectedDraft`.
- Produces: `invalid_output` cuenta como falla de formato (1 regeneración y luego plantilla); `timeout`, `unavailable`, `rate_limited` y `refused` siguen yendo directo a la plantilla.

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/m08/test_responder_generate.py`:

```python
import pytest

INVALID = GatewayError(GatewayErrorKind.invalid_output, tokens_in=5, tokens_out=3, cost_usd=Decimal("0.0005"),
                       model="scripted-1")


def test_t_m8_12_invalid_output_regenerates_once_and_reports_a_format_rejection() -> None:
    w = World([INVALID, gen(GOOD, ["f-pqr"])])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "generated"
    assert len(rejected) == 1 and rejected[0].failures == ["format"] and rejected[0].text_model == ""
    payload = _payload(events)
    assert payload.validator.regenerations == 1 and payload.llm.calls == 2  # type: ignore[union-attr]
    assert payload.llm.cost_known is True  # el error informó su costo  # type: ignore[union-attr]
    feedback = w.gateway.calls[1].inputs["validation_feedback"]
    assert isinstance(feedback, list) and feedback[0]["check"] == "format"  # type: ignore[call-overload]


def test_t_m8_12_two_invalid_outputs_fall_back_to_the_template() -> None:
    w = World([INVALID, INVALID])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "template"
    payload = _payload(events)
    assert payload.fallback_used and payload.validator.failures == ["format"]
    assert len(w.gateway.calls) == 2 and len(rejected) == 2


@pytest.mark.parametrize("kind", [GatewayErrorKind.timeout, GatewayErrorKind.unavailable,
                                  GatewayErrorKind.rate_limited, GatewayErrorKind.refused])
def test_t_m8_12_other_gateway_errors_go_straight_to_the_template(kind: GatewayErrorKind) -> None:
    w = World([GatewayError(kind)])
    message, rejected, events = w.run()
    assert isinstance(message, Message) and message.kind == "template" and rejected == []
    assert len(w.gateway.calls) == 1
    assert _payload(events).llm.cost_known is False  # type: ignore[union-attr]
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/m08/test_responder_generate.py -k t_m8_12 -v`
Expected: FAIL en los dos primeros (`invalid_output` hoy va directo a la plantilla).

- [ ] **Step 3: Implementar en `responder.py`**

En `agent_core/response/responder.py` agregar `GatewayError` y `GatewayErrorKind` al import de `agent_core.domain`, y reemplazar

```python
            try:
                result = meter.call(partial(ctx.gateway.generate, prompt, inputs, ctx.locale, DRAFT_SCHEMA))
            except Exception:
                break
            parsed = parse_draft(result.output)
```

por

```python
            try:
                result = meter.call(partial(ctx.gateway.generate, prompt, inputs, ctx.locale, DRAFT_SCHEMA))
            except GatewayError as error:
                if error.kind is not GatewayErrorKind.invalid_output:
                    break  # timeout, unavailable, rate_limited, refused: directo a la plantilla (spec §3.3)
                failure = Failure(check="format", detail="la salida del gateway no cumple el esquema")
                rejected.append(RejectedDraft(text_model="", reason=f"{failure.check}: {failure.detail}",
                                              failures=[failure.check]))
                last_failures, regenerations = [failure], attempt
                continue
            except Exception:
                break
            parsed = parse_draft(result.output)
```

- [ ] **Step 4: Correr y verificar**

Run: `uv run pytest tests/m08 -q && uv run mypy && uv run ruff check .`
Expected: PASS (incluidos los tests existentes de M8).

- [ ] **Step 5: Commit**

```bash
git add agent_core/response tests/m08
git commit -m "feat(m8): invalid_output del gateway regenera una vez; los demas errores van a la plantilla (T-M8-12)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `ToolDef.description` y `args_schema` + regla G0-24 (T-M1-46)

**Files:**
- Modify: `agent_core/domain/entities.py` (clase `ToolDef`), `agent_core/flows/rules/phase5.py`, `agent_core/flows/validate.py`, `tests/m01/cases.py`
- Test: `tests/m00/test_entities.py`, `tests/m01/test_agent_node.py`
- Regenerar: `contracts/`

**Interfaces:**
- Produces:
  - `ToolDef.description: str | None = None` y `ToolDef.args_schema: dict[str, JsonValue] | None = None`.
  - Regla `G0-24`: para toda tool en `tools_allowed` de un nodo `agent`, `description` no vacía, `args_schema` presente y dentro del subconjunto (`unsupported_keyword`).

- [ ] **Step 1: Escribir las pruebas**

En `tests/m00/test_entities.py` agregar (ajustando el import de `ToolDef` si ya existe):

```python
def test_tool_def_documentation_fields_are_optional_and_round_trip() -> None:
    base = {"id": "leer", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
            "idempotent": True}
    bare = ToolDef.model_validate(base)
    assert bare.description is None and bare.args_schema is None
    doc = ToolDef.model_validate(base | {"description": "Lee un cargo", "args_schema": {"type": "object"}})
    assert doc.description == "Lee un cargo" and doc.args_schema == {"type": "object"}
```

En `tests/m01/cases.py`, en `_tool`, agregar documentación por defecto para las tools de solo lectura y cálculo (las que un nodo `agent` puede usar) y dos tools de prueba nuevas:

```python
def _tool(tid: str, risk: str, **extra: Any) -> ToolDef:
    docs: dict[str, Any] = (
        {"description": f"tool {tid}", "args_schema": {"type": "object"}} if risk in ("read", "compute") else {})
    return ToolDef.model_validate(
        {"id": tid, "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
         "idempotent": risk in ("read", "compute"), **docs, **extra}
    )
```

y, en la lista `ENTITIES` junto a `_tool("leer", "read")`:

```python
    _tool("sindoc", "read", description=None, args_schema=None),
    _tool("malschema", "read", args_schema={"type": "object", "properties": {"a": {"oneOf": []}}}),
```

En `tests/m01/test_agent_node.py` agregar:

```python
def test_g0_24_agent_tool_without_documentation() -> None:
    assert rules(check(with_agent(tools_allowed=["sindoc@1"]))) == {"G0-24"}


def test_g0_24_agent_tool_with_args_schema_outside_the_subset() -> None:
    found = check(with_agent(tools_allowed=["malschema@1"]))
    assert rules(found) == {"G0-24"}
    assert "oneOf" in found[0].message


def test_g0_24_does_not_apply_to_tools_outside_agent_nodes() -> None:
    assert "G0-24" not in rules(check(base()))  # `leer@1` sin documentar sería válida fuera de un nodo agent
```

(`base()` es el flow sin nodo `agent` que ya importa este archivo desde `cases`.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/m00/test_entities.py tests/m01/test_agent_node.py -q`
Expected: FAIL (`ToolDef` rechaza `description`/`args_schema` por `extra=forbid`; falta la regla).

- [ ] **Step 3: Implementar el tipo**

En `agent_core/domain/entities.py`, en `ToolDef`, después de `confirmation_ttl`:

```python
    description: str | None = None  # qué hace la tool, para el modelo del nodo `agent` (unidad 5)
    args_schema: dict[str, JsonValue] | None = None  # subconjunto cerrado de JSON Schema (domain.schema)
```

(`JsonValue` ya se importa en ese archivo por `Prompt`/`DecisionModelDef`; si no, importarlo de `agent_core.domain.json`.)

- [ ] **Step 4: Implementar la regla**

En `agent_core/flows/rules/phase5.py` agregar `unsupported_keyword` al import de `agent_core.domain` y, junto a `g0_07`:

```python
def g0_24(ctx: Ctx) -> Iterator[Violation]:
    """Toda tool de un nodo `agent` se documenta para el modelo: `description` y un `args_schema` válido."""
    for node in ctx.flow.nodes:
        if not isinstance(node, AgentNode):
            continue
        for i, ref in enumerate(node.config.tools_allowed):
            tool = ctx.tool(ref)
            if tool is None:
                continue
            sub = f"/config/tools_allowed/{i}"
            if not tool.description or tool.args_schema is None:
                yield ctx.v("G0-24", node.id,
                            f"la tool {clip(ref.id)} de un nodo agent necesita description y args_schema", sub)
                continue
            problem = unsupported_keyword(tool.args_schema)
            if problem is not None:
                yield ctx.v("G0-24", node.id, f"args_schema de {clip(ref.id)}: {clip(problem)}", sub)
```

En `agent_core/flows/validate.py` agregar `g0_24` al import de `phase5` y a la tupla de reglas (junto a `g0_22`).

- [ ] **Step 5: Regenerar contratos y correr todo**

Run: `uv run agentcore contracts`
Run: `uv run agentcore contracts --check`
Run: `uv run pytest tests/m00 tests/m01 -q && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS. Si otros tests de M1 fallan porque una tool que usa un nodo `agent` no tiene documentación, agregar `description` y `args_schema` a esa tool en el fixture del test (no relajar la regla).

- [ ] **Step 6: Commit**

```bash
git add -A agent_core tests contracts
git commit -m "feat(m0,m1): ToolDef.description y args_schema, regla G0-24 para tools de nodos agent (T-M1-46)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `LLMAgentPort` (T-U5-13, 14, 15)

**Files:**
- Create: `agent_core/adapters/llm/agent_port.py`
- Modify: `agent_core/adapters/llm/__init__.py`, `.importlinter`
- Test: `tests/u05/test_agent_port.py`

**Interfaces:**
- Consumes: `LLMGateway`, `RegistryPort`; de `agent_core.interpreter`: `AgentPort`, `AgentRequest`, `AgentStepResult`, `AgentToolCall`, `AgentFinal`; `ToolDef` con `description`/`args_schema` (Task 9).
- Produces: `LLMAgentPort(gateway, registry, resolve_ref)` con `step(request: AgentRequest, state: RunState) -> AgentStepResult`; `STEP_SCHEMA: dict[str, JsonValue]`.

- [ ] **Step 1: Escribir las pruebas**

`tests/u05/test_agent_port.py`:

```python
"""`LLMAgentPort`: un paso del nodo `agent` sobre `generate` prompted (spec §3.8; T-U5-13..15)."""

from decimal import Decimal
from typing import Any

import pytest

from agent_core.adapters.llm import LLMAgentPort
from agent_core.adapters.llm.agent_port import STEP_SCHEMA
from agent_core.domain import (
    AgentNodeConfig,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    Prompt,
    SchemaError,
    ToolDef,
)
from agent_core.interpreter import AgentFinal, AgentObservation, AgentRequest, AgentToolCall
from agent_core.ports import GenerationResult, ToolStatus
from testing.builders import run_state
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.registry import InMemoryRegistry

ARGS_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {"q": {"type": "string"}}}
OUTPUT_SCHEMA: dict[str, JsonValue] = {"type": "object", "properties": {"resumen": {"type": "string"}}}


def _step(output: JsonValue, *, tokens_in: int = 40, tokens_out: int = 10) -> GenerationResult:
    return GenerationResult(output=output, tokens_in=tokens_in, tokens_out=tokens_out,
                            cost_usd=Decimal("0.001"), model="scripted-1")


def _port(*script: Any, docs: bool = True) -> tuple[LLMAgentPort, ScriptedGateway]:
    registry = InMemoryRegistry()
    extra: dict[str, Any] = {"description": "Busca un cargo", "args_schema": ARGS_SCHEMA} if docs else {}
    registry.add(
        ToolDef.model_validate({"id": "leer", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True, **extra}),
        Prompt.model_validate({"id": "p/agente", "version": "1.0.0", "locales": {"es": "bucle", "pt": "laço"},
                               "model_profile": "perfil@1.0.0"}))
    gateway = ScriptedGateway(script)
    return LLMAgentPort(gateway, registry, lambda kind, ref: ref.require_exact()), gateway


def _request(**over: Any) -> AgentRequest:
    config = AgentNodeConfig.model_validate({
        "tools_allowed": ["leer@1.0.0"], "max_steps": 3, "prompt_ref": "p/agente@1.0.0", "goal": "Investiga",
        "save_as": "hallazgo", "output_schema": OUTPUT_SCHEMA})
    return AgentRequest(node_id="investigar", config=config, step=over.pop("step", 1), **over)


def test_a_tool_call_step_becomes_an_agent_tool_call_with_usage() -> None:  # T-U5-13
    port, _ = _port(_step({"kind": "tool_call", "tool": "leer@1.0.0", "args": {"q": "cargo"}}))
    result = port.step(_request(), run_state())
    assert result.action == AgentToolCall(tool=EntityRef.parse("leer@1.0.0"), args={"q": "cargo"})
    assert (result.model_calls, result.tokens, result.cost_usd) == (1, 50, Decimal("0.001"))


def test_a_final_step_becomes_an_agent_final() -> None:  # T-U5-13
    port, _ = _port(_step({"kind": "final", "output": {"resumen": "ok"}}))
    assert port.step(_request(), run_state()).action == AgentFinal(output={"resumen": "ok"})


def test_the_gateway_gets_the_prompt_locale_step_schema_and_the_catalog() -> None:  # T-U5-14
    port, gateway = _port(_step({"kind": "final", "output": {}}))
    obs = AgentObservation(tool=EntityRef.parse("leer@1.0.0"), args={"q": "x"}, status=ToolStatus.ok,
                           result={"n": 2})
    port.step(_request(step=2, observations=(obs,), feedback="/: falta la propiedad 'resumen'"),
              run_state(locale="pt"))
    (call,) = gateway.calls
    assert str(call.prompt) == "p/agente@1.0.0" and call.locale == "pt" and call.schema == STEP_SCHEMA
    assert call.inputs == {
        "goal": "Investiga", "step": 2, "output_schema": OUTPUT_SCHEMA,
        "feedback": "/: falta la propiedad 'resumen'",
        "tools": [{"tool": "leer@1.0.0", "description": "Busca un cargo", "args_schema": ARGS_SCHEMA}],
        "observations": [{"tool": "leer@1.0.0", "args": {"q": "x"}, "status": "ok", "result": {"n": 2},
                          "error": None}]}


def test_a_tool_without_documentation_is_a_programming_error() -> None:  # T-U5-14
    port, gateway = _port(docs=False)
    with pytest.raises(SchemaError):
        port.step(_request(), run_state())
    assert gateway.calls == []


@pytest.mark.parametrize("output", [
    {"kind": "tool_call"},
    {"kind": "tool_call", "tool": "leer"},
    {"kind": "tool_call", "tool": "leer@1.0.0", "args": [1]},
    {"kind": "final"},
    {"kind": "otra"},
    "no es un objeto",
])
def test_a_malformed_step_is_invalid_output_with_the_usage(output: JsonValue) -> None:  # T-U5-15
    port, _ = _port(_step(output, tokens_in=4, tokens_out=2))
    with pytest.raises(GatewayError) as caught:
        port.step(_request(), run_state())
    error = caught.value
    assert error.kind is GatewayErrorKind.invalid_output
    assert (error.tokens_in, error.tokens_out, error.cost_usd, error.model) == (4, 2, Decimal("0.001"),
                                                                                  "scripted-1")


def test_a_tool_outside_tools_allowed_passes_through_for_m2_to_deny() -> None:  # T-U5-15
    port, _ = _port(_step({"kind": "tool_call", "tool": "escribir@1.0.0", "args": {}}))
    assert port.step(_request(), run_state()).action == AgentToolCall(
        tool=EntityRef.parse("escribir@1.0.0"), args={})


def test_a_missing_args_defaults_to_an_empty_object() -> None:
    port, _ = _port(_step({"kind": "tool_call", "tool": "leer@1.0.0"}))
    assert port.step(_request(), run_state()).action == AgentToolCall(
        tool=EntityRef.parse("leer@1.0.0"), args={})


def test_a_gateway_error_goes_up_unchanged() -> None:
    error = GatewayError(GatewayErrorKind.timeout, model="m")
    port, _ = _port(error)
    with pytest.raises(GatewayError) as caught:
        port.step(_request(), run_state())
    assert caught.value is error
```

Nota: `run_state(locale="pt")` acepta overrides como en `testing/builders.py` (ya lo usan `tests/m08` y `tests/composition`). Si `AgentRequest` no acepta el orden de argumentos por nombre, usar los nombres de campo de `interpreter/ports.py:AgentRequest` (`node_id`, `config`, `step`, `observations`, `feedback`).

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/u05/test_agent_port.py -v`
Expected: FAIL con `ImportError: cannot import name 'LLMAgentPort'`.

- [ ] **Step 3: Implementar**

`agent_core/adapters/llm/agent_port.py`:

```python
"""`LLMAgentPort`: un paso del bucle del nodo `agent` sobre `LLMGateway.generate` (spec §3.8, ADR 0019).

Solo hace **un paso**: el bucle, los presupuestos, la ejecución de tools, la validación contra
`output_schema` y la regeneración con `feedback` son de M2. Va en modo `prompted`, sin tool-calling nativo."""

from collections.abc import Callable

from agent_core.domain import (
    EntityKind,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    RefSpec,
    RunState,
    SchemaError,
    ToolDef,
)
from agent_core.interpreter import AgentFinal, AgentObservation, AgentRequest, AgentStepResult, AgentToolCall
from agent_core.ports import GenerationResult, LLMGateway, RegistryPort

# Esquema plano del paso, dentro del subconjunto de `domain.schema` (sin oneOf/anyOf).
STEP_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind"],
    "properties": {
        "kind": {"type": "string", "enum": ["tool_call", "final"]},
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "output": {},
    },
}

ResolveRef = Callable[[EntityKind, RefSpec], EntityRef]


class LLMAgentPort:
    def __init__(self, gateway: LLMGateway, registry: RegistryPort, resolve_ref: ResolveRef) -> None:
        self._gateway = gateway
        self._registry = registry
        self._resolve_ref = resolve_ref

    def step(self, request: AgentRequest, state: RunState) -> AgentStepResult:
        config = request.config
        inputs: dict[str, JsonValue] = {
            "goal": config.goal,
            "step": request.step,
            "tools": [self._catalog_entry(ref) for ref in config.tools_allowed],
            "observations": [_observation(o) for o in request.observations],
            "feedback": request.feedback,
            "output_schema": config.output_schema,
        }
        prompt = self._resolve_ref(EntityKind.prompt, config.prompt_ref)
        result = self._gateway.generate(prompt, inputs, state.locale, STEP_SCHEMA)
        return AgentStepResult(
            action=_action(result), model_calls=1, tokens=result.tokens_in + result.tokens_out,
            cost_usd=result.cost_usd)

    def _catalog_entry(self, ref: RefSpec) -> JsonValue:
        exact = self._resolve_ref(EntityKind.tool, ref)
        tool = self._registry.get(exact, ToolDef)
        if not tool.description or tool.args_schema is None:  # G0-24 lo impide en la validación estática
            raise SchemaError(f"la tool {exact} no tiene description y args_schema")
        return {"tool": str(exact), "description": tool.description, "args_schema": tool.args_schema}


def _observation(o: AgentObservation) -> JsonValue:
    return {"tool": str(o.tool), "args": o.args, "status": o.status.value, "result": o.result, "error": o.error}


def _action(result: GenerationResult) -> AgentToolCall | AgentFinal:
    out = result.output
    if isinstance(out, dict):
        if out.get("kind") == "final" and "output" in out:
            return AgentFinal(output=out["output"])
        args = out.get("args", {})
        tool = out.get("tool")
        if out.get("kind") == "tool_call" and isinstance(tool, str) and isinstance(args, dict):
            try:
                return AgentToolCall(tool=EntityRef.parse(tool), args=args)
            except InvalidRuntimeRef:
                pass  # `tool` sin versión exacta: paso inválido
    raise GatewayError(GatewayErrorKind.invalid_output, tokens_in=result.tokens_in,
                       tokens_out=result.tokens_out, cost_usd=result.cost_usd, model=result.model)
```

Agregar `InvalidRuntimeRef` al import de `agent_core.domain` de ese archivo.

En `agent_core/adapters/llm/__init__.py` agregar `from agent_core.adapters.llm.agent_port import LLMAgentPort` y `"LLMAgentPort"` a `__all__`.

En `.importlinter`, dentro del contrato `[importlinter:contract:adapters]`, agregar al final:

```
ignore_imports =
    agent_core.adapters.llm.agent_port -> agent_core.interpreter
```

- [ ] **Step 4: Correr y verificar**

Run: `uv run pytest tests/u05 -v && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: PASS. `lint-imports` debe seguir en verde y seguir prohibiendo `interpreter` a cualquier otro módulo de `adapters`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/adapters/llm tests/u05 .importlinter
git commit -m "feat(u5): LLMAgentPort, un paso del nodo agent sobre generate prompted (T-U5-13..15)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: M2 captura `GatewayError` de `AgentPort.step` (T-U5-17)

**Files:**
- Modify: `agent_core/interpreter/handlers/agent.py` (`handle_agent`)
- Test: `tests/m02/test_agent.py`

**Interfaces:**
- Consumes: `charge_model` (ya importada en el archivo), `GatewayError`.
- Produces: un `GatewayError` de `step` termina el nodo con `result_key = "gave_up"` y carga a `budgets_used` lo que el error informe.

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/m02/test_agent.py` (los imports `Decimal`, `pytest`, `_world`, `_node` ya existen; agregar `GatewayError`, `GatewayErrorKind` a los de `agent_core.domain`):

```python
class FailingAgent:
    """`AgentPort` cuyo paso falla con la excepción dada."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def step(self, request: Any, state: Any) -> AgentStepResult:
        raise self.error


def test_t_u5_17_a_gateway_error_ends_the_node_as_gave_up_and_charges_the_reported_usage() -> None:
    w, _, state = _world()
    error = GatewayError(GatewayErrorKind.invalid_output, tokens_in=7, tokens_out=3, cost_usd=Decimal("0.004"),
                         model="m")
    out = w.step(state, agents=FailingAgent(error))
    assert _node(out) == "esc"  # gave_up
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 10, Decimal("0.004"))


def test_t_u5_17_a_gateway_error_without_usage_charges_the_call_only() -> None:
    w, _, state = _world()
    out = w.step(state, agents=FailingAgent(GatewayError(GatewayErrorKind.timeout)))
    used = out.state.budgets_used
    assert _node(out) == "esc"
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 0, Decimal("0"))


def test_t_u5_17_any_other_exception_still_goes_up() -> None:
    w, _, state = _world()
    with pytest.raises(RuntimeError):
        w.step(state, agents=FailingAgent(RuntimeError("error de programación")))
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/m02/test_agent.py -k t_u5_17 -v`
Expected: FAIL (`GatewayError` sube desde `handle_agent`).

- [ ] **Step 3: Implementar**

En `agent_core/interpreter/handlers/agent.py` agregar `GatewayError` al import de `agent_core.domain` y `from decimal import Decimal` si no está, y reemplazar

```python
        started = ctx.clock.monotonic_ns()
        result = ctx.agents.step(
            AgentRequest(node.id, node.config, step, tuple(loop.observations), feedback), loop.state)
```

por

```python
        started = ctx.clock.monotonic_ns()
        try:
            result = ctx.agents.step(
                AgentRequest(node.id, node.config, step, tuple(loop.observations), feedback), loop.state)
        except GatewayError as error:
            # La falla del modelo no escapa del turno: el nodo se rinde y cuenta lo que el proveedor informó.
            loop.state = charge_model(
                loop.state, calls=1, tokens=(error.tokens_in or 0) + (error.tokens_out or 0),
                cost=error.cost_usd or Decimal("0"))
            break
```

- [ ] **Step 4: Correr y verificar**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02
git commit -m "feat(m2): un GatewayError del paso del nodo agent termina el nodo en gave_up (T-U5-17)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Cableado en composition e integración (T-U5-16, T-U5-18)

**Files:**
- Modify: `agent_core/composition/runtime.py`
- Test: `tests/composition/test_runtime.py`, `tests/u05/test_agent_integration.py`

**Interfaces:**
- Consumes: `LLMAgentPort` (Task 10), `release_view` de `agent_core.flows`.
- Produces: `release_resolver(registry, release) -> Callable[[EntityKind, RefSpec], EntityRef]` en `runtime.py`; `EngineRuntimeFactory.open` inyecta `StepContext.agents`.

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/composition/test_runtime.py`:

```python
from agent_core.adapters.llm import LLMAgentPort


def test_t_u5_18_open_inyecta_el_agent_port_real() -> None:
    w = EngineWorld()
    _, runtime = _open(w)
    assert isinstance(runtime.step.agents, LLMAgentPort)
```

`tests/u05/test_agent_integration.py`:

```python
"""Nodo `agent` de M2 con `LLMAgentPort` sobre `OpenAICompatGateway` (spec §3.8; T-U5-16)."""

from decimal import Decimal

import httpx
from respx import MockRouter

from agent_core.adapters.llm import LLMAgentPort
from agent_core.domain import Prompt, ToolDef
from tests.m02.harness import World, flow
from tests.u05.helpers import CHAT, build_gateway, completion, install_llm_entities

BUSCAR = EntityRef(id="buscar", version="1.0.0")
SCHEMA = {"type": "object", "properties": {"resumen": {"type": "string"}}, "required": ["resumen"],
          "additionalProperties": False}
TAIL = [{"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}}]
AGENT = {"id": "a", "type": "agent", "next": {"answered": "fin", "gave_up": "esc"},
         "config": {"tools_allowed": ["buscar@1.0.0"], "max_steps": 3, "prompt_ref": "p/x@1.0.0",
                    "goal": "g", "save_as": "hallazgo", "output_schema": SCHEMA}}


def _world() -> tuple[World, LLMAgentPort]:
    w = World()
    w.add_tool(
        ToolDef.model_validate({"id": "buscar", "version": "1.0.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True, "source": "tx",
                                "description": "Busca un cargo", "args_schema": {"type": "object"}}),
        handler=lambda a: {"n": 2})
    install_llm_entities(w.registry)
    w.registry.add(Prompt.model_validate(
        {"id": "p/x", "version": "1.0.0", "locales": {"es": "bucle"}, "model_profile": "perfil@1.0.0"}))
    port = LLMAgentPort(build_gateway(w.registry), w.registry, lambda kind, ref: ref.require_exact())
    return w, port


def test_t_u5_16_the_loop_reaches_answered_and_accumulates_the_cost(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).mock(side_effect=[
        httpx.Response(200, json=completion('{"kind": "tool_call", "tool": "buscar@1.0.0", "args": {}}')),
        httpx.Response(200, json=completion('{"kind": "final", "output": {"resumen": "ok"}}'))])
    w, port = _world()
    out = w.step(w.state(flow(AGENT, *TAIL)), agents=port)
    assert out.state.active_flow.node_id == "fin"
    assert out.state.facts["hallazgo"].value == {"resumen": "ok"}
    used = out.state.budgets_used
    assert used.turn_model_calls == 2 and used.run_tokens == 300
    assert used.run_cost == Decimal("0.001620")  # 2 x (120 x 3 + 30 x 15) / 1e6
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/composition/test_runtime.py tests/u05/test_agent_integration.py -v`
Expected: `test_t_u5_18` FALLA (`agents is None`); `test_t_u5_16` puede pasar ya (no depende de composition) — está bien: pinea el comportamiento extremo a extremo.

- [ ] **Step 3: Implementar el cableado**

En `agent_core/composition/runtime.py`:

1. Agregar `from agent_core.adapters.llm import LLMAgentPort` con los demás imports de `agent_core`.
2. Agregar, encima de `class EngineRuntimeFactory`, el ayudante compartido:

```python
def release_resolver(registry: RegistryPort, release: Release) -> Callable[[EntityKind, RefSpec], EntityRef]:
    """`resolve_ref` de la release del run: referencia de autoría → referencia exacta (lo usan M8 y el nodo agent)."""
    view = release_view(registry, release)

    def resolve_ref(kind: EntityKind, ref: RefSpec) -> EntityRef:
        entity = view.resolve(kind, ref)
        if entity is None:
            raise InvalidRuntimeRef(f"{kind.value} {ref} no está en la release {release.id}")
        return EntityRef(id=entity.id, version=entity.version)

    return resolve_ref
```

3. En `open`, agregar al `StepContext(...)` el argumento `agents=LLMAgentPort(self._gateway, self._registry, release_resolver(self._registry, release)),`.
4. En `_context`, reemplazar las líneas `view = release_view(step.registry, step.release)` y la función anidada `resolve_ref` por `resolve_ref = release_resolver(step.registry, step.release)`.

- [ ] **Step 4: Correr todo**

Run: `uv run pytest tests/composition tests/u05 tests/m02 tests/m08 -q && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS. Los escenarios de composition existentes (`test_engine.py`, replay) deben seguir en verde.

- [ ] **Step 5: Commit**

```bash
git add agent_core/composition tests
git commit -m "feat(u5): composition inyecta LLMAgentPort por run y el bucle del nodo agent llega a answered (T-U5-16, T-U5-18)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Prueba de humo `agentcore llm-smoke`

**Files:**
- Create: `agent_core/adapters/llm/smoke.py`
- Modify: `agent_core/cli.py`
- Test: `tests/u05/test_smoke.py`

**Interfaces:**
- Consumes: `OpenAICompatGateway`, `load_endpoints`, `_AuthoringAgents` (ya en `cli.py`), `load_registry`, `SystemClock`.
- Produces:
  - `run_smoke(gateway: LLMGateway, prompt: EntityRef, clock: Clock, n: int = 10) -> SmokeReport`.
  - `SmokeReport` con `calls`, `ok`, `errors: dict[str, int]`, `latencies_ms`, `tokens_in`, `tokens_out`, `cost_usd`, `p50_ms`, `p95_ms`.
  - `SmokeRegistry(inner: RegistryPort, profile: EntityRef)`: `RegistryPort.get` que además sirve el prompt sintético `llm-smoke@0.0.0`.
  - `format_report(report) -> str`.
  - Subcomando `agentcore llm-smoke --registry DIR --profile id@v [--n 10]`.

- [ ] **Step 1: Escribir las pruebas**

`tests/u05/test_smoke.py`:

```python
"""Prueba de humo del gateway (spec §7): `run_smoke`, su reporte y el subcomando `llm-smoke`."""

from decimal import Decimal

from agent_core.adapters.llm.smoke import SMOKE_PROMPT, SmokeRegistry, format_report, percentile, run_smoke
from agent_core.cli import main
from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind, ModelProfile, Prompt
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway, gen
from testing.fakes.registry import InMemoryRegistry
from tests.u05.helpers import PROFILE, install_llm_entities


def test_percentile_uses_the_nearest_rank() -> None:
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50 and percentile(values, 95) == 100 and percentile([], 50) == 0


def test_run_smoke_counts_ok_errors_tokens_and_cost() -> None:
    script = [gen("hola", []) for _ in range(8)] + [
        GatewayError(GatewayErrorKind.invalid_output, tokens_in=5, tokens_out=1, cost_usd=Decimal("0.0001")),
        GatewayError(GatewayErrorKind.timeout)]
    report = run_smoke(ScriptedGateway(script), SMOKE_PROMPT, FakeClock(), n=10)
    assert (report.calls, report.ok) == (10, 8)
    assert report.errors == {"invalid_output": 1, "timeout": 1}
    assert report.tokens_in == 8 * 10 + 5 and report.tokens_out == 8 * 5 + 1
    assert report.cost_usd == Decimal("0.008") + Decimal("0.0001")
    text = format_report(report)
    assert "invalid_output" in text and "p50" in text and "p95" in text


def test_run_smoke_alternates_es_and_pt() -> None:
    gateway = ScriptedGateway([gen("x", []) for _ in range(4)])
    run_smoke(gateway, SMOKE_PROMPT, FakeClock(), n=4)
    assert [c.locale for c in gateway.calls] == ["es", "pt", "es", "pt"]


def test_the_smoke_registry_serves_a_synthetic_prompt_that_uses_the_profile() -> None:
    inner = InMemoryRegistry()
    install_llm_entities(inner)
    registry = SmokeRegistry(inner, PROFILE)
    prompt = registry.get(SMOKE_PROMPT, Prompt)
    assert str(prompt.model_profile.require_exact()) == "perfil@1.0.0" and set(prompt.locales) == {"es", "pt"}
    assert registry.get(PROFILE, ModelProfile).model == "vendor/modelo-x"  # lo demás va al registro real


def test_llm_smoke_without_endpoints_is_a_usage_error(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("LLM_ENDPOINTS", raising=False)
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil@1.0.0"])
    assert code == 2 and "LLM_ENDPOINTS" in capsys.readouterr().err


def test_llm_smoke_rejects_a_non_exact_profile(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LLM_ENDPOINTS", '{"o": {"base_url": "https://x.test/v1", "api_key_env": "K"}}')
    code = main(["llm-smoke", "--registry", str(tmp_path), "--profile", "perfil"])
    assert code == 2 and "id@versión" in capsys.readouterr().err
```

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/u05/test_smoke.py -v`
Expected: FAIL con `ModuleNotFoundError: agent_core.adapters.llm.smoke`.

- [ ] **Step 3: Implementar `smoke.py`**

`agent_core/adapters/llm/smoke.py`:

```python
"""Prueba de humo del gateway (spec §7): prompts sintéticos ES/PT contra el endpoint configurado."""

from dataclasses import dataclass, field
from decimal import Decimal
from math import ceil

from agent_core.domain import (
    EntityRef,
    GatewayError,
    JsonValue,
    Locale,
    ModelProfile,
    Prompt,
    RegistryEntity,
    SchemaError,
)
from agent_core.ports import Clock, LLMGateway, RegistryPort

SMOKE_PROMPT = EntityRef(id="llm-smoke", version="0.0.0")
_NS_PER_MS = 1_000_000
_SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["text", "citations"],
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}
_TEXTS: dict[Locale, str] = {
    "es": "Redacta una respuesta breve y cordial para el cliente sobre el asunto indicado. No inventes cifras.",
    "pt": "Redija uma resposta breve e cordial ao cliente sobre o assunto indicado. Não invente números."}
_TOPICS: dict[Locale, list[str]] = {
    "es": ["cargo desconocido en tienda", "tarjeta bloqueada", "demora de una transferencia",
           "cambio de dirección", "consulta de saldo"],
    "pt": ["cobrança desconhecida em loja", "cartão bloqueado", "atraso em uma transferência",
           "mudança de endereço", "consulta de saldo"]}


@dataclass
class SmokeReport:
    calls: int = 0
    ok: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[int] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")

    @property
    def p50_ms(self) -> int:
        return percentile(self.latencies_ms, 50)

    @property
    def p95_ms(self) -> int:
        return percentile(self.latencies_ms, 95)


def percentile(values: list[int], q: int) -> int:
    """Rango más cercano; 0 si no hay valores."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(ceil(q / 100 * len(ordered)) - 1, 0)]


class SmokeRegistry:
    """`RegistryPort.get` que además sirve el prompt sintético, atado al perfil que se prueba."""

    def __init__(self, inner: RegistryPort, profile: EntityRef) -> None:
        self._inner = inner
        self._prompt = Prompt(id=SMOKE_PROMPT.id, version=SMOKE_PROMPT.version, locales=dict(_TEXTS),
                              model_profile=f"{profile.id}@{profile.version}")  # type: ignore[arg-type]

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        if ref == SMOKE_PROMPT:
            if kind is not Prompt:
                raise SchemaError(f"{ref} es un prompt")
            return self._prompt  # type: ignore[return-value]
        return self._inner.get(ref, kind)


def run_smoke(gateway: LLMGateway, prompt: EntityRef, clock: Clock, n: int = 10) -> SmokeReport:
    report = SmokeReport()
    for i in range(n):
        locale: Locale = "es" if i % 2 == 0 else "pt"
        topic = _TOPICS[locale][(i // 2) % len(_TOPICS[locale])]
        started = clock.monotonic_ns()
        report.calls += 1
        try:
            result = gateway.generate(prompt, {"asunto": topic}, locale, _SCHEMA)
        except GatewayError as error:
            report.errors[error.kind.value] = report.errors.get(error.kind.value, 0) + 1
            report.tokens_in += error.tokens_in or 0
            report.tokens_out += error.tokens_out or 0
            report.cost_usd += error.cost_usd or Decimal("0")
        else:
            report.ok += 1
            report.tokens_in += result.tokens_in
            report.tokens_out += result.tokens_out
            report.cost_usd += result.cost_usd
        report.latencies_ms.append((clock.monotonic_ns() - started) // _NS_PER_MS)
    return report


def format_report(report: SmokeReport) -> str:
    errors = ", ".join(f"{kind}={count}" for kind, count in sorted(report.errors.items())) or "ninguno"
    rate = report.errors.get("invalid_output", 0) / report.calls if report.calls else 0
    return "\n".join([
        f"llamadas: {report.calls}  ok: {report.ok}  errores: {errors}",
        f"tasa de invalid_output: {rate:.0%}",
        f"latencia p50: {report.p50_ms} ms  p95: {report.p95_ms} ms",
        f"tokens: {report.tokens_in} entrada / {report.tokens_out} salida  costo: {report.cost_usd} USD"])
```

Nota: `Prompt(... model_profile=...)` acepta el `str` porque `RefSpec` valida desde texto (`RefSpec.parse`); si `mypy` se queja, construir con `Prompt.model_validate({...})` en lugar de los argumentos por nombre y quitar los `type: ignore`.

- [ ] **Step 4: Implementar el subcomando en `cli.py`**

En `agent_core/cli.py`:

1. Imports: `from agent_core.adapters.llm import OpenAICompatGateway, load_endpoints` y `from agent_core.adapters.llm.smoke import SMOKE_PROMPT, SmokeRegistry, format_report, run_smoke`.
2. Función:

```python
def _run_llm_smoke(args: argparse.Namespace) -> int:
    try:
        endpoints = load_endpoints(os.environ)
        if not endpoints:
            print("llm-smoke necesita LLM_ENDPOINTS con al menos un endpoint", file=sys.stderr)
            return USAGE_ERROR
        try:
            profile = EntityRef.parse(args.profile)
        except DomainError:
            print("--profile debe ser id@versión exacta", file=sys.stderr)
            return USAGE_ERROR
    except SchemaError as error:
        print(str(error), file=sys.stderr)
        return USAGE_ERROR
    registry, _ = load_registry(args.registry)
    gateway = OpenAICompatGateway(SmokeRegistry(_AuthoringAgents(registry), profile), endpoints, os.environ)
    report = run_smoke(gateway, SMOKE_PROMPT, SystemClock(), n=args.n)
    print(format_report(report))
    return 0 if report.ok > 0 else 1
```

(agregar `DomainError` al import de `agent_core.domain`.)

3. En `main`: el docstring del módulo pasa a listar `llm-smoke`; agregar

```python
    smoke = sub.add_parser("llm-smoke", help="prueba de humo del gateway de LLM (spec del gateway §7)")
    smoke.add_argument("--registry", type=Path, required=True, help="directorio del registro de autoría")
    smoke.add_argument("--profile", required=True, help="model_profile id@versión exacta")
    smoke.add_argument("--n", type=int, default=10, help="cantidad de llamadas (por defecto 10)")
```

y, antes de `return 2`, `if args.command == "llm-smoke": return _run_llm_smoke(args)`.

- [ ] **Step 5: Correr y verificar**

Run: `uv run pytest tests/u05/test_smoke.py tests/m01/test_cli.py -q && uv run mypy && uv run ruff check . && uv run lint-imports`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_core tests
git commit -m "feat(u5): agentcore llm-smoke con prompts sinteticos ES/PT y reporte de latencia y costo" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Specs afectadas, verificación final y prueba de humo real

**Files:**
- Modify: `docs/specs/motor/m00-dominio-y-contratos.md`, `m01-validacion-estatica.md`, `m02-interprete.md`, `m08-validador-de-respuesta.md`, `00-indice.md`, `docs/adr/0016-llm-gateway-propio-compatible-openai.md`, `docs/specs/2026-09-28-llm-gateway-design.md`

- [ ] **Step 1: Actualizar las specs (mismo cambio, regla del repo)**

- `m00-dominio-y-contratos.md`: en la definición de `ToolDef` agregar `description: str | None` y `args_schema: dict | None` con una línea de porqué (catálogo del nodo `agent`, unidad 5), y anotar que `check_output` es ahora de `domain` (`schema.py`).
- `m01-validacion-estatica.md`: agregar la regla **G0-24** a la tabla y a la sección §3.13 (agentes internos): «toda tool de `tools_allowed` de un nodo `agent` lleva `description` y un `args_schema` dentro del subconjunto cerrado», con la prueba `T-M1-46`; marcarla como implementada.
- `m02-interprete.md`: §3.7, agregar que un `GatewayError` de `AgentPort.step` termina el nodo en `gave_up` cargando el uso informado; §11, tachar el abierto del adaptador real de `AgentPort` con enlace a la spec del gateway; cambiar la mención de `interpreter/schema.py` a `agent_core.domain.schema`.
- `m08-validador-de-respuesta.md`: §3.2 y §5, reemplazar «Gateway caído → directo a plantilla» por la tabla por `kind` (`invalid_output` regenera una vez; `timeout`, `unavailable`, `rate_limited` y `refused` directo a la plantilla); agregar `T-M8-12`.
- `00-indice.md`: en la fila de `LLMGateway`, «Implementa: `OpenAICompatGateway` (unidad 5)»; agregar `LLMAgentPort` como implementación de `AgentPort`.
- `2026-09-28-llm-gateway-design.md`: cambiar el estado a «rev. 2, implementada» y marcar la Definición de terminado punto por punto; en §3.5 agregar «el SDK `openai` registra el contenido de los requests en DEBUG: los loggers `openai` y `httpx` no se activan en DEBUG en ningún entorno con datos reales».

- [ ] **Step 2: Verificación completa**

Run: `uv run pytest -q -m "not integration and not perf"`
Run: `uv run mypy && uv run ruff check . && uv run lint-imports && uv run agentcore contracts --check`
Expected: todo en verde. Con Postgres disponible (`docker compose up -d postgres`) correr además `uv run pytest -q -m integration`.

- [ ] **Step 3: Prueba de humo real contra OpenRouter (manual, requiere la key del usuario)**

El usuario debe exportar `LLM_ENDPOINTS` y `OPENROUTER_API_KEY` en su shell (nunca en el repo ni en el chat) y tener un `ModelProfile` publicado en el registro de demo (`tests/fixtures/registry-demo` o el suyo) con `endpoint_alias: openrouter`, un modelo de OpenRouter que soporte ES/PT y `structured: prompted`.

Run: `uv run agentcore llm-smoke --registry <directorio-del-registro> --profile <id@versión> --n 10`
Expected: reporte con `ok` cercano a 10, tasa de `invalid_output` baja, latencia y costo. Anotar el resultado (fecha, modelo, tasa, p50/p95, costo) en la sección «Resultado de la prueba de humo» de la enmienda del ADR 0016.

- [ ] **Step 4: Commit final**

```bash
git add docs
git commit -m "docs(u5): specs de M0, M1, M2, M8 y del indice reflejan el gateway y LLMAgentPort" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review (hecha al escribir el plan)

**Cobertura de la spec.** §2 interfaz → Tasks 2, 3, 7, 9, 10. §3.1 (`generate`, tope total) → 3, 4, 5. §3.2 → 4. §3.3 (M8) → 8. §3.4 → 2. §3.5 → 6. §3.6 (costo por principal, ya aplicado) → verificado por T-U5-16 en 12. §3.7 → 1. §3.8 → 10, 11, 12. §7 pruebas: T-U5-01 (7), 02–03 (3), 04–06 y 08 (4), 07 (2), 09 (6), 10 (3), 11 (5), 12 (1), 13–15 (10), 16 y 18 (12), 17 (11), T-M8-12 (8), T-M1-46 (9); prueba de humo (13, 14). §10: `openai`/`respx`/`.env.example` (0), CLI (13), specs (14). Sin huecos.

**Placeholders.** Ninguno. Los ids `T-M8-12` y `T-M1-46` son los de la spec. La Task 3 marca un único test con `skip` hasta la Task 4 y lo dice; la implementación de `gateway.py` de la Task 3 es deliberadamente parcial (camino feliz) y la Task 4 da su versión completa.

**Consistencia de tipos.** `OpenAICompatGateway(registry, endpoints, env, client_factory, tracer)` es el mismo en Tasks 3, 4, 6, 7 y en `tests/u05/helpers.py` (Task 3, `tracer` llega en la 6 y `helpers.build_gateway` ya lo reenvía por `**kwargs`). `LLMAgentPort(gateway, registry, resolve_ref)` igual en 10, 12 y en el test de integración. `check_output`/`unsupported_keyword` en `agent_core.domain` desde la Task 1 y consumidos por 3, 9 y 10. `GatewayError(kind, tokens_in, tokens_out, cost_usd, model)` es el de M0.

**Riesgos que el ejecutor debe vigilar.** (1) `run_state(locale="pt")` y los nombres exactos de los campos de `AgentRequest` se toman de `testing/builders.py` e `interpreter/ports.py`; si difieren, ajustar solo las pruebas. (2) `Prompt(..., model_profile=str)` en `SmokeRegistry` depende de que `RefSpec` valide desde texto; hay una alternativa indicada. (3) Al agregar `description`/`args_schema` obligatorios por G0-24, algún fixture de M1/M2/composition con un nodo `agent` puede necesitar documentar su tool: corregir el fixture, no la regla.
