# Spec — LLM gateway (unidad 5)

- Estado: borrador para revisión · se construye en paralelo a las fases 2 y 3 (01/10); M8 lo necesita en la fase 4 (02/10)
- Fecha: 2026-09-28
- Repo: `agent-core`
- Paquete: `agent_core.adapters.llm`
- ADRs: 0016 (gateway propio compatible con OpenAI), 0001 (stack), 0003 (observabilidad), 0008 (vista `model`)
- Usa: M0 (`LLMGateway`, `RegistryPort`, `ModelProfile`, `Prompt`, `GatewayError`) · Lo usan: M8 (`respond(generate)`), M5 (`llm_structured`, solo baseline)
- Autor: Juan Zapata, con Claude

## 1. Propósito y límites

Implementa el puerto `LLMGateway` de M0: recibe un `prompt@v` exacto y entradas en vista `model`, llama a un modelo generativo por una API compatible con OpenAI y devuelve la salida validada con su uso (tokens y costo en `Decimal`).

El proveedor todavía no está decidido. El gateway lo hace irrelevante para el resto del núcleo: el endpoint se elige por configuración y el modelo por un `ModelProfile` versionado en el registro.

**No hace:**
- **Recuperación:** no reintenta ni cambia de proveedor. La cadena generar → regenerar → plantilla es de M8.
- **Estado:** no guarda contadores. El costo por principal lo acumula M4 al commitear el turno (§3.6).
- **Auditoría:** no emite eventos. El uso llega al log por `response_emitted.llm`, que arma M8.
- **PII:** no tokeniza. Las entradas ya llegan en vista `model` (M7).
- Tampoco valida contenido (M8), ni hace streaming, caché, varios proveedores con respaldo o gestión de prompts fuera del registro (§9).

## 2. Interfaz pública

Tipos de M0 (rev. 5):

```python
class StructuredMode(StrEnum): native, prompted
class ModelPrice:     input_per_mtok: Decimal; output_per_mtok: Decimal     # USD por millón de tokens
                      source: str; as_of: date                             # de dónde salió el precio y cuándo
class ModelProfile:   id: str; version: str; endpoint_alias: str; model: str
                      temperature: Decimal; max_tokens: int; timeout_s: int = 8
                      structured: StructuredMode = native; price: ModelPrice
class Prompt:         id; version; locales: dict[Locale, str]; reads: frozenset[str]
                      model_profile: RefSpec                               # nuevo en rev. 5

class GenerationResult: output: JsonValue; tokens_in: int; tokens_out: int; cost_usd: Decimal; model: str
class GatewayErrorKind(StrEnum): timeout, unavailable, rate_limited, invalid_output, refused
class GatewayError(DomainError):
    kind: GatewayErrorKind
    tokens_in: int | None; tokens_out: int | None; cost_usd: Decimal | None; model: str | None   # uso parcial, si el proveedor lo informó

class LLMGateway(Protocol):
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult     # falla → GatewayError
```

Adaptador (`agent_core/adapters/llm/`):

```python
class EndpointConfig: alias: str; base_url: str; api_key_env: str        # nombre de la variable, nunca la key
def load_endpoints(env: Mapping[str, str]) -> dict[str, EndpointConfig]  # lee LLM_ENDPOINTS
class OpenAICompatGateway(LLMGateway):
    def __init__(self, registry: RegistryPort, endpoints: dict[str, EndpointConfig],
                 client_factory: Callable[[EndpointConfig, int], OpenAI] = default_client) -> None
def price_of(price: ModelPrice, tokens_in: int, tokens_out: int) -> Decimal
```

**Configuración:** `LLM_ENDPOINTS` es un JSON `{alias: {base_url, api_key_env}}`. Ejemplos de alias: `openrouter`, `openai`, `local` (vLLM u Ollama), `litellm-proxy`. Las keys viven solo en las variables que nombra `api_key_env`.

## 3. Comportamiento

### 3.1 `generate`

1. `prompt = registry.get(prompt_ref, Prompt)` y `profile = registry.get(prompt.model_profile, ModelProfile)`. Las dos referencias son exactas porque vienen de una release (M0 §2.2).
2. `text = prompt.locales[locale]`. Si falta el locale es un error de programación, porque M1 lo impide (G0-12): lanza `SchemaError`.
3. Arma los mensajes:
   - `system`: `text`, **sin sustitución de variables**. Con `structured = prompted`, se le agrega al final la instrucción de responder solo con JSON que cumpla `schema` (el esquema en JSON canónico).
   - `user`: `canonical_bytes(inputs_model_view)` decodificado como texto. Los campos `untrusted_text` ya vienen envueltos por M7.
