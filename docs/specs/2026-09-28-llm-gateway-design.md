# Spec — LLM gateway (unidad 5)

- Estado: rev. 2, implementada (2026-09-30; falta la prueba de humo manual contra OpenRouter) · se construye sobre `origin/main` (3bf8afc); M8 lo necesita para la demo antes del congelamiento (02/10)
- Fecha: 2026-09-28 (rev. 2: 2026-09-30)
- Repo: `agent-core` · rama `feat/llm-gateway` (worktree `.claude/worktrees/llm-gateway`)
- Paquete: `agent_core.adapters.llm`
- ADRs: 0016 (gateway propio compatible con OpenAI, enmendado en rev. 2), 0019 (agentes internos), 0001 (stack), 0003 (observabilidad), 0008 (vista `model`)
- Usa: M0 (`LLMGateway`, `RegistryPort`, `ModelProfile`, `Prompt`, `ToolDef`, `GatewayError`) · Lo usan: M8 (`respond(generate)`), M2 (nodo `agent`, por `LLMAgentPort`), M5 (`llm_structured`, solo baseline)
- Autor: Juan Zapata, con Claude

## Cambios de la rev. 2

- **Proveedor de la demo: OpenRouter.** Los perfiles de la demo usan `structured = prompted` (§3.1, §11).
- **Validación local del esquema** con `check_output`, que pasa de M2 a `agent_core.domain` (§3.7). Sin dependencias nuevas.
- **Timeout:** además del timeout por fase del SDK, un tope total por llamada (§3.1, T-U5-11).
- **Segundo entregable:** `LLMAgentPort`, el adaptador real de `AgentPort` del nodo `agent` (§3.8, ADR 0019). Cierra el abierto de m02 §11.
- **`ToolDef` gana `description` y `args_schema`** y M1 gana la regla G0-24 (§2, §3.8; G0-22 y G0-23 ya son de agentes internos).
- **Cambio de M8** (§3.3) incluido en el alcance de construcción, no solo anotado.

## 1. Propósito y límites

Implementa el puerto `LLMGateway` de M0: recibe un `prompt@v` exacto y entradas en vista `model`, llama a un modelo generativo por una API compatible con OpenAI y devuelve la salida validada con su uso (tokens y costo en `Decimal`). Sobre ese puerto, `LLMAgentPort` implementa el paso del bucle del nodo `agent` de M2.

El proveedor de la demo es OpenRouter. El gateway lo hace intercambiable: el endpoint se elige por configuración y el modelo por un `ModelProfile` versionado en el registro.

**No hace:**
- **Recuperación:** no reintenta ni cambia de proveedor. La cadena generar → regenerar → plantilla es de M8; la regeneración de una salida final inválida del nodo `agent` es de M2.
- **Estado:** no guarda contadores. El costo por principal lo acumula M4 al commitear el turno (§3.6).
- **Auditoría:** no emite eventos. El uso llega al log por `response_emitted.llm` (M8) y por los presupuestos del nodo `agent` (M2).
- **PII:** no tokeniza. Las entradas ya llegan en vista `model` (M7).
- **Tool-calling nativo de la API:** el nodo `agent` usa `generate` en modo `prompted` (§3.8).
- Tampoco valida contenido (M8), ni hace streaming, caché, varios proveedores con respaldo o gestión de prompts fuera del registro (§9).

## 2. Interfaz pública

Tipos de M0 (rev. 5, ya en `main`):

```python
class StructuredMode(StrEnum): native, prompted
class ModelPrice:     input_per_mtok: Decimal; output_per_mtok: Decimal     # USD por millón de tokens
                      source: str; as_of: date                             # de dónde salió el precio y cuándo
class ModelProfile:   id: str; version: str; endpoint_alias: str; model: str
                      temperature: Decimal; max_tokens: int; timeout_s: int = 8
                      structured: StructuredMode = native; price: ModelPrice
class Prompt:         id; version; locales: dict[Locale, str]; reads: frozenset[str]
                      model_profile: RefSpec

class GenerationResult: output: JsonValue; tokens_in: int; tokens_out: int; cost_usd: Decimal; model: str
class GatewayErrorKind(StrEnum): timeout, unavailable, rate_limited, invalid_output, refused
class GatewayError(DomainError):
    kind: GatewayErrorKind
    tokens_in: int | None; tokens_out: int | None; cost_usd: Decimal | None; model: str | None   # uso parcial, si el proveedor lo informó

class LLMGateway(Protocol):
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult     # falla → GatewayError
```

