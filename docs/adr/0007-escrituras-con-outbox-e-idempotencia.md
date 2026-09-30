# ADR 0007 — Escrituras: acción congelada, outbox de intención e idempotencia por action_id

- Estado: aceptado (2026-09-28)
- Unidad: 1 · Motor de decisión
- Origen: hallazgo C2 de la revisión externa. Enmendado el 2026-09-28 por los hallazgos C3 y C4, y por los temas #1 (reclamos de éxito) y #6 (bucle de `unclear` en `confirm`) de la auto-revisión. Enmendado el 2026-09-29: rotación del token en la reentrada (M3 rev. 2). Enmendado el 2026-09-30: el botón con token rotado o vencido lleva a `unclear` sin contar intento (M3 rev. 3).

## Contexto
- El turno commiteaba estado y eventos al final, pero la tool `write_*` ya se había ejecutado antes.
- Si el commit fallaba, el efecto externo ya había ocurrido y su auditoría se perdía.
- En el reintento, el nodo de escritura recalculaba sus `args`, con lo que podía cambiar el `action_hash` (usado como clave de idempotencia) y la escritura se duplicaba.
- **(Auto-revisión #1)** La validación exigía que ningún `respond` con `claims_success` fuera alcanzable antes de `verified`, pero `respond` no declaraba ese campo, y "`respond` seguro" (ramas de fallo) no estaba definido. Un booleano además no distingue entre varias escrituras del mismo flow.
- **(Auto-revisión #6)** `confirm → unclear → confirm` no tenía tope. Cada reentrada al `confirm` creaba otra acción `proposed` con otro token, dejando dos tokens vigentes para la misma intención. Y con un `confirm` pendiente, `out_of_scope` cerraba el run en vez de dar `unclear`.

## Decisión
1. **Acción congelada.** `confirm` congela `{tool, args}` y crea un `action_id` estable. El nodo `tool` de una escritura usa `action_from: <confirm>` y no admite `args` propios. El validador estático exige que ese `confirm` domine el nodo.
2. **Outbox de intención.** Antes de invocar la tool, una transacción propia commitea `state = executing` + el evento `action_dispatched`. Después de la invocación, otra transacción commitea el resultado.
3. **Idempotencia.** `idempotency_key = action_id`. Es la misma clave en cualquier reintento del turno.
4. **Recuperación.** Una acción en `executing` al cargar el run nunca se re-ejecuta: se va a su `verify`.
5. **Readback por clave (C3).**
   - Toda tool `write_*` acepta `idempotency_key` y la guarda con restricción única.
   - Un reintento con la misma clave devuelve el recurso existente.
   - Su definición declara `readback_by: idempotency_key`, y `verify` consulta por esa clave.
   - Una tool puede usar la clave como ID propio si su backend lo permite.
   - Patrón de referencia: el header `Idempotency-Key` de las APIs de pagos.
6. **Resultados de escritura (C4).** Una escritura solo tiene tres resultados (más `step_up_required`, ver abajo):
   - `ok`;
   - `denied`: la política bloqueó **antes** de llamar, así que no hubo efecto posible;
   - `uncertain`: cualquier fallo del backend (error, 5xx, reset, timeout).
   - `step_up_required` es un cuarto resultado: la acción vuelve a `confirmed` sin efecto (no se llamó a la tool) hasta que el step-up se complete.

   `ok` y `uncertain` van siempre a `verify`, y el error original queda en el evento `tool_called`.
7. **Reclamos de éxito (auto-revisión #1).**
   - `respond` acepta `claims: [<id de confirm>]`, con `[]` por defecto. Enumera las acciones cuyo éxito afirma.
   - El validador estático **deriva** además un reclamo sobre la acción X cuando el `respond` lee un hecho que proviene de X: el `save_as` de la escritura, el `save_as` de su `verify`, o un hecho `compute` con alguno de ellos en su procedencia. La lectura puede ser una variable de plantilla o una entrada de `allowed_facts`.
   - **Invariante:** para cada X reclamada, todo camino desde la entrada del flow hasta el `respond` pasa por la rama `verified` del `verify` de X.
   - Un **`respond` seguro** es uno con conjunto de reclamos vacío; es el único `respond` admitido en ramas de fallo.
   - `verify` gana `save_as` para exponer el readback como hecho.
8. **Confirmación acotada e idempotente (auto-revisión #6).**
   - `confirm` tiene `max_attempts` (2 por defecto) y un resultado `max_attempts`. Cada `unclear` suma al contador del nodo y al tope global de reparación. Al agotarse, la acción pasa a `cancelled`.
   - **Reentrada:** con una acción `proposed` y el token vigente, el `confirm` repite la misma acción (mismo `action_id` y `token_exp`; el token se rota porque solo se guarda su hash, M3 rev. 2) con `reprompt_template` o `summary_template`. Con el token vencido, cancela la acción y congela una nueva. Nunca hay dos acciones `proposed` del mismo `confirm`.
   - **Precedencia:** con un `confirm` pendiente, solo aplican las interrupciones, `cancel` y `handoff`. `out_of_scope`, `clarify` y los comandos bajo umbral dan `unclear`; una intención nueva va a `pending_intents` y también da `unclear`.
   - Una respuesta por botón (token en el request) no pasa por Understand y nunca da `unclear` de texto: no suma reparación. Con un token rotado o vencido (Enmienda 2026-09-30) el `confirm` sigue su rama `unclear`, que repropone con token nuevo, sin contar intento.

## Alternativas
- **(C3) Búsqueda heurística por cliente, tiempo y contenido:** se descarta porque puede verificar el recurso equivocado.
- **(C4) Clasificar errores en definitivos e indeterminados:** es más preciso en los códigos de motivo, pero exige un mapeo por backend que, si falla, afirma que no hubo efecto cuando sí lo hubo.
- **Worker asíncrono de acciones** (cola + ejecución diferida + notificación): es más robusto a escala y con escrituras lentas, pero agrega infraestructura y una experiencia asíncrona que no caben en la construcción de la demo. **Es la evolución prevista para producción.**
- **Un solo commit con idempotencia por `action_hash`:** se descarta por las pérdidas de auditoría y los posibles duplicados.
- **(#1) `claims_success: bool`:** se descarta porque no distingue entre varias escrituras y un olvido del autor pasa la validación.
- **(#1) `claims` solo declarado:** se descarta porque un olvido del autor pasa la validación aunque el `respond` lea el resultado de la escritura.
- **(#1) Todo `respond` reclama éxito:** se descarta porque prohíbe respuestas seguras en ramas de fallo y respuestas informativas previas al `confirm`.
- **(#1) Clasificar el texto de la respuesta:** se descarta para el validador estático porque no es determinista.
- **(#6) Solo contar `unclear` en el tope global:** permite hasta 8 repeticiones y no resuelve la acción duplicada.
- **(#6) Tratar `unclear` como `no` tras N intentos:** convierte confusión en una respuesta que el cliente no dio.

## Consecuencias
- Cada escritura cuesta dos transacciones adicionales y el cargador necesita lógica de recuperación (~100 LOC).
- La unidad 3 debe aceptar `idempotency_key` en toda tool `write_*` y exponer `readback_by`. El validador estático lo exige.
- Un fallo definitivo del backend cuesta un readback extra. Se acepta a cambio de no mantener un mapeo de errores por backend (alternativa descartada en C4) y de no afirmar nunca "no ocurrió" sin haberlo comprobado.
- **(#1)** El validador estático necesita análisis de caminos y de procedencia de hechos (~40 LOC). La unidad 2 debe exponer qué variables de hecho lee cada plantilla.
- **(#1) Riesgo residual:** un texto fijo que afirma éxito sin leer hechos ni declarar `claims`, o un texto generado que lo afirma sin citarlo, pasa la validación estática. Se revisa al publicar y lo mide la unidad 6.
- **(#6)** Todo `confirm` necesita cablear `max_attempts` (regla 6.1.3) hacia una salida segura (regla 6.1.6). El estado del run guarda `node_attempts` y `repair_turns_used`.

## Enmienda 2026-09-30 (ADR 0019)
- **Clase `write_draft`** (diseño, sin construir): tool cuyo efecto queda confinado a un borrador del registry que ninguna release publicada lee. Se ejecuta como `act → verify`, sin `confirm`, y conserva `idempotency_key`, `readback_by`, `verify` obligatorio y auditoría por escritura. El gate humano es la aprobación de la propuesta.
- Solo agentes con `invocable_by ⊆ {builder}` pueden usarla (AG-02). Todo lo demás (`write_reversible`, `write_irreversible`, `money_movement`) conserva `confirm → act → verify` sin cambios.
- G0-16 no se relaja: un flow `task` escribe con `write_draft` o no escribe.
