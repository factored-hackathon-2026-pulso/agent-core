# Temas abiertos — Motor de decisión (spec 2026-09-28)

- Estado: **9 de 9 temas originales resueltos; 3 temas nuevos abiertos (#10, alta; #11 y #12, media).** Reemplaza la versión anterior de este documento, cuyo contenido se descartó por basarse en hallazgos incorrectos.
- Fecha: 2026-09-28
- Spec: `2026-09-28-motor-de-decision-design.md` (rev. 15)
- Regla de trabajo: antes de resolver cada tema se lee el ADR que lo gobierna.

## Descartado de la iteración anterior

Estas propuestas **no se aplican**:
- **`token_map` efímero en memoria.** Contradice ADR 0008 (`token_map` cifrado en el estado del run). Además rompe la resolución de tokens entre turnos, la recuperación `executing → verify` (ADR 0007) y el render del transcript y del handoff después del cierre.
- **Tool `detect_language` de unidad 3 invocada en cada turno, con umbral 0.7 fijo.** El comportamiento sticky ya estaba en §4.3, y el replay ya consume la salida registrada de las guardas. El umbral fijo viola la regla de umbrales calibrados.
- **`respond` reclama éxito implícitamente.** Choca con la regla 6.1.6, que permite `respond` seguro en ramas de fallo.

Tampoco eran temas abiertos: la cadena de hash y `reportable_attrs` (ADR 0003), los datos PT (fuera de alcance, §7, ADR 0012), el destino del outbox (unidad 4, §15) y la numeración §4.x.

## Estado

| # | Tema | Severidad | Estado |
|---|---|---|---|
| 1 | `claims_success` referenciado pero no declarado | Alta | **Resuelto (rev. 6)** |
| 2 | `reauth` corre después de Understand | Alta | **Resuelto (rev. 7)** |
| 3 | Qué recalcula el replay y qué lee de eventos | Alta | **Resuelto (rev. 8)** |
| 4 | `sha256` sin clave en la vista `audit` | Media | **Resuelto (rev. 11)** |
| 5 | Detector de idioma sin especificar | Media | **Resuelto (rev. 9)** |
| 6 | `unclear` de `confirm` sin tope | Media | **Resuelto (rev. 10)** |
| 7 | `end(abandoned)` declarable; outcome vs modo sin validar | Baja | **Resuelto (rev. 12)** |
| 8 | Dos ADR con número 0009 | Baja | **Resuelto (rev. 12)** |
| 9 | `untrusted_text` falta en ADR 0008 | Baja | **Resuelto (rev. 12)** |
| 10 | ADR de conocimiento (0015) aceptado pero no integrado en la spec | **Alta** | **Abierto** |
| 11 | Capa de analítica (cálculo y visualización de métricas) sin spec | Media | **Abierto** |
| 12 | Política del contexto conversacional (`recent_turns`) sin definir | Media | **Abierto** |

## Resueltos
- **#1 (rev. 6):** `respond.claims` declarado + derivado; invariante por camino hasta `verified`; `respond` seguro = sin reclamos. Spec §2, §5, §6.1, §8.2, §11, §13.3, §14, §15; ADR 0007.
- **#2 (rev. 7):** credenciales rechazadas con `401`/`403` antes del turno; `principal_mismatch`; límites por principal después de la firma; `reauth` fuera de los manejadores. Spec §3, §4.1, §4.5, §10, §11, §13.5; ADR 0006 y 0010.
- **#3 (rev. 8):** replay en modos `fixture` (CI, recálculo completo) y `audit` (integridad + transiciones); almacén `full` como diseño de producción. Spec §0, §11, §13.2, §15; ADR 0003.
- **#4 (rev. 11):** huellas HMAC-SHA256 con clave rotada por `kid` y JCS, en vista `audit` y transcript; cadena de eventos sigue en `sha256`; clave por subject como diseño de producción. Spec §0, §8.1.1, §8.4, §11, §13.6, §14, §15; ADR 0008 y 0003.
- **#5 (rev. 9):** lingua-py local fijado en la release, reglas `short`/`undetermined`/histéresis/no soportado, umbrales por calibración (1.0 si faltan), comparación con JEV en la prueba de humo. Spec §2, §4.3, §8.3, §11, §13.8, §14, §15; ADR 0012 y 0005.
- **#6 (rev. 10):** `max_attempts` en `confirm`; reentrada idempotente; con `confirm` pendiente, `out_of_scope`/`clarify`/bajo umbral dan `unclear`. Spec §4.4, §4.5, §5, §8, §8.2, §13.11; ADR 0007.
- **#7 (rev. 12):** outcomes declarables por modo; `abandoned`/`escalated` solo los asigna el motor; regla 6.1.14; chequeo de modo en el gate; `end(failed)` como salida de fallo en modo task. Spec §2, §5, §6.1, §6.2, §13.1.
- **#8 (rev. 12):** el ADR de conocimiento pasa a `0015-consumo-de-conocimiento-nodo-knowledge.md`; 0009 queda para políticas protegidas.
- **#9 (rev. 12):** ADR 0008 enmendado con la clase `untrusted_text`, su catálogo por defecto y su envoltura en la vista `model`.

## 10. Integración del ADR 0015 (conocimiento) — abierto
Encontrado al renumerar (#8). ADR 0015 está aceptado y declara cambios que la spec no tiene:
- **Catálogo (§5):** no existe el nodo `knowledge` (`read` / `navigate`), aunque el ADR lo agrega como cambio mayor del esquema.
- **Corte MVP (§0):** el ADR dice que en el MVP se construyen `read`, los filtros y el validador; §0 no menciona conocimiento.
- **`respond.generate`:** la spec sigue con `knowledge_refs[]`; el ADR lo reemplaza por `knowledge_from[]` + `purpose`.
- **Validador (§8.3):** cita "páginas recuperadas en este run", pero ningún nodo de la spec las recupera; faltan las comprobaciones de `audience: public` + `status: approved` para respuestas al cliente.
- **Validación estática (§6.1):** el ADR habla de "reglas 10–14" y "comprobaciones 5 y 6", numeración que ya choca con la spec actual; hay que definirlas y numerarlas de nuevo.
- **Dependencias (§14):** faltan `knowledge_view(principal, purpose)` (unidad 3), `knowledge_snapshot` en la release (unidad 2) y el puerto `KnowledgeSource` (unidad 7).
- **Reclamos de éxito (#1):** hay que decidir si un hecho de conocimiento puede alimentar un reclamo (probablemente no).

Por qué es alta: quien implemente desde la spec no construiría el nodo `knowledge`, y el validador de respuesta no tendría cómo comprobar citas de páginas.

## 11. Capa de analítica — abierto
Encontrado al agregar las métricas de eficiencia (spec rev. 14). Los eventos ya llevan los datos (latencias, uso del LLM, `turn_completed`) y §12 y cada módulo listan las métricas, pero ningún documento define cómo se calculan ni dónde se ven. Se delega a la unidad 6, que no tiene spec en este repo. Falta decidir:
- **Cálculo:** vistas SQL sobre el log en Postgres, o una exportación por release (`agentcore export`) que procese el harness de eval.
- **Consumo:** dashboard para la demo (Phoenix, un notebook o una página) y el formato que lee la auto-mejora.
- **Comparación entre releases:** qué diferencia de latencia o costo bloquea una promoción, con qué tamaño de muestra.
- **Retención:** cuánto tiempo se guardan los eventos para calcular tendencias.

Por qué es media: no bloquea la fase 1 porque los eventos ya capturan los datos, pero sin esto las métricas no se pueden mostrar en la demo del 03–05/10.

## 12. Política del contexto conversacional — abierto
M5 (`m05-decision-model.md` §3.2) incluye `recent_turns` (unidad 7, vista `model`) en su única llamada por turno, y `TranscriptStore.recent_turns(run_id, n)` está en M0. Pero ningún documento define cómo se arma ese contexto. Falta decidir:
- **Tamaño:** valor de `n`, y si se mide en turnos o en tokens; si es fijo por release o configurable por agente.
- **Conversaciones largas:** truncar, resumir o ambos. Un resumen es texto generado por un modelo: hay que decidir si cuenta como `untrusted_text` (ADR 0008) y cómo lo trata el replay (§11).
- **Contenido del turno:** qué entra por turno (mensaje del cliente, respuesta del agente, resultados de acciones) y en qué clase de vista; los hechos siguen viniendo de acciones verificadas, nunca del historial.
- **Entre runs:** un asunto retomado es un run nuevo sobre el mismo subject (spec §1); si M5 recibe algo del run anterior queda para la unidad 7 y no tiene spec en este repo.
- **Fase 1:** con `InMemoryTranscript` basta un `n` fijo; el resto no bloquea, pero debe cerrarse antes de implementar M5 con un proveedor real.

Por qué es media: no bloquea la fase 1, pero afecta al costo por turno, a la calibración de umbrales de M5 y al determinismo del replay.
