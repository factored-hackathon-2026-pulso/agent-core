# ADR 0010 — Niveles de autenticación y step-up

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por el tema #2 de la auto-revisión (credenciales inválidas o vencidas).
- Unidad: 1 · Motor de decisión (con la unidad 3)
- Origen: hallazgo C6 de la revisión externa

## Contexto
- El `Principal` no tenía nivel de autenticación, no existía un cliente anónimo y `reauth` solo bloqueaba.
- El reto pide *"authentication with a trusted test session or identity service"* y aclara que un número de cliente no prueba identidad.
- **(Auto-revisión #2)** La spec validaba credenciales al cargar el turno, pero trataba el fallo como un manejador global (`reauth`) que corría después de Understand. Así se enviaba a un modelo el texto de un principal con credenciales inválidas. Además, los límites "por principal" se aplicaban antes de validar la firma.

## Decisión
- **Nivel en el principal:** `Principal.auth = {level: anonymous|session|step_up, at}`, emitido por el servicio de identidad.
- **Requisitos declarados:**
  - Los agentes declaran `min_auth_level`.
  - Las tools declaran `min_auth_level` y, opcionalmente, `max_auth_age`.
  - Un `customer` anónimo no tiene `id` y solo accede a agentes y tools públicas.
- **Chequeo previo:** si el nivel no alcanza, el ejecutor devuelve `step_up_required` **antes** de llamar a la tool.
- **Espera:** el motor responde `awaiting: step_up` y se detiene en el mismo nodo. El siguiente turno, con un principal elevado, reintenta ese nodo. Si se agotan los intentos, `escalate(auth_insufficient)`.
- **Demo:** el OTP es **simulado** por un servicio de identidad de prueba y va etiquetado como tal.
- **Credenciales inválidas o vencidas (auto-revisión #2).** No son un manejador del flow: se rechazan **antes** del turno, sin cargar estado, sin Understand, sin modelos, sin tools y sin transcript.
  - Firma inválida → `401 credentials_invalid`. Solo log de seguridad; nada en la cadena del run.
  - Principal vencido → `401 principal_expired`. Se registra `access_denied`.
  - Delegación vencida o revocada → `403 delegation_expired`. Se registra `access_denied`.
  - La app renueva con el servicio de identidad y reintenta con el mismo `client_turn_id`.
  - La validez se evalúa una vez por turno, a su inicio, con el `Clock`.
  - Los límites de tasa y costo por principal se aplican después de validar la firma.
- **Diferencia con step-up:** el step-up eleva un principal válido y retoma un nodo; un principal vencido no se arregla dentro de la conversación.

## Alternativas
- **Solo niveles, sin step-up (denegar y pedir inicio de sesión):** es más simple. Se puede bajar a esto en la demo sin cambiar contratos.
- **Sin niveles:** se descarta.
- **(#2) Turno `200` con `awaiting: reauth` y una plantilla:** se descarta porque obliga a procesar un turno con credenciales inválidas y a cuidar que su mensaje no persista, sin poder resolver la renovación desde el motor.

## Consecuencias
- El flow no cablea `step_up_required`; lo maneja el motor.
- Queda pendiente la integración con un OTP o biometría reales en producción.
- **(#2)** La app debe manejar `401`/`403` de credenciales y reintentar el turno. `reauth` desaparece de los manejadores globales.

## Enmienda 2026-10-05 (revisión técnica)
- `max_auth_age` no se aplicaba en ningún lado: un step-up de hace horas seguía habilitando tools de nivel `step_up`. Ahora el chequeo previo del ejecutor es `ToolDef.accepts(auth, at)`: nivel **y** antigüedad de `auth.at` frente a `max_auth_age`, con `at` = instante del turno que el motor pasa en `ToolCallContext.at` (M0 rev. 14). Si la autenticación es más vieja, el resultado es `step_up_required` sin llamar a la tool, igual que un nivel insuficiente. Sin instante, una tool con `max_auth_age` se rechaza.
- El contrato `tool-provider` 1.0.0 no transporta `auth.at`; el servicio externo no repite este chequeo (lo hace el motor antes de llamarlo).
