# ADR 0025 — El ejecutor de tools es un servicio externo

- Estado: propuesto (2026-10-04); T1 (el adaptador y su contrato) implementado en `feat/http-tool-executor`.
- Relacionado: ADR 0007 (escrituras: confirmar → actuar → verificar), ADR 0024 (mismo patrón para el LLM gateway), ADR 0006 (principales y delegación). Diseño del lado de los datos: `support-platform/docs/platform/adr/0004-tool-service.md`.

## Contexto
- `ToolExecutor` es un puerto en proceso cuyas únicas implementaciones son dobles de prueba (`testing/e2e_demo.py`, `testing/serve_demo.py`). Producción necesita acceso a datos que no debe vivir en el proceso del motor: PII en claro, credenciales de datos y un ritmo de cambio propio (el pipeline de datos publica sus propias corridas).
- El motor ya verifica la identidad en el borde (JWS → `Principal` y `OnBehalfOf`) y no conserva el token firmado; lo que llega a la tool es el `ToolCallContext` ya verificado.

## Decisión
1. **`HttpToolExecutor`** (`agent_core.adapters.tools`) implementa `ToolExecutor` llamando a `POST {url}/v1/tools/{id}/execute`. La `ToolDef` sigue en el registry (riesgo, nivel mínimo, idempotencia, `readback_by`, `source`); el servicio implementa la parte de datos por nombre.
2. **Configuración por entorno, URL y token juntos** (como el gateway): `AGENTCORE_TOOL_SERVICE_URL`, `AGENTCORE_TOOL_SERVICE_TOKEN`, y `AGENTCORE_TOOL_SERVICE_TIMEOUT_S` (10 s por defecto). Se activa con `serve --tools agent_core.adapters.tools:http_tool_executor`; fuera de demo es la forma de tener `tools` real. Falta una de las dos o URL inválida: el proceso no arranca.
3. **Contrato de la llamada.**

   ```
   POST /v1/tools/{id}/execute            Authorization: Bearer <token>
   { "tool": "leer_productos@1.0.0",
     "args": {...},                        # lo escribe el modelo; nunca lleva al sujeto
     "bound_params": {...},                # lo fija el motor (sujeto, caso)
     "context": { "run_id", "release", "call_id", "turn_id",
                  "principal": {type, id, roles, scopes, attrs, auth_level},
                  "subject": {kind, ref} | null,
                  "on_behalf_of": {subject, grant_ref, grantee, scopes} | null },
     "idempotency_key": "<action_id>" | null }
   → 200 { "status": "ok|denied|step_up_required|error|timeout|uncertain",
           "result": ..., "source": "<tabla>", "error": {"kind","message"} | null }
   ```

   **Cambio respecto al borrador de la plataforma:** el servicio recibe los *claims ya verificados* y no el JWS, porque el motor no lo conserva y reenviarlo obligaría a cambiar M9 y el estado del run. La confianza entre servicios es el bearer (red privada, TLS delante); el servicio compara `bound_params` con `context` (sujeto/delegación) y responde `denied` si no concuerdan.
4. **Mapeo de errores sin lanzar al motor.** Lectura: transporte caído, 5xx, cuerpo ilegible, 401/403 o estado fuera de contrato → `error` (timeout del cliente → `timeout`). **Escritura: cualquier fallo de transporte o respuesta no válida → `uncertain`, nunca `error`** (ADR 0007: el motor verifica por `idempotency_key` en vez de reintentar a ciegas). Un estado `uncertain` en una lectura es fuera de contrato.
5. **`step_up_required` antes de cualquier efecto.** Si el nivel del principal es menor que `min_auth_level`, el ejecutor responde `step_up_required` sin llamar al servicio; el servicio también puede pedirlo.
6. **Sin datos sensibles en logs ni errores.** Solo `call_id`, nombre de la tool, estado y causa corta; nunca args, resultados, claims ni texto de excepción. El mensaje de error del servicio se trunca a 200 caracteres y solo se conserva si el estado no es `ok`.
7. Los dobles de demo siguen tras `AGENTCORE_ALLOW_DEMO=1`.

## Consecuencias
- El motor no guarda credenciales de datos ni monta el dataset. Un servicio pequeño (T2) tiene el único acceso al dato con PII.
- El contrato debe vivir en un archivo compartido y verificarse contra deriva en ambos repos (T2).
- La verificación de coherencia sujeto/delegación pasa a ser responsabilidad del servicio, no del motor; por eso el bearer del servicio es secreto de despliegue y el servicio no debe exponerse fuera de la red privada.

## Abierto
- Si `GET /v1/tools` debe publicar los nombres y la versión del `args_schema` que implementa el servicio, para que `publish` de una release falle si falta una tool.
- Pruebas contra un servicio real: llegan con T2.
