# Qué autoriza a un agente a llamar una tool

Una página para quien proponga enlaces de lectura (`read`) entre un agente y una tool. Se verificó leyendo el
código el 2026-10-05; cada punto cita su archivo.

Un agente **no tiene una lista de tools permitidas**. Llamar una tool exige que se cumplan cuatro cosas, en este
orden:

1. **El flujo la declara.** Solo un nodo `tool`, `write` o `confirm` de un flujo de la release puede llamar una
   tool, con `tool: <id>@<major>` y los `args` que el flujo escribe. El modelo nunca elige la tool ni inventa
   argumentos de sujeto. En un nodo `agent` o `suggest` (donde el modelo sí propone argumentos) la validación
   estática exige que cada tool tenga `description` y un `args_schema` del subconjunto admitido (G0-24,
   `agent_core/flows/rules/phase5.py:334`).
2. **La `ToolDef` del registry fija el riesgo y el nivel.** `risk_class`, `min_auth_level`, `idempotent` y
   `readback_by` salen de la entidad del registry, no del servicio. El intérprete compara el nivel del principal
   con `min_auth_level` antes de ejecutar (`agent_core/interpreter/handlers/tool.py:76`, `write.py:66`); si no
   alcanza, el run pide `step_up` en vez de ejecutar. Una escritura sin `idempotency_key` es un error de
   programación (`adapters/tools/http_executor.py`, ADR 0007).
3. **El sujeto lo pone el motor, no el modelo.** `authz.bind_params(principal, obo, subject)` produce los
   parámetros ligados (`subject_ref`, y `builder_id` para un `builder`) que viajan junto a `args`; los `args` del
   modelo nunca llevan el sujeto (`policy_authz.py`, `http_executor.py`). `authorize_subject` decide si el
   principal puede actuar sobre ese sujeto: un cliente solo sobre sí mismo, un asesor solo con una delegación
   firmada y vigente (`grant_active`), un `service` por `scope`, un `builder` nunca sobre clientes salvo el
   administrador de la plataforma.
4. **El tool-service vuelve a decidir.** Recibe un Bearer propio de agent-core (`AGENTCORE_TOOL_SERVICE_TOKEN`),
   los *claims* ya verificados (nunca un token) y los parámetros ligados, y ejecuta por nombre. Es la última
   barrera: valida sus propios rangos y tablas.

## Qué decide qué ve el modelo (no si puede llamar)

El **clasificador de campos** (`AGENTCORE_FIELD_CLASSIFICATION_FILES`, `views/`) y las **concesiones de campo**
(`AGENTCORE_AUTHZ_FIELD_GRANTS_FILE`, pares `[campo, purpose]`) gobiernan la *salida*: un campo sin clasificar es
`pii_direct` y llega al modelo tokenizado; `can_read_field(reader, obo, campo, purpose)` decide quién lo ve en
claro, y sin archivo de concesiones nadie lo ve. Un `builder` que no es administrador no lee ningún campo de
cliente. Esto no impide la llamada: impide que el dato salga de la vista.

## Consecuencia para proponer un enlace de lectura

Proponer "el agente X puede leer la tool T" es **proponer que un flujo de X incluya un nodo `tool` hacia T@v** y
revisar tres cosas: que `T.min_auth_level` sea alcanzable para los principales que invocan a X
(`agent.invocable_by`, `min_auth_level` del agente); que sus `bound_params` tengan sentido para esos principales
(un agente de asesor necesita delegación); y que los campos que devuelve estén clasificados (si no, llegan
tokenizados y el flujo de respuesta puede quedarse sin datos). No hay otra lista que editar.

## Origen de los datos de una tool (`source`)

`source` en la `ToolDef` es la tabla o conjunto de datos que la tool lee en el tool-service
(`customer_products`, `customer_cases`, `customer_transactions`). Lo define el tool-service y las fixtures lo
copian; `tests/registry/test_tool_contract_drift.py` falla si divergen.
