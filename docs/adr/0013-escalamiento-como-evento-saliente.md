# ADR 0013 — Escalamiento como evento saliente; el asesor lee la conversación renderizada

- Estado: aceptado (2026-09-28)
- Unidad: 1 · Motor de decisión (con las unidades 4 y 7)
- Origen: hallazgo U6 de la revisión externa, resuelto por decisión del usuario

## Contexto
- No estaba definido qué pasa después de `escalate`: si el cliente sigue escribiendo, si el asesor puede devolver el caso al bot, o qué ocurre sin asesores disponibles.
- La plataforma del asesor es parte de la solución pero **no** del núcleo.
- El asesor necesita tener a mano la conversación del usuario con el agente.
- El reto pide transferir *"verified facts and open questions without dumping raw transcripts"*.

## Decisión
- **Evento, no integración.** `escalate` construye el `HandoffPacket`, emite `handoff_created` a un **outbox de eventos salientes** (en la misma transacción del turno) y cierra el run para el bot (`status: escalated`).
  - El destino de entrega (webhook, cola o tabla) se define en la unidad 4 y se itera después.
- **Dueño de la conversación.** Después del escalamiento, la conversación pertenece a la plataforma del asesor.
  - Nuevos turnos sobre ese run reciben `410 run_closed`, y la app enruta al asesor.
  - La disponibilidad de asesores, las colas y la atención fuera de horario son responsabilidad de esa plataforma.
- **Conversación disponible para el asesor.** `GET /v1/runs/{run_id}/transcript` devuelve la conversación **renderizada con los permisos del lector**: un asesor con delegación vigente ve los campos que la política le permite.
  - El `HandoffPacket` sigue siendo estructurado y referencia el transcript, sin incrustarlo.
  - Así se cumple el reto: el handoff no es un volcado del transcript, y el asesor igual puede consultarlo.

## Alternativas
- **Que el núcleo consulte la disponibilidad de la cola y elija un mensaje:** acopla el núcleo a la operación del asesor.
- **Que el núcleo siga atendiendo al cliente después de escalar (acuses, anexar mensajes):** duplica responsabilidades con la plataforma del asesor.

## Consecuencias
- El núcleo no promete tiempos de atención; eso lo comunica la plataforma del asesor.
- La devolución del caso del asesor al bot queda como diseño de producción: sería un run nuevo o un endpoint `resume` sujeto a política.
