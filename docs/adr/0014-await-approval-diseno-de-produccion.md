# ADR 0014 — Aprobación humana sin escalar (`await_approval`), diseño de producción

- Estado: aceptado como diseño de producción; **no se construye en la demo** (2026-09-28)
- Unidad: 1 · Motor de decisión
- Origen: hallazgo U7 de la revisión externa

## Contexto
- Algunas acciones de alto riesgo, como compensaciones o reversos, deberían requerir que un humano apruebe mientras el bot continúa la conversación, sin transferir el caso.
- El motor ya persiste el estado entre turnos.

## Decisión
- **Nodo `await_approval`** con los campos `approver: {principal_type, roles[]}`, `summary_template` y `timeout`, y los resultados `approved`, `rejected` y `timeout`.
- **Espera persistida.** Funciona como `collect`, pero la espera termina con un evento externo, `POST /v1/runs/{run_id}/events {type: approval, decision, approver}`. El evento lo firma un principal aprobador, y la aprobación queda en la auditoría.
- **Sin motor durable.** No hace falta Temporal para esto, porque el estado ya vive en Postgres entre turnos.
- **Relación con el invariante de escritura.** La aprobación no reemplaza el `confirm` del cliente: una escritura aprobada sigue el protocolo `confirm → act → verify` (ADR 0007).

## Consecuencias
- Agregar el nodo es un cambio mayor del catálogo, que se hará cuando se construya.
- Deja documentado ante el jurado cómo se harían escrituras de alto riesgo sin darle autonomía al bot.
