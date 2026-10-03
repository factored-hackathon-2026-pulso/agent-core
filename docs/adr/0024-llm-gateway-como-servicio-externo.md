# ADR 0024 — El LLM gateway es un servicio externo; agent-core es un consumidor

- Estado: aceptado (2026-10-03)
- Reemplaza en parte: ADR 0016 (gateway propio compatible con OpenAI). Lo que 0016 decidió sobre el **comportamiento** sigue vigente y vive ahora en el servicio; cambia **dónde vive y cómo se construye**.
- Diseño del servicio: repo `pulso-factored/llm-gateway` (`docs/adr/0001-llm-gateway-as-a-standalone-http-service.md` y `docs/specs/2026-10-03-llm-gateway-extraction-design.md`)

## Contexto
- Varios servicios, en distintos lenguajes, necesitan el mismo acceso a modelos con costo exacto y errores tipados. Un adaptador Python dentro de `agent-core` obliga a duplicarlo por lenguaje y deja las keys de los proveedores en el proceso que tiene el vault.
- ADR 0016 ya había aislado al núcleo detrás del puerto `LLMGateway`; por eso el cambio se limita a un adaptador.

## Decisión
1. **`HttpLLMGateway`** (`agent_core.adapters.llm.http_gateway`) implementa el puerto `LLMGateway` llamando a `POST /v1/generate` del servicio. Sigue resolviendo `Prompt` y `ModelProfile` en el registro (van en la release) y los envía en cada request; el servicio no guarda estado.
2. **Se borra** el adaptador `OpenAICompatGateway` y con él `config`, `cost`, `output`, el SDK `openai` y las pruebas que lo cubrían. Ese comportamiento queda cubierto en el servicio por los vectores de conformidad generados desde este código antes de borrarlo.
3. **Configuración:** `AGENTCORE_LLM_GATEWAY_URL` y `AGENTCORE_LLM_GATEWAY_TOKEN` (las dos o ninguna) reemplazan a `LLM_ENDPOINTS` y las keys de proveedor, que pasan al despliegue del servicio. Sin configurar, toda generación falla como `unavailable` (M8 usa la plantilla) y `serve` lo avisa al arrancar, junto con una sonda no bloqueante a `/healthz`. Una URL inválida o la mitad del par es un problema de configuración.
4. **Errores:** los `kind` del servicio se mapean uno a uno a `GatewayErrorKind`, con el uso parcial que informe. `bad_request`, `unauthorized` y cualquier `kind` desconocido significan despliegue mal configurado o desfasado: se tratan como `unavailable` y se registra solo el estado HTTP y el `kind`.
5. **Observabilidad:** el span `chat {modelo}` lo emite el servicio; `HttpLLMGateway` propaga `traceparent` y envía la correlación del turno (`run_id`, `turn_id`, `session_id`, `release`, `agent`) como `labels`. Las pruebas de atributos cerrados del span viven en el servicio.
6. **Lo que no cambia:** `LLMAgentPort`, `check_output` en `domain`, `agentcore llm-smoke` (ahora contra el servicio), el puerto y todo M8/M2/M5.

## JEV a través del mismo servicio (ampliación del 2026-10-03)
- El servicio expone `POST /v1/jev`, un transporte opaco hacia `POST /v1/systemone` de JEV: pone la key (`JEV_API_KEY` en el servicio), aplica un plazo total, reintenta solo 429/529 (`Retry-After`, backoff, nunca más allá del plazo), no sigue redirects y acota la respuesta.
- `GatewayJevTransport` (`agent_core.decision.providers.jev_gateway`) implementa `JevTransport` sobre ese endpoint y reemplaza a `HttpJevTransport`, que se eliminó junto con `AGENTCORE_JEV_API_KEY`. `JevProvider` (preguntas, respuestas, calibración) no cambia: la lógica de dominio de JEV sigue en este repo.
- Sin gateway configurado, llamar a JEV es un `DecisionConfigError` (como antes con la key vacía). Los 429/529 que el servicio reintentó vuelven en `retried` para la métrica de límite.

## Consecuencias
- Un salto de red más por generación; el timeout del cliente es `timeout_s` del perfil más 5 s de margen para que llegue la respuesta tipada `timeout` del servicio.
- Los avisos de arranque por alias de modelo sin endpoint (`LLM_ENDPOINTS`) desaparecen: los alias son del servicio. Se conserva el aviso de agentes sin release `prod`.
- El spec `2026-09-28-llm-gateway-design.md` queda como referencia histórica del comportamiento portado.
- Infra: la ejecución del servicio y los secretos de proveedor son de `pulso-factored/infra` (su ADR 0004); el contrato de ADR 0003 sobre `LLM_ENDPOINTS` pasa a ese servicio cuando este cambio se despliegue.

## Pendiente
- La prueba de humo contra OpenRouter sigue sin correrse (necesita la key del usuario); ahora se corre con `agentcore llm-smoke` apuntando al servicio.