4. Llama `client.chat.completions.create(model=profile.model, messages, temperature, max_tokens, response_format?)`, donde el cliente tiene `timeout = profile.timeout_s` y **`max_retries = 0`**. Con `structured = native` y `schema`, `response_format = {type: json_schema, json_schema: {name, schema, strict: true}}`.
5. Lee `usage.prompt_tokens` y `usage.completion_tokens`, y calcula `cost_usd = price_of(profile.price, tokens_in, tokens_out)`.
6. Interpreta la salida:
   - con `schema`: parsea el contenido con `agent_core.domain.loads` (los números con decimales quedan como `Decimal`) y lo valida contra `schema`;
   - sin `schema`: `output` es el texto.
7. Devuelve `GenerationResult`, con `model` igual al modelo que informó la respuesta (puede diferir del pedido si el endpoint enruta).

### 3.2 Traducción de errores

| Causa | `GatewayError.kind` |
|---|---|
| Timeout del cliente | `timeout` |
| Error de conexión, 5xx, alias no configurado o variable de key vacía | `unavailable` |
| 429 del proveedor | `rate_limited` |
| La salida no parsea como JSON o no cumple `schema`; `finish_reason = length` con `schema` | `invalid_output` |
| Rechazo o filtro de contenido (`finish_reason = content_filter` o campo `refusal`) | `refused` |

- Con `invalid_output` y `refused` el proveedor sí informó el uso: va en el error con su costo.
- En los demás casos, el uso va si el proveedor lo informó, y si no, en `None`.
- Ninguna otra excepción sale del adaptador: todo lo no previsto es `unavailable`, y el detalle va al log técnico sin contenido.

### 3.3 Qué hace M8 con cada error (cambia M8 §3.2 y §5)

- `invalid_output` cuenta como falla de la comprobación de formato: **1 regeneración** y luego la plantilla.
- `timeout`, `unavailable`, `rate_limited` y `refused` van **directo a la plantilla**, sin regenerar.
- Toda llamada, exitosa o no, suma a `LlmUsage`: `calls + 1`, latencia medida por M8 y tokens y costo si se conocen. Si alguna llamada no informó uso, `cost_known = false`.

### 3.4 Costo

- `price_of = (tokens_in × input_per_mtok + tokens_out × output_per_mtok) / 1 000 000`, en `Decimal` y redondeado a 6 decimales con `ROUND_HALF_EVEN`.
- El gateway nunca usa el costo que calcule un SDK o un proveedor en `float`.
- El precio es parte del `ModelProfile`: cambiarlo es una versión nueva del perfil y queda en la release.

### 3.5 Observabilidad

