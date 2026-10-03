# ADR 0016 — LLM gateway propio sobre la API compatible con OpenAI

- Estado: aceptado (2026-09-28); **reemplazado en parte por el ADR 0022** (2026-10-03): el comportamiento sigue vigente pero vive en el servicio `llm-gateway`; el adaptador Python con el SDK `openai` se eliminó
- Unidad: 5 · LLM gateway
- Spec: `docs/specs/2026-09-28-llm-gateway-design.md`

## Contexto
- El núcleo necesita un modelo generativo para `respond(generate)` (M8) y para el baseline `llm_structured` (M5). El proveedor de la demo todavía no está decidido.
- El puerto `LLMGateway` de M0 ya aísla al núcleo del proveedor. Lo que falta es su implementación real.
- El MVP pide poco: una operación (`generate` con esquema opcional), tokens, costo exacto en `Decimal` y un timeout corto. La recuperación ya existe en M8 (regenerar una vez y luego usar la plantilla).
- Hay que medir costo y latencia por release (spec rev. 14), así que el modelo y su precio tienen que quedar versionados.
- LiteLLM, la opción obvia, sufrió un compromiso de supply chain el 24/03/2026: las versiones 1.82.7 y 1.82.8 en PyPI robaban credenciales al instalarse. Además, el diccionario de datos del reto trae credenciales de AWS.

## Decisión
1. **Adaptador propio** `OpenAICompatGateway` detrás del puerto, con el SDK oficial `openai` apuntando a cualquier endpoint compatible. `base_url` y la variable de la key se eligen por alias en `LLM_ENDPOINTS`.
2. **`ModelProfile` versionado en el registro:** modelo, temperatura, `max_tokens`, timeout, modo de salida estructurada (`native`/`prompted`) y precio en `Decimal`, con fuente y fecha. Cada `Prompt` referencia su perfil. Cambiar de modelo o de precio es una release nueva, comparable en la eval y proponible por la auto-mejora.
3. **Sin reintentos en el gateway** (`max_retries = 0`; el SDK reintenta 2 veces por defecto). Los errores se traducen a `GatewayError(kind)`. Solo `invalid_output` se regenera, en M8.
4. **El gateway no guarda estado.** El costo por principal lo acumula M4 en la transacción del turno (`UnitOfWork.add_usage`). Así cubre también el costo de `DecisionModel`, que no pasa por el gateway. `CostCounters` pasa a ser de M4.
5. **El texto del prompt no lleva variables.** Las entradas van como JSON canónico en el mensaje `user`, así los datos no pueden reescribir las instrucciones y el prompt versionado es exactamente el que se envía.

## Alternativas
| | Por qué no |
|---|---|
| **LiteLLM SDK** | Máxima cobertura, pero es una dependencia grande dentro del proceso que tiene el vault y las claves de huellas, con un incidente de supply chain reciente, y el costo sale en `float`. Sigue disponible: su **proxy** se puede usar como un alias más, sin código. |
| **LiteLLM proxy u otro gateway autohospedado (Bifrost, Portkey)** | Suma un servicio y su almacenamiento en contra del ADR 0001, sin necesidad para el MVP. Queda como diseño de producción (presupuestos y aislamiento de keys), adoptable cambiando el alias. |
| **any-llm (Mozilla)** | Capa delgada sobre SDKs oficiales, pero el costo lo seguiríamos calculando nosotros y agrega una dependencia más para lo que cubre el SDK `openai`. |
| **Pydantic AI** | Es un framework de agentes que se solapa con el motor; solo usaríamos su capa de modelos. |
| **OpenRouter como decisión fija** | Queda cubierto como un alias. Fijarlo ataría la demo a un tercero sin necesidad. |
| **Modelo en variables de entorno** | El modelo no quedaría en la release ni en el replay, y no se podrían comparar modelos. |

## Consecuencias
- Cambios en M0 (rev. 5): `ModelProfile`, `Prompt.model_profile`, `GenerationResult` con `tokens_in`/`tokens_out`, `GatewayError`, `LlmUsage` con `cost_known` y tokens separados, y `UnitOfWork.add_usage`.
- M1 gana G0-15, M8 distingue los errores del gateway y M4 escribe el uso.
- Cualquier endpoint compatible con OpenAI se usa sin código. Un proveedor sin API compatible usable necesita un adaptador nativo (~100 LOC) detrás del mismo puerto.
- El precio se mantiene a mano en el registro y puede quedar desactualizado. Por eso el perfil guarda la fuente y la fecha.

## Riesgos y lo no verificado
- No verifiqué que cada proveedor soporte `json_schema` estricto por su API compatible con OpenAI. Mitigación: modo `prompted` con validación local.
- El comportamiento de `usage` y `finish_reason` en errores varía entre proveedores. El adaptador trata el uso como opcional en los errores (`cost_known`).
- Resultado de la prueba de humo: pendiente (se anota aquí al elegir proveedor).

## Enmienda 2026-09-30 (spec rev. 2)
- **Proveedor de la demo: OpenRouter**, como un alias más de `LLM_ENDPOINTS`. Los perfiles de la demo usan `structured = prompted`: OpenRouter enruta entre proveedores y uno puede ignorar `response_format` sin avisar. `native` sigue soportado; su uso con OpenRouter necesitaría `provider.require_parameters` (campo aparte en `ModelProfile`, fuera de esta rev.).
- **Validación local sin `jsonschema`:** el gateway reutiliza el validador de subconjunto cerrado de M2 (`check_output`), que pasa a `agent_core.domain`.
- **Nodo `agent` (ADR 0019):** su adaptador real de `AgentPort` (`LLMAgentPort`) se construye sobre `generate` en modo `prompted`, sin tool-calling nativo de la API. `ToolDef` gana `description` y `args_schema` para el catálogo que ve el modelo.
- **Versión del SDK:** `openai` queda fijado a la última 2.x, exactamente `openai==2.54.0`: la línea 3.x depende de `httpx2`, que el transporte de pruebas `respx` no puede interceptar.
- **Resultado de la prueba de humo:** sigue pendiente (requiere la key del usuario; no se corrió en la unidad 5).

## Fuentes
- https://docs.litellm.ai/blog/security-update-march-2026
- https://www.trendmicro.com/en_us/research/26/c/inside-litellm-supply-chain-compromise.html
- https://docs.litellm.ai/docs/completion/token_usage
- https://blog.mozilla.ai/introducing-any-llm-a-unified-api-to-access-any-llm-provider/
- https://pydantic.dev/docs/ai/api/pydantic-ai/usage/
- https://www.getmaxim.ai/articles/best-litellm-alternatives-in-2026/