**Cambio de M0 en esta rev. (`ToolDef`):**

```python
class ToolDef:  ...                                       # campos actuales sin cambios
                description: str | None = None            # qué hace la tool, para el modelo del nodo `agent`
                args_schema: dict[str, JsonValue] | None = None   # subconjunto cerrado de JSON Schema (§3.7)
```

Son opcionales en el tipo (las releases existentes siguen válidas) y obligatorios por la regla G0-24 de M1 para toda tool que aparezca en `tools_allowed` de un nodo `agent`. Regenerar `contracts/` (`uv run agentcore contracts`).

Adaptador (`agent_core/adapters/llm/`):

```python
class EndpointConfig: alias: str; base_url: str; api_key_env: str        # nombre de la variable, nunca la key
def load_endpoints(env: Mapping[str, str]) -> dict[str, EndpointConfig]  # lee LLM_ENDPOINTS
class OpenAICompatGateway(LLMGateway):
    def __init__(self, registry: RegistryPort, endpoints: dict[str, EndpointConfig],
                 env: Mapping[str, str],
                 client_factory: Callable[[EndpointConfig, str, int], OpenAI] = default_client,
                 tracer: Tracer | None = None) -> None                    # `None`: el tracer global de OpenTelemetry
def price_of(price: ModelPrice, tokens_in: int, tokens_out: int) -> Decimal

class LLMAgentPort(AgentPort):                                           # §3.8
    def __init__(self, gateway: LLMGateway, registry: RegistryPort,
                 resolve_ref: Callable[[EntityKind, RefSpec], EntityRef]) -> None
```

`resolve_ref` traduce una referencia de autoría (`RefSpec`) a la exacta de la release del run, como el de `ResponderContext` (M8); lo arma `composition` por run. El `Locale` sale de `state.locale`.

`env` es el mapa de variables de entorno inyectado; el gateway nunca lee `os.environ` (así las pruebas no dependen del proceso). `client_factory` recibe el endpoint, la key ya leída y el `timeout_s`.

**Configuración:** `LLM_ENDPOINTS` es un JSON `{alias: {base_url, api_key_env}}`. Para la demo:

```json
{"openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"}}
```

Otros alias posibles sin código: `openai`, `local` (vLLM u Ollama), `litellm-proxy`. Las keys viven solo en las variables que nombra `api_key_env`.

## 3. Comportamiento

### 3.1 `generate`

1. `prompt = registry.get(prompt_ref, Prompt)` y `profile = registry.get(prompt.model_profile, ModelProfile)`. Las dos referencias son exactas porque vienen de una release (M0 §2.2).
2. `text = prompt.locales[locale]`. Si falta el locale es un error de programación, porque M1 lo impide (G0-12): lanza `SchemaError`.
3. Arma los mensajes:
   - `system`: `text`, **sin sustitución de variables**. Con `structured = prompted` y `schema`, se le agrega al final la instrucción de responder solo con JSON que cumpla `schema` (el esquema en JSON canónico).
   - `user`: `canonical_bytes(inputs_model_view)` decodificado como texto. Los campos `untrusted_text` ya vienen envueltos por M7.
4. Llama `client.chat.completions.create(model=profile.model, messages, temperature, max_tokens, response_format?)`, donde el cliente se construye con `max_retries = 0` y `timeout = httpx.Timeout(profile.timeout_s)` (aplica a conexión, escritura, lectura y pool). Con `structured = native` y `schema`, `response_format = {type: json_schema, json_schema: {name: "output", schema, strict: true}}`. Con `prompted` no se envía `response_format`.
5. **Tope total:** la llamada corre con un plazo de pared de `profile.timeout_s` segundos, medido por el adaptador con un `Timeout` propio del cliente HTTP (transporte con `read` acotado por el plazo restante). Si el plazo se agota, `timeout`. El SDK solo acota cada fase, así que un endpoint que gotea bytes podría exceder el plazo sin este tope. La implementación elige el mecanismo (hilo con plazo o deadline en el transporte); el criterio de aceptación es T-U5-11.
6. Lee `usage.prompt_tokens` y `usage.completion_tokens`, y calcula `cost_usd = price_of(profile.price, tokens_in, tokens_out)`.
7. Interpreta la salida:
   - con `schema`: parsea el contenido con `agent_core.domain.loads` (los números con decimales quedan como `Decimal`) y lo valida con `check_output(schema, valor)` (§3.7). Si el contenido entero es un único bloque de código (` ```json … ``` ` o ` ``` … ``` `), se extrae su interior antes de parsear: los modelos en modo `prompted` lo hacen con frecuencia y no es una salida inválida. Cualquier otro texto alrededor del JSON sí lo es;
   - sin `schema`: `output` es el texto.