- Un span `chat` por llamada, con:
  - las semconv GenAI de la versión fijada: `gen_ai.operation.name = chat`, `gen_ai.provider.name` (el alias), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` y `gen_ai.usage.output_tokens`;
  - los atributos propios `agentcore.prompt`, `agentcore.model_profile` y `agentcore.gateway.error_kind`.
- Captura de contenido desactivada (ADR 0003). Nunca van a spans ni logs la key, los headers ni el contenido de mensajes o respuestas.
- La latencia que llega al log de auditoría la mide M8 con el `Clock` (M0 §2.10). El span tiene la suya propia.

### 3.6 Costo por principal (cambia M4, M9 y M0)

- M4, en el paso 14 (persistir), suma el costo del turno y llama `uow.add_usage(principal.key, cost, now)` dentro de la misma transacción.
  - El costo es Σ `decision_made.cost_usd` + Σ `response_emitted.llm.cost_usd` del turno. Las llamadas sin uso conocido suman 0.
  - `add_usage` también suma 1 a los hits del principal.
- M9 lee con `CostCounters.spent_today` y `hits` (sin cambios). `CostCounters` pasa de la unidad 5 a M4 (dueño) y la implementa la misma tabla que escribe la UoW.
- Un turno rechazado por M9 (pasos 1–5) no suma hits: el límite de tasa cuenta turnos procesados.

## 4. Invariantes

- Solo entran al gateway datos en vista `model`: la firma lo nombra y T-M7-01 lo verifica capturando requests.
- `max_retries = 0` en todo cliente: una llamada a `generate` hace a lo sumo una request HTTP.
- `cost_usd` es `Decimal` calculado con `ModelPrice`; nunca `float`.
- El texto del prompt que se envía es byte a byte el `Prompt.locales[locale]` de la release, más la instrucción de esquema en modo `prompted`.
- El gateway no lee la hora, no genera IDs y no guarda estado entre llamadas.
- Ninguna key sale de su variable de entorno: ni al registro, ni a eventos, spans, logs o errores.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Proveedor caído o lento | `GatewayError(timeout\|unavailable)` → M8 usa la plantilla; el turno sigue |
| Alias del perfil sin configurar | `unavailable` y log de error con el alias (sin secretos). Al arrancar, la demo avisa por cada perfil de la release activa cuyo alias falte |
| El endpoint no soporta `json_schema` | el proveedor responde 4xx → `unavailable`; se corrige con un perfil `prompted` (versión nueva) |
| Salida truncada por `max_tokens` | `invalid_output` si hay esquema; texto truncado si no (M8 lo valida) |
| Precio desactualizado | el costo reportado difiere de la factura; se corrige publicando otra versión del perfil |

## 6. Eventos que emite

Ninguno. El uso va en `response_emitted.llm` (M8) y, para `llm_structured`, en `decision_made` (M5).

## 7. Pruebas

Sin red: el adaptador se prueba con un transporte HTTP falso (`respx` sobre `httpx`).

| ID | Caso |
|---|---|
| T-U5-01 | Suite de contrato de `LLMGateway`, parametrizada por `ScriptedGateway` y `OpenAICompatGateway` (con transporte falso): salida válida, tokens y costo `Decimal`, cada `GatewayErrorKind` |
| T-U5-02 | El request lleva `model`, `temperature` y `max_tokens` del perfil, `system` igual a `Prompt.locales[locale]` y `user` igual al JSON canónico de las entradas |
| T-U5-03 | `native` envía `response_format` `json_schema` estricto; `prompted` no lo envía y agrega la instrucción al `system` |
| T-U5-04 | Salida que no cumple el esquema → `invalid_output` con el uso informado |
| T-U5-05 | Timeout → `timeout`; 503 → `unavailable`; 429 → `rate_limited`; `content_filter` → `refused`; excepción no prevista → `unavailable` |
| T-U5-06 | El cliente se construye con `max_retries = 0`: un 503 produce exactamente una request |
| T-U5-07 | `price_of` con precios y tokens conocidos da el `Decimal` esperado, redondeado a 6 decimales |
| T-U5-08 | Alias no configurado o variable de key vacía → `unavailable` sin request |
| T-U5-09 | Ni la key ni el contenido aparecen en spans, logs o el mensaje de `GatewayError` (captura de logs y exportador de spans en memoria) |
| T-U5-10 | Números con decimales en la salida estructurada llegan como `Decimal` |

**Prueba de humo (manual, fuera de CI):** `agentcore llm-smoke --profile <id@v>` corre 10 prompts sintéticos ES/PT contra el endpoint configurado y reporta la tasa de `invalid_output`, la latencia p50/p95 y el costo. Se corre al elegir proveedor y antes de promover un perfil nuevo.

## 8. Evaluación

Por `model_profile@v` y por `prompt@v`, desde `response_emitted.llm` y los spans:

- latencia p50/p95;
- tasa de error por `kind`, incluida la tasa de `invalid_output`;
- tokens y costo por llamada;
- tasa de `cost_known = false`.

Comparar dos perfiles es comparar dos releases (unidad 6).

## 9. Puntos de iteración

- **Proveedor:** cambiar `base_url` en `LLM_ENDPOINTS`. OpenRouter, vLLM, Ollama, Bifrost o el proxy de LiteLLM funcionan sin código.
- **Adaptador nativo** (p. ej. SDK de Anthropic) si el proveedor elegido no soporta bien la API compatible: otra implementación del mismo puerto, elegida por el alias.
- **Respaldo entre proveedores:** un `ModelProfile` con `fallbacks: [profile@v]`. Diseño de producción; hoy M8 cae a la plantilla.
- **Proxy de producción** con presupuestos y aislamiento de keys: se adopta cambiando el alias al proxy (ADR 0016).
- **Streaming y caché de prompts:** fuera del MVP.

## 10. Definición de terminado

- `OpenAICompatGateway` exportado; T-U5-01…10 en verde; `mypy`, `ruff` y `lint-imports` en verde (el adaptador solo importa `domain`, `ports` y el SDK `openai`).
- `openai` fijado a versión exacta con hash en `uv.lock`.
- `LLM_ENDPOINTS` documentado en `.env.example`, sin keys.
- Prueba de humo corrida contra al menos un endpoint, con el resultado anotado en el ADR 0016.
- Cambios de M0 rev. 5, M4 (`add_usage`), M8 (errores) y M1 (G0-15) aplicados en sus specs.

## 11. Abiertos

- **Proveedor de la demo:** sin decidir. No bloquea la construcción; se necesita antes del 02/10 para la prueba de humo. Candidatos: OpenRouter (una key, muchos modelos) o el proveedor que dé créditos el hackathon.
- **Soporte de `json_schema` estricto** en la API compatible del proveedor elegido: no verificado. Mitigación: modo `prompted`.
- **`llm_structured` en M5:** cómo su `ProviderSpec` referencia un `Prompt`. Se resuelve al construir M5 (fase 4); el gateway no cambia.
