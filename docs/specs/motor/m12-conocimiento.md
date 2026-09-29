# M12 — Conocimiento (nodo `knowledge`)

- Estado: **propuesta de integración del ADR 0015; cierra el tema #10 cuando se apruebe.** Fase por decidir.
- Paquete: `agent_core.knowledge`
- Origen: ADR 0015 (la spec general todavía no lo refleja), spec general §8.3 (citas de páginas), §14 (unidad 7)
- ADRs: 0015 (nodo `knowledge`, páginas OKF, `purpose`, snapshot, `KnowledgeSource`), 0008 (vistas), 0007 (reclamos)
- Usa: M0, M7 · Lo usan: M2 (handler del nodo), M8 (comprobaciones de páginas), M1 (reglas)

## 1. Propósito y límites

Permite que un flow recupere páginas de conocimiento (patrón LLM Wiki / OKF) de un snapshot fijado en la release, las filtre por audiencia y estado según el `purpose`, y que el validador compruebe las citas a esas páginas.

**No hace:** ingesta, mantenimiento, lint ni aprobación de páginas (unidad 7), búsqueda libre (solo para el nodo `agent` de producción), calcular la vista de conocimiento de un principal (unidad 3: `knowledge_view`).

## 2. Interfaz pública

```python
class PageMeta:   path; anchor: str | None; snapshot: str; type: str
                  audience: Literal["public", "internal", "agent_only"]
                  status: Literal["draft", "approved"]; approved_by: str | None
                  lang: str; translation_of: str | None; valid_from; valid_to; source_refs: list[str]
class PageView:   ref: str            # "ruta@snapshot#ancla"
                  meta: PageMeta; content_model: str      # vista model (M7)
Purpose = Literal["customer_answer", "advisor_view", "agent_guidance"]

class KnowledgeNode (M1):  type: "knowledge"; mode: Literal["read", "navigate"]
                           pages: list[str]            # read: "ruta#ancla" (resueltas al publicar)
                           scope: str | None           # navigate
                           selector: EntityRef | None  # navigate: DecisionModel sobre el enum de rutas del scope
                           purpose: Purpose; save_as: str
                           # resultados: ok, not_found, denied  (+ low_confidence en navigate)

class KnowledgeService:
    def read(self, node, state, ctx) -> tuple[RunState, str, list[EngineEvent]]
```

Cambios en otros módulos:

- **`RunState`** (M0): `pages: dict[save_as, list[PageView]]`, escrito solo por M12.
- **`respond.generate`** (M1/M8): `knowledge_refs[]` se reemplaza por `knowledge_from: [save_as]` + `purpose`.
- **Puertos:** `KnowledgeSource` (M0); `AuthzPort.knowledge_view(principal, purpose) -> View` (unidad 3); `Release.knowledge_snapshot` (unidad 2).

## 3. Comportamiento

### 3.1 `read` (MVP según ADR 0015)

1. `view = authz.knowledge_view(principal, purpose)`.
2. Para cada página: `source.read(path, release.knowledge_snapshot, view)`.
3. **Doble filtro:** el servicio filtra con `view` y M12 vuelve a filtrar:
   - `customer_answer`: solo `audience: public` + `status: approved` + vigente + `lang` del turno (o su traducción);
   - `advisor_view`: `public` o `internal`;
   - `agent_guidance`: cualquiera, pero nunca citable al cliente.
4. Proyección a vista `model` con M7 (el contenido de páginas se trata como `untrusted_text` si su `source_refs` es externo).
5. `pages[save_as] = [...]`. Resultado `ok`; página ausente → `not_found`; filtrada → `denied`.
6. Hecho de procedencia: `FactSource{kind: knowledge, ref: "ruta@snapshot#ancla"}`.

### 3.2 `navigate` (si alcanza el tiempo)

Divulgación progresiva desde `index.md` del scope: el `selector` (un `DecisionModel` de M5) elige sobre el enum cerrado de rutas del scope; bajo umbral → `low_confidence`. Cada snapshot que cambie un scope obliga a recalibrar el selector.