8. Devuelve `GenerationResult`, con `model` igual al modelo que informó la respuesta (puede diferir del pedido si el endpoint enruta, como hace OpenRouter).

Parámetros del modelo: `max_tokens` y `temperature` se envían tal cual. Un modelo que los rechace (400) produce `unavailable` y se corrige con otro perfil. Los modelos de razonamiento que exigen `max_completion_tokens` quedan fuera del MVP (§9).

### 3.2 Traducción de errores

| Causa | `GatewayError.kind` |
|---|---|
| Timeout del cliente o tope total (§3.1 paso 5) | `timeout` |
| Error de conexión, 4xx distinto de 429, 5xx, alias no configurado o variable de key vacía | `unavailable` |
| 429 del proveedor | `rate_limited` |
| La salida no parsea como JSON o no cumple `schema`; `finish_reason = length` con `schema` | `invalid_output` |
| Rechazo o filtro de contenido (`finish_reason = content_filter` o campo `refusal`) | `refused` |

- Con `invalid_output` y `refused` el proveedor sí informó el uso: va en el error con su costo.
- En los demás casos, el uso va si el proveedor lo informó, y si no, en `None`.
- Ninguna otra excepción sale del adaptador: todo lo no previsto es `unavailable`, y el detalle va al log técnico sin contenido.
- El mensaje de `GatewayError` solo lleva `kind` y `model` (M0 §2.11). El motivo de `invalid_output` (ruta y regla de `check_output`, nunca el valor) va al log técnico.

### 3.3 Qué hace M8 con cada error (cambia M8 §3.2 y §5; **entra en el alcance de construcción**)

- `invalid_output` cuenta como falla de la comprobación de formato: **1 regeneración** y luego la plantilla.
- `timeout`, `unavailable`, `rate_limited` y `refused` van **directo a la plantilla**, sin regenerar.
- Toda llamada, exitosa o no, suma a `LlmUsage`: `calls + 1`, latencia medida por M8 y tokens y costo si se conocen. Si alguna llamada no informó uso, `cost_known = false`.
- Hoy M8 trata cualquier falla del gateway como plantilla directa y no distingue `kind`; el cambio es acotado a `response/` y a m08, con pruebas propias.

### 3.4 Costo

- `price_of = (tokens_in × input_per_mtok + tokens_out × output_per_mtok) / 1 000 000`, en `Decimal` y redondeado a 6 decimales con `ROUND_HALF_EVEN`.
- El gateway nunca usa el costo que calcule un SDK o un proveedor en `float` (OpenRouter informa su propio `usage.cost`: se ignora).
- El precio es parte del `ModelProfile`: cambiarlo es una versión nueva del perfil y queda en la release.

### 3.5 Observabilidad

