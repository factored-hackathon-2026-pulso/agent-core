# ADR 0015 — Consumo de conocimiento: nodo `knowledge`, páginas OKF y puerto `KnowledgeSource`

- Estado: aceptado (2026-09-28). **Renumerado desde 0009** el 2026-09-28 (tema #8 de la auto-revisión: el número 0009 ya correspondía a políticas protegidas).
- **Integración:** aprobada como diseño el 2026-09-30 en `docs/specs/motor/m12-conocimiento.md` (nodo `knowledge`, `knowledge_from[]`, `purpose`, reglas de validación y comprobaciones del validador); la construcción va a la fase 2. Ver tema #10 de `TEMAS-ABIERTOS-PENDIENTES.md`.
- Unidad: 1 · Motor de decisión (contrato compartido con las unidades 3 y 7)
- Origen: hallazgo U11 de la revisión externa (el catálogo de nodos no tenía mecanismo de recuperación de conocimiento)
- Spec: `docs/specs/2026-09-28-motor-de-decision-design.md` §8.4

## Contexto
- El conocimiento sigue el patrón LLM Wiki / Open Knowledge Format, no RAG vectorial. Lo mantiene un servicio aparte (unidad 7), que todavía no existe.
- El spec citaba "páginas recuperadas en este run" en el validador, pero ningún nodo las recuperaba, y `knowledge_refs` era ambiguo.
- El conocimiento tiene tres usos: respuestas al cliente, procedimientos del asesor y guías para el propio agente. Algunas páginas nunca deben llegar al cliente.
- En un LLM Wiki las páginas las mantiene un LLM. Afirmarle al cliente algo de una página no revisada es un riesgo bancario.
- OKF solo exige `type` y no define versionado, búsqueda ni control de acceso.

## Decisión
1. **Nodo dedicado `knowledge`** con dos modos:
   - `read`: páginas o anclas fijas, resueltas contra el snapshot al publicar.
   - `navigate`: divulgación progresiva desde `index.md`, con un `DecisionModel` selector sobre el enum cerrado de rutas del scope.

   La búsqueda (`knowledge_search`) existe solo como tool interna de solo lectura del nodo `agent`, y solo si el servicio la anuncia.
2. **Páginas compatibles con OKF** con campos obligatorios para el núcleo: `audience: public|internal|agent_only`, `status: draft|approved` (+ `approved_by`), `lang`/`translation_of`, vigencia y `source_refs`. La cita es por sección: `ruta@snapshot#ancla`.
3. **`purpose` decide la visibilidad.** `customer_answer`, `advisor_view` o `agent_guidance` se traducen en una `view` que calcula el PEP de la unidad 3. El servicio filtra con ella y el motor vuelve a filtrar. Solo páginas `public` + `approved` pueden citarse al cliente; el validador lo comprueba en runtime y G0 al publicar.
4. **Snapshot fijado en el release.** Editar el wiki solo entra con un release nuevo. La auto-mejora puede proponer subir el snapshot, pero no aprobar páginas.
5. **Puerto `KnowledgeSource`:** `capabilities`, `index`, `read` y `search` opcional. En la demo lo implementa `FileKnowledgeSource` sobre `agent-registry/knowledge/`.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **Conocimiento como tools `read` del sistema** (sin nodo nuevo) | Navegar exigiría encadenar `tool` + `decide` + `tool`, lo que es difícil de autorar para una persona no técnica, y reparte en varias tools los filtros de audiencia, estado y snapshot. |
| **Solo búsqueda en el servicio** | Deja la selección de páginas en el ranking de un sistema externo, con poco determinismo. En la práctica es volver a RAG. |
| **Bundles separados por audiencia** | Más simple, pero duplica las páginas compartidas y no resuelve el estado de revisión. |
| **Todas las páginas citables al cliente** | Traslada el riesgo de páginas generadas por LLM y no revisadas a respuestas bancarias. |

## Consecuencias
- El esquema de flows gana un tipo de nodo (cambio mayor). Hoy su costo es bajo porque el intérprete no está implementado.
- `respond.generate` cambia `knowledge_refs[]` por `knowledge_from[]` y agrega `purpose`.
- Se agregan las reglas 10–14 de validación estática y las comprobaciones 5 y 6 del validador de respuesta. **Nota de renumeración:** esa numeración ya no coincide con la spec actual (hoy las reglas llegan a 6.1.14 y el validador usa la comprobación 5 para idioma); se reasigna al integrar (tema #10).
- La unidad 3 debe exponer `knowledge_view(principal, purpose)`. La unidad 2 agrega `knowledge_snapshot` al release.
- La unidad 7 hereda la ingesta, el mantenimiento, el lint, la aprobación humana de páginas y `search`.
- Cada snapshot que cambie un scope de `navigate` obliga a recalibrar su selector.
- **Corte MVP:** se construyen `read`, los filtros y el validador. `navigate` se construye si alcanza el tiempo.

## Fuentes
- https://www.marktechpost.com/2026/06/16/google-cloud-introduces-open-knowledge-format-okf-a-vendor-neutral-markdown-spec-for-giving-ai-agents-curated-context/
- https://aaif.io/blog/karpathys-llm-wiki-as-agent-memory