### 3.3 Reglas estáticas nuevas (propuesta de numeración)

Continúan la numeración de M1 para no chocar con las existentes:

| ID | Regla |
|---|---|
| G0-15 | Toda página de `knowledge.read` existe en el snapshot de la release (se verifica en el gate de release) |
| G0-16 | Un `respond` con `purpose: customer_answer` solo lee `knowledge_from` de nodos con `purpose: customer_answer` |
| G0-17 | Las páginas fijas de un nodo `customer_answer` son `public` + `approved` en el snapshot |
| G0-18 | `navigate`: el enum de salida del `selector` es exactamente el conjunto de rutas del scope |
| G0-19 | Todo `knowledge_from` de un `respond` apunta a un nodo `knowledge` que lo domina |

### 3.4 Comprobaciones nuevas del validador (M8)

| ID | Comprobación |
|---|---|
| 6 · `page_citations` | Cada `page_ref` citado está en `pages` de un `save_as` listado en `knowledge_from` del nodo |
| 7 · `page_audience` | En respuestas al cliente, cada página citada es `public` + `approved` + vigente al instante del `Clock` |

Las cifras de páginas citadas cuentan para la comprobación 3 igual que los hechos.

### 3.5 Reclamos de éxito (propuesta)

Un hecho de conocimiento **no** alimenta reclamos de éxito: `derive_claims` (M1) ignora `pages`. Una página describe procedimientos, no el resultado de una acción de este run.

## 4. Invariantes

- Ninguna página `internal`, `agent_only` o `draft` llega a una respuesta al cliente.
- El snapshot es el de la release; editar el wiki solo entra con una release nueva.
- La auto-mejora puede proponer subir el snapshot, nunca aprobar páginas.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| `KnowledgeSource` caído | rama `not_found` (propuesta: agregar `error` si se prefiere distinguir) |
| Página filtrada | `denied` |
| Página vencida al momento de responder | comprobación 7 falla → regenerar/plantilla |

## 6. Eventos que emite

Propuesta: `knowledge_read {node_id, purpose, refs, filtered_out: [ref, motivo]}` (versión menor de contratos).

## 7. Pruebas

| ID | Caso |
|---|---|
| T-M12-01 | `customer_answer` descarta páginas `internal` y `draft` aunque el servicio las devuelva |
| T-M12-02 | Respuesta que cita una página no `approved` se rechaza (comprobación 7) |
| T-M12-03 | Cita a una página de un `save_as` no listado se rechaza (comprobación 6) |
| T-M12-04 | Un flow con G0-15…G0-19 violadas no se publica (un fixture por regla) |
| T-M12-05 | `derive_claims` ignora páginas |
| T-M12-06 | `FileKnowledgeSource` sobre `agent-registry/knowledge/` pasa la suite de contrato |

## 8. Evaluación

Respuestas con citas a páginas no aprobadas que escapan (objetivo 0), tasa de `not_found`/`denied`, precisión del selector de `navigate` al umbral (si se construye).

## 9. Puntos de iteración

- `navigate` y `search` se agregan sin cambiar `read`.
- Servicio real de la unidad 7: reemplaza `FileKnowledgeSource` detrás del puerto.

## 10. Definición de terminado

- Decisiones de la sección 11 aprobadas y la spec general actualizada (cierra el tema #10).
- `read` + filtros + comprobaciones 6 y 7 + G0-15…G0-19 con T-M12-01…06 en verde.

## 11. Abiertos (decisiones para cerrar el tema #10)

1. ¿Entra `read` en el MVP de construcción (30/09–02/10)? ADR 0015 dice que sí; el calendario está apretado.
2. Numeración propuesta: G0-15…G0-19 y comprobaciones 6–7.
3. Hechos de conocimiento fuera de los reclamos (3.5).
4. Resultado ante caída del `KnowledgeSource`: `not_found` o un `error` propio.