- Un span `chat` por llamada, con:
  - las semconv GenAI de la versión fijada: `gen_ai.operation.name = chat`, `gen_ai.provider.name` (el alias), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` y `gen_ai.usage.output_tokens`;
  - los atributos propios `agentcore.prompt`, `agentcore.model_profile` y `agentcore.gateway.error_kind`.
- Captura de contenido desactivada (ADR 0003). Nunca van a spans ni logs la key, los headers ni el contenido de mensajes o respuestas.
- El SDK `openai` registra el contenido de los requests en DEBUG: los loggers `openai` y `httpx` no se activan en DEBUG en ningún entorno con datos reales.
- La latencia que llega al log de auditoría la mide M8 con el `Clock` (M0 §2.10). El span tiene la suya propia.

### 3.6 Costo por principal (ya aplicado en M4, M9 y M0)

- M4, en el paso 14 (persistir), suma el costo del turno y llama `uow.add_usage(principal.key, cost, now)` dentro de la misma transacción.
  - El costo es Σ `decision_made.cost_usd` + Σ `response_emitted.llm.cost_usd` del turno. Las llamadas sin uso conocido suman 0.
- M9 lee con `CostCounters.spent_today` y `hits`.
- El costo de las llamadas del nodo `agent` ya entra en esa suma: `handle_agent` lo carga con `charge_model` a `budgets_used.run_cost`, y M4 suma el delta del turno. T-U5-16 lo verifica.

### 3.7 Validación local del esquema (`check_output` pasa a `domain`)

- `check_output(schema, value, path="") -> str | None` es hoy un validador puro de M2 (`interpreter/schema.py`, 74 líneas). Se mueve a `agent_core/domain/schema.py` y se exporta desde `agent_core.domain`; `interpreter` lo importa desde ahí. El comportamiento no cambia.
- Subconjunto cerrado: `type`, `enum`, `properties`, `required`, `additionalProperties` (booleano) e `items`. Las anotaciones (`description`, `title`, …) se ignoran. Una palabra clave fuera de la lista falla cerrado.
- El mensaje describe la ruta y la regla, nunca el valor.
- Consecuencia para los esquemas de M5 (`{slots: objeto libre}`) y M8 (`{text, citations}`): caben en el subconjunto. `oneOf`, `anyOf` y `$ref` no; por eso el esquema del paso del nodo `agent` es plano (§3.8).
- No se agrega `jsonschema` (ADR 0016: menos dependencias en el proceso que tiene el vault).

### 3.8 `LLMAgentPort`: un paso del nodo `agent` (ADR 0019, m02 §3.7)

Implementa `AgentPort.step(request, state) -> AgentStepResult` sobre `LLMGateway.generate`, en modo `prompted`. El bucle, los presupuestos, la ejecución de tools, la validación contra `output_schema` y la regeneración con `feedback` son de M2; este adaptador solo hace **un paso**.

1. **Tools del catálogo.** Para cada `ref` de `request.config.tools_allowed`, `registry.get(resolve_ref(EntityKind.tool, ref), ToolDef)`. Cada entrada del catálogo es `{tool: "id@version", description, args_schema}`. Si una tool no tiene `description` o `args_schema`, es un error de programación (G0-24 lo impide): lanza `SchemaError`.
2. **Entradas** (`inputs_model_view`):
   ```
   {goal, step, tools: [<catálogo>], observations: [{tool, args, status, result, error}],
    feedback, output_schema}
   ```
   Las `observations` ya vienen en vista `model` (m02 §3.7). `feedback` es el motivo sin datos de la última salida rechazada, o `null`.
3. **Prompt.** `resolve_ref(EntityKind.prompt, request.config.prompt_ref)` (un `Prompt` con su `model_profile`); el texto describe el bucle y el formato del paso. La llamada es `generate(prompt, inputs, state.locale, schema=STEP_SCHEMA)`.
4. **Esquema del paso** (plano, dentro del subconjunto de §3.7):
   ```json
   {"type": "object", "additionalProperties": false, "required": ["kind"],
    "properties": {
      "kind":   {"type": "string", "enum": ["tool_call", "final"]},
      "tool":   {"type": "string"},
      "args":   {"type": "object"},
      "output": {}}}
   ```
5. **Traducción:**
   - `kind = "tool_call"`, con `tool` y `args` → `AgentToolCall(EntityRef.parse(tool), args)`. Si falta `tool`, o `args` no es objeto, o `tool` no parsea como referencia, es un paso inválido (ver abajo). Una tool fuera de `tools_allowed` **no** se rechaza aquí: la rechaza el handler con `tool_denied` (m02 §3.7).
   - `kind = "final"` → `AgentFinal(output)`. Si falta `output`, es un paso inválido. La validación de `output` contra `output_schema` y su única regeneración son de M2.
   - `AgentStepResult(action, model_calls=1, tokens=tokens_in + tokens_out, cost_usd=cost_usd)`.
6. **Paso inválido** (forma que el esquema plano no puede excluir, p. ej. `tool_call` sin `tool`): lanza `GatewayError(invalid_output)` con el uso del `GenerationResult` (tokens y costo). No se reintenta aquí; M2 decide (`gave_up`).
7. **Errores:** un `GatewayError` del gateway sube tal cual. **Cambio de M2:** hoy `handle_agent` no captura excepciones de `AgentPort.step`, así que una falla del modelo escaparía del turno. Debe capturar `GatewayError`, cargar al presupuesto el uso que informe (`charge_model` con `calls=1` y los tokens y costo del error, o 0 si no hay) y terminar el nodo con `gave_up`. Cualquier otra excepción sigue subiendo (error de programación o de cableado).
8. **Sin estado:** no guarda historial. Cada paso reconstruye su entrada desde `request`, `state` y el registro.
9. **Cableado:** `EngineRuntimeFactory.open` (composition) inyecta `StepContext.agents = LLMAgentPort(gateway, registry, resolve_ref)`, con el `resolve_ref` de la release del run (el mismo que usa para `ResponderContext`). Sin ese cableado todo nodo `agent` es un error de cableado.

**Dónde vive y qué importa.** `agent_core/adapters/llm/agent_port.py` importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de `agent_core.interpreter` (`AgentPort`, `AgentRequest`, `AgentStepResult`, `AgentToolCall`, `AgentFinal`). Requiere ampliar `.importlinter`: hoy `adapters` no puede importar `interpreter`; la excepción se limita a este módulo.

## 4. Invariantes

- Solo entran al gateway datos en vista `model`: la firma lo nombra y T-M7-01 lo verifica capturando requests.
- `max_retries = 0` en todo cliente: una llamada a `generate` hace a lo sumo una request HTTP.
- `cost_usd` es `Decimal` calculado con `ModelPrice`; nunca `float`.
- El texto del prompt que se envía es byte a byte el `Prompt.locales[locale]` de la release, más la instrucción de esquema en modo `prompted`.
- El gateway no lee la hora, no genera IDs y no guarda estado entre llamadas. `LLMAgentPort` tampoco.
- Ninguna key sale de su variable de entorno: ni al registro, ni a eventos, spans, logs o errores.
- Una llamada a `step` hace exactamente una llamada a `generate`.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Proveedor caído o lento | `GatewayError(timeout\|unavailable)` → M8 usa la plantilla; el turno sigue. En el nodo `agent`, `gave_up` |
| Alias del perfil sin configurar | `unavailable` y log de error con el alias (sin secretos). Al arrancar, la demo avisa por cada perfil de la release activa cuyo alias falte |
| El modelo o proveedor rechaza `max_tokens`/`temperature` | 400 → `unavailable`; se corrige con otro perfil (versión nueva) |
| OpenRouter enruta a un modelo sin soporte de un parámetro | con `prompted` no se envía `response_format`, así que el riesgo se reduce a `invalid_output` por salida mal formada |
| Respuesta exitosa sin `usage` | tokens y costo en 0 y un aviso en el log técnico (sin contenido); el turno sigue. La tasa de avisos se vigila en la prueba de humo |
| Salida truncada por `max_tokens` | `invalid_output` si hay esquema; texto truncado si no (M8 lo valida) |
| Paso del nodo `agent` con forma inválida | `GatewayError(invalid_output)` con uso; M2 → `gave_up` |
| Precio desactualizado | el costo reportado difiere de la factura; se corrige publicando otra versión del perfil |

## 6. Eventos que emite

Ninguno. El uso va en `response_emitted.llm` (M8), en `decision_made` (M5) y en los presupuestos del nodo `agent` (M2).

## 7. Pruebas

Sin red: el adaptador se prueba con un transporte HTTP falso (`respx` sobre `httpx`).

| ID | Caso |
|---|---|
| T-U5-01 | Suite de contrato de `LLMGateway`, parametrizada por `ScriptedGateway` y `OpenAICompatGateway` (con transporte falso): salida válida, tokens y costo `Decimal`, cada `GatewayErrorKind` |
| T-U5-02 | El request lleva `model`, `temperature` y `max_tokens` del perfil, `system` igual a `Prompt.locales[locale]` y `user` igual al JSON canónico de las entradas |
| T-U5-03 | `native` envía `response_format` `json_schema` estricto con `name = "output"`; `prompted` no lo envía y agrega la instrucción al `system` |
| T-U5-04 | Salida que no cumple el esquema → `invalid_output` con el uso informado |
| T-U5-05 | Timeout → `timeout`; 503 → `unavailable`; 400 → `unavailable`; 429 → `rate_limited`; `content_filter` → `refused`; excepción no prevista → `unavailable` |
| T-U5-06 | El cliente se construye con `max_retries = 0`: un 503 produce exactamente una request |
| T-U5-07 | `price_of` con precios y tokens conocidos da el `Decimal` esperado, redondeado a 6 decimales |
| T-U5-08 | Alias no configurado o variable de key vacía → `unavailable` sin request |
| T-U5-09 | Ni la key ni el contenido aparecen en spans, logs o el mensaje de `GatewayError` (captura de logs y exportador de spans en memoria) |
| T-U5-10 | Números con decimales en la salida estructurada llegan como `Decimal` |
| T-U5-11 | Un endpoint que responde gota a gota (cada fase bajo el timeout, total sobre `timeout_s`) → `timeout` dentro del plazo |
| T-U5-12 | `check_output` en `domain`: mismas pruebas que tenía en M2 (subconjunto, falla cerrada, mensaje sin el valor); M2 sigue en verde importándola desde `domain` |
| T-U5-13 | `LLMAgentPort`: un paso `tool_call` válido → `AgentToolCall` con la referencia parseada; un paso `final` → `AgentFinal`; `tokens = tokens_in + tokens_out` y `cost_usd` del resultado |
| T-U5-14 | `LLMAgentPort`: catálogo en las entradas con `description` y `args_schema` de cada tool de `tools_allowed`; sin ellas → `SchemaError` |
| T-U5-15 | `LLMAgentPort`: `tool_call` sin `tool`, `args` que no es objeto o `tool` sin referencia válida → `GatewayError(invalid_output)` con uso; una tool fuera de `tools_allowed` pasa tal cual (la rechaza M2) |
| T-U5-16 | Integración con M2: un nodo `agent` con `LLMAgentPort` sobre `OpenAICompatGateway` (transporte falso) llega a `answered`; `budgets_used.run_cost` acumula el costo del bucle |
| T-U5-17 | M2: un `GatewayError` de `AgentPort.step` termina el nodo en `gave_up` y carga al presupuesto el uso que informe; otra excepción sube |
| T-U5-18 | Composition: `EngineRuntimeFactory.open` inyecta `agents` y un flow con nodo `agent` corre con gateway guionado |
| T-M8-12 | M8: `invalid_output` regenera una vez y luego usa la plantilla; los otros `kind` van directo a la plantilla; `cost_known = false` si un error no informa costo (siguiente id libre en m08; ajustar si se ocupa antes) |
| T-M1-46 | M1 G0-24: una tool en `tools_allowed` sin `description` o `args_schema`, o con `args_schema` fuera del subconjunto, falla la validación estática (siguiente id libre en m01) |

**Prueba de humo (manual, fuera de CI):** `agentcore llm-smoke --profile <id@v>` corre 10 prompts sintéticos ES/PT contra el endpoint configurado y reporta la tasa de `invalid_output`, la latencia p50/p95 y el costo. Se corre contra OpenRouter antes de promover un perfil nuevo. Incluye 3 pasos del nodo `agent` para medir la tasa de pasos inválidos.

## 8. Evaluación

Por `model_profile@v` y por `prompt@v`, desde `response_emitted.llm` y los spans:

- latencia p50/p95;
- tasa de error por `kind`, incluida la tasa de `invalid_output`;
- tokens y costo por llamada;
- tasa de `cost_known = false`;
- para el nodo `agent`: pasos por respuesta, tasa de pasos inválidos y tasa de `gave_up`.

Comparar dos perfiles es comparar dos releases (unidad 6).

## 9. Puntos de iteración

- **Proveedor:** cambiar `base_url` en `LLM_ENDPOINTS`. OpenRouter, OpenAI, vLLM, Ollama, Bifrost o el proxy de LiteLLM funcionan sin código.
- **Salida estructurada nativa:** `structured = native` está implementado pero no se usa en la release inicial. Con OpenRouter haría falta `provider.require_parameters` (un campo `provider_options` en `ModelProfile`, cambio de M0 aparte) para evitar que enrute a un proveedor que ignore `response_format`.
- **Modelos de razonamiento:** exigen `max_completion_tokens` y no admiten `temperature`; requieren opciones por perfil (mismo `provider_options`).
- **Adaptador nativo** (p. ej. SDK de Anthropic) si el proveedor no soporta bien la API compatible: otra implementación del mismo puerto, elegida por el alias.
- **Tool-calling nativo** para el nodo `agent`: hoy el paso va por `generate` prompted; si la tasa de pasos inválidos es alta, un adaptador con `tools=` de la API sería otra implementación de `AgentPort`.
- **Respaldo entre proveedores:** un `ModelProfile` con `fallbacks: [profile@v]`. Diseño de producción; hoy M8 cae a la plantilla.
- **Proxy de producción** con presupuestos y aislamiento de keys: se adopta cambiando el alias al proxy (ADR 0016).
- **Streaming y caché de prompts:** fuera del MVP.

## 10. Definición de terminado

**Gateway**
- [x] `OpenAICompatGateway` exportado; T-U5-01…11 en verde; `mypy`, `ruff` y `lint-imports` en verde (el adaptador solo importa `domain`, `ports` y el SDK `openai`).
- [x] `openai` fijado a versión exacta (`openai==2.54.0`) con hash en `uv.lock`; `respx` en dependencias de desarrollo.
- [x] `LLM_ENDPOINTS` y `OPENROUTER_API_KEY` (sin valor) documentados en `.env.example`.
- [x] Suite de contrato de `LLMGateway` parametrizada por `ScriptedGateway` y `OpenAICompatGateway`.
- [x] `agentcore llm-smoke` implementado.
- [ ] `agentcore llm-smoke` corrido contra OpenRouter, con el resultado anotado en el ADR 0016 (manual, requiere la key del usuario; pendiente).
- [x] `check_output` movida a `domain` (T-U5-12).

**Nodo `agent`**
- [x] `LLMAgentPort` exportado; T-U5-13…18 en verde; excepción de `.importlinter` acotada a su módulo (`ignore_imports` de `agent_core.adapters.llm.agent_port -> agent_core.interpreter`).
- [x] `handle_agent` captura `GatewayError` (T-U5-17) y `composition` inyecta `agents` (T-U5-18).
- [x] `ToolDef.description` y `args_schema` en M0, con `contracts/` regenerado y la regla G0-24 en M1 con su prueba.
- [x] m02 §3.7 y §11 actualizados: el abierto del adaptador real queda cerrado y apunta a esta spec.

**Cambios en otras specs (aplicados en el mismo cambio)**
- [x] M8 (errores del gateway, §3.3) con su código y pruebas; M0 (`ToolDef`); M1 (G0-24); M2 (§3.7 y §11, import de `check_output`). M4 (`add_usage`) y M0 rev. 5 ya están aplicados.

## 11. Abiertos

- **Modelo concreto de la demo en OpenRouter:** sin elegir. No bloquea la construcción; se necesita para la prueba de humo. Debe soportar salida JSON obediente en modo `prompted` y ES/PT.
- **Prompt del bucle del nodo `agent`:** el texto concreto del `Prompt` (instrucciones del formato `kind/tool/args/output`) se escribe en el registro de la demo, no en el código. La spec fija el contrato, no la redacción.
- **Regla de M1 para `output_schema`:** m02 §11 anota que falta detectar al validar el flow un `output_schema` fuera del subconjunto. G0-24 cubre `args_schema`; extenderla a `output_schema` es trivial pero no está en el alcance de esta rev.
- **`GatewayError` sin evento en el nodo `agent`:** cuando `AgentPort.step` lanza un `GatewayError`, M2 carga el uso y termina en `gave_up` sin dejar ningún evento en el log de auditoría (`agent_step` solo se emite en pasos que devolvieron). Abierto: si `agent_step` debe registrar el fallo (`status` con el `kind`) para que la auditoría explique el `gave_up`.
- **`SCHEMA_VERSION` sin subir:** `ToolDef.description` y `args_schema` se agregaron como campos opcionales sin subir `SCHEMA_VERSION` (`0.4.0`). Abierto: decidir si un campo opcional nuevo exige subirla y regenerar `contracts/` con otra versión.
- **`llm_structured` en M5:** cómo su `ProviderSpec` referencia un `Prompt`. Se resuelve al construir M5; el gateway no cambia.
- **Mecanismo del tope total (§3.1 paso 5):** decidido en el plan: la llamada corre en un hilo y el adaptador espera con `Future.result(timeout=timeout_s)` (sin leer el reloj, regla de `ruff`). Los timeouts por fase del SDK son por chunk y no acotan un endpoint que gotea, así que al vencer el plazo el adaptador cierra el cliente (`client.close()`, errores ignorados) desde el hilo que espera: la lectura bloqueada falla y el hilo abandonado termina (los workers de `ThreadPoolExecutor` no son daemon y se unen al salir del intérprete). La llamada se envía con `contextvars.copy_context().run` para que el contexto de OpenTelemetry siga al hilo. Criterio de aceptación: T-U5-11.
