# M12 — Conocimiento (nodo `knowledge`)

- Estado: **rev. 2 · `read` implementado (2026-09-30, fase 2); `navigate` y `search` fuera de la construcción.** Diseño aprobado el 2026-09-30 (cierra el tema #10).
- Paquete: `agent_core.knowledge`
- Origen: ADR 0015, spec general §8.3 (citas de páginas), §14 (unidad 7)
- ADRs: 0015 (nodo `knowledge`, páginas OKF, `purpose`, snapshot, `KnowledgeSource`), 0008 (vistas), 0007 (reclamos)
- Usa: M0, M7 · Lo usan: M2 (handler del nodo), M8 (comprobaciones de páginas), M1 (reglas)

**Changelog**

- rev. 2 (2026-09-30), construcción de `read` (`SCHEMA_VERSION` 1.0.0, versión mayor: M0 §9). Decisiones de detalle que el diseño no fijaba, todas dentro de lo aprobado:
  - tipos en M0 (`domain/knowledge.py`), incluida la vista `KnowledgeView` que calcula `AuthzPort.knowledge_view` y el `PageRecord` que devuelve el servicio; `KnowledgeSource` gana `index` y su `read` pasa a recibir la vista;
  - el "hecho de procedencia" `FactSource{knowledge}` es una propiedad derivada de `PageView`: no se escribe en `state.facts`;
  - `denied` gana sobre `not_found` cuando un mismo nodo tiene páginas de ambos tipos, y un resultado distinto de `ok` no deja páginas bajo `save_as`;
  - las traducciones y las anclas tienen una regla concreta (§3.1);
  - G0-17, G0-19 y G0-20 necesitan el snapshot de la release: viven en `validate_flow_for_release`, no en `validate_flow` (M1 §3.4);
  - un nodo `navigate` está en el esquema (G0-20 activa), pero en runtime sale por `not_found` con motivo `navigate_unavailable`;
  - la comprobación 7 se aplica a toda respuesta de un run cuyo principal es `customer` (§3.4).

## 1. Propósito y límites

Permite que un flow recupere páginas de conocimiento (patrón LLM Wiki / OKF) de un snapshot fijado en la release, las filtre por audiencia y estado según el `purpose`, y que el validador compruebe las citas a esas páginas.

**No hace:** ingesta, mantenimiento, lint ni aprobación de páginas (unidad 7), búsqueda libre (solo para el nodo `agent` de producción), calcular la vista de conocimiento de un principal (unidad 3: `knowledge_view`), ni el modo `navigate`.

## 2. Interfaz pública

Tipos en M0 (`agent_core.domain`, §2.12 de M0):

```python
Purpose = Literal["customer_answer", "advisor_view", "agent_guidance"]
class PageMeta:   path; anchor: str | None; snapshot: str; type: str
                  audience: Literal["public", "internal", "agent_only"]
                  status: Literal["draft", "approved"]; approved_by: str | None
                  lang: str; translation_of: str | None; valid_from; valid_to; source_refs: list[str]
class PageView:   ref: str            # "ruta@snapshot#ancla"; coincide con meta
                  meta: PageMeta; content_model: str      # vista model (M7)
                  source -> FactSource{kind: knowledge, ref}   # derivada, no se guarda
class PageRecord: meta: PageMeta; content: str            # vista full: nunca en repr ni serialización
class KnowledgeView: audiences: frozenset[Audience]; approved_only: bool   # la calcula el PEP
def page_ref(path, snapshot, anchor=None) -> str; parse_page_ref(text) -> PageRef | None; parse_page_spec(text) -> PageSpec

class KnowledgeConfig (M1, nodo "knowledge"):  mode: Literal["read", "navigate"]
                           pages: list[str]            # read: "ruta" o "ruta#ancla" (resueltas al publicar)
                           scope: str | None           # navigate: directorio del snapshot
                           selector: RefSpec | None    # navigate: DecisionModel sobre el enum de rutas del scope
                           purpose: Purpose; save_as: str
                           # resultados: ok, not_found, denied  (+ low_confidence solo en navigate)
```

Puertos (M0 §2.9):

```python
class KnowledgeSource:  capabilities() -> frozenset[str]
                        index(snapshot, view) -> list[PageMeta]          # visibles con `view`, por ruta
                        read(path, snapshot, view) -> PageRecord | None  # None: ausente o fuera de la vista
AuthzPort.knowledge_view(principal, purpose) -> KnowledgeView            # unidad 3
```

Interfaz de M12 (`agent_core.knowledge`, lo único que M2 y M8 importan):

```python
@dataclass(frozen=True)
class KnowledgeContext:  release; clock; ids; views: ViewService; vault: TokenVault; turn_id
class KnowledgeService:
    def __init__(self, source: KnowledgeSource, authz: AuthzPort)
    def read(self, node: KnowledgeNode, state: RunState, ctx: KnowledgeContext) -> tuple[RunState, str, list[EngineEvent]]
```

Cambios en otros módulos:

- **`RunState`** (M0): `pages: dict[save_as, list[PageView]]`, escrito solo por M12.
- **`respond.generate`** (M0/M1/M8): `knowledge_refs[]` se reemplaza por `knowledge_from: [save_as]` + `purpose` (por defecto `customer_answer`).
- **Puertos:** `KnowledgeSource` (M0); `AuthzPort.knowledge_view(principal, purpose) -> KnowledgeView` (unidad 3); `Release.knowledge_snapshot` (unidad 2, ya existe).

## 3. Comportamiento

### 3.1 `read`

1. Sin snapshot en la release → `not_found` con motivo `no_snapshot`. Un nodo `navigate` → `not_found` con motivo `navigate_unavailable` (§3.2).
2. `view = authz.knowledge_view(state.principal, purpose)`. Con `view.audiences` vacío no se llama a la fuente: todas las páginas pedidas quedan filtradas con motivo `view` y el resultado es `denied`.
3. Para cada página fija: `source.read(path, snapshot, view)`. `None` (ausente o fuera de la vista del servicio, que no lo distingue) es `missing`.
4. **Doble filtro:** el servicio filtra con `view` y M12 vuelve a filtrar, sin confiar en él (`filters.filter_reason`, en este orden; gana el primer motivo):
   - el `snapshot` de la página es el de la release (`snapshot`);
   - la audiencia está en la vista (`audience`) y, si la vista lo pide, está aprobada (`not_approved`);
   - `customer_answer`: solo `audience: public` + `status: approved` + vigente al instante del `Clock` (`valid_from`/`valid_to` inclusivos; `not_yet_valid`, `expired`) + `lang` del turno (`lang`);
   - `advisor_view`: `public` o `internal`;
   - `agent_guidance`: cualquiera, pero nunca citable al cliente (lo comprueba M8).
5. **Traducción:** si lo único que falla es `lang` en `customer_answer`, se busca en `source.index` la página del idioma del turno enlazada por `translation_of` (en cualquiera de los dos sentidos) y se entrega esa; su `ref` apunta a la traducción. Sin traducción, la página queda filtrada con motivo `lang`.
6. **Ancla:** `ruta#ancla` entrega la sección cuyo encabezado lleva el id explícito `{#ancla}` (`## Título {#ancla}`), hasta el siguiente encabezado de igual o mayor jerarquía. El id es explícito para que una traducción conserve el ancla. Un ancla inexistente es `missing`. `ruta` sola entrega la página completa.
7. **Proyección a vista `model` con M7:** la PII del texto pasa a tokens del vault del run (`tokenize_text`); si algún `source_refs` es externo (lleva `://`), el contenido además se envuelve como `untrusted_text`.
8. **Resultado:** `denied` si M12 retuvo alguna página; si no, `not_found` si falta alguna; si no, `ok`. Solo `ok` escribe `pages[save_as]` (en el orden del nodo, reemplazando lo anterior); cualquier otro resultado borra lo que hubiera bajo ese `save_as`, para que nada viejo quede citable tras un fallo.
9. **Hecho de procedencia:** `PageView.source` = `FactSource{kind: knowledge, ref: "ruta@snapshot#ancla"}`; se deriva de la página y no se escribe en `state.facts`.
10. Un `KnowledgeSource` que lanza una excepción (en `read` o `index`) → `not_found` con motivo `source_unavailable`.

Un `snapshot` de la release es `id@versión` (`kb-base@1.0.0`), así que un ref queda como `faq/cargos.md@kb-base@1.0.0#plazos`; la ruta no admite `@`, por eso el primer `@` separa la ruta.

### 3.2 `navigate` (fuera de la construcción)

Divulgación progresiva desde `index.md` del scope: el `selector` (un `DecisionModel` de M5) elige sobre el enum cerrado de rutas del scope; bajo umbral → `low_confidence`. Cada snapshot que cambie un scope obliga a recalibrar el selector.

**Estado:** el esquema del nodo y G0-20 existen; el handler no ejecuta `navigate`: sale por `not_found` con motivo `navigate_unavailable` (falla cerrada: nada citable). Construirlo es agregarlo en `KnowledgeService.read` sin cambiar `read`.

### 3.3 Reglas estáticas nuevas (M1)

Continúan la numeración de M1 (G0-15 y G0-16 ya las usa M1 rev. 2):

| ID | Regla | Dónde |
|---|---|---|
| G0-17 | Toda página de `knowledge.read` existe en el snapshot de la release (la ruta; el ancla no se ve en el manifiesto) | `validate_flow_for_release` |
| G0-18 | Un `respond` con `purpose: customer_answer` solo lee `knowledge_from` de nodos con `purpose: customer_answer` | `validate_flow` |
| G0-19 | Las páginas fijas de un nodo `customer_answer` son `public` + `approved` en el snapshot | `validate_flow_for_release` |
| G0-20 | `navigate`: el enum de salida del `selector` (`output_schema.properties.path.enum`) es exactamente el conjunto de rutas del scope (las páginas bajo `scope/`, sin su `index.md`) | `validate_flow_for_release` |
| G0-21 | Todo `knowledge_from` de un `respond` apunta a un nodo `knowledge` que lo domina | `validate_flow` |

G0-17, G0-19 y G0-20 necesitan el snapshot que fija la release, y un flow aislado no lo conoce. Por eso las corre `validate_registry` por cada release, con el snapshot de `ReleaseDecl.knowledge`; lo usan la CLI `agentcore validate` y el gate del registry (`registry/validation.py`, sobre la release candidata).

### 3.4 Comprobaciones nuevas del validador (M8)

| ID | Comprobación |
|---|---|
| 6 · `page_citations` | Cada `page_ref` citado está en `pages` de un `save_as` listado en `knowledge_from` del nodo |
| 7 · `page_audience` | En respuestas al cliente, cada página citada es `public` + `approved` + vigente al instante del `Clock` |

- Una cita es de página si tiene la forma `ruta@snapshot#ancla` (`parse_page_ref`); la comprobación 2 (`citations`) ya no las juzga.
- Una respuesta es **al cliente** si el nodo declara `purpose: customer_answer` **o** el principal del run es `customer`. Así un `respond` que se declara `advisor_view` dentro de un run de cliente no puede citar una página interna.
- La comprobación 7 falla cerrado: una página sin metadatos o sin instante del `Clock` no se acepta.
- Las cifras de páginas citadas cuentan para la comprobación 3 igual que los hechos (el texto de la página se lee con el mismo parser de cifras).
- `Responder.generate` arma las páginas permitidas desde `RunState.pages` y le pasa al modelo, en vista `model`, `pages: {save_as: [{ref, content}]}`.

### 3.5 Reclamos de éxito

Un hecho de conocimiento **no** alimenta reclamos de éxito: `derive_claims` (M1) ignora las páginas (un nodo `knowledge` no produce hechos y `knowledge_from` no es una lectura). Una página describe procedimientos, no el resultado de una acción de este run.

## 4. Invariantes

- Ninguna página `internal`, `agent_only` o `draft` llega a una respuesta al cliente.
- El snapshot es el de la release; editar el wiki solo entra con una release nueva.
- La auto-mejora puede proponer subir el snapshot, nunca aprobar páginas.
- Solo M12 escribe `RunState.pages`; nada de una página sale en vista `full` (ni `knowledge_read` lleva texto).

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| `KnowledgeSource` caído | rama `not_found` y evento con motivo `source_unavailable` (no hay salida `error`) |
| Release sin snapshot | `not_found`, motivo `no_snapshot` |
| Página filtrada (M12 o vista vacía) | `denied` |
| Página o ancla ausente | `not_found` |
| Página vencida al momento de responder | comprobación 7 falla → regenerar/plantilla |
| Nodo `navigate` | `not_found`, motivo `navigate_unavailable` |
| Falta el `KnowledgeService` en el cableado | `IllegalTransition` en M2 (error de cableado, como el `AgentPort`) |

## 6. Eventos que emite

`knowledge_read {node_id, purpose, result, refs, filtered_out: [{ref, reason}], missing: [ref], reason}` (evento nuevo de M0, versión mayor de contratos con el nodo). Lo construye M12; el handler de M2 lo entrega al turno. Solo referencias y motivos, nunca texto. `reason` ∈ `source_unavailable`, `navigate_unavailable`, `no_snapshot` o nulo; el motivo de cada página filtrada ∈ `audience`, `not_approved`, `expired`, `not_yet_valid`, `lang`, `snapshot`, `view`.

## 7. Pruebas

| ID | Caso | Archivo |
|---|---|---|
| T-M12-01 | `customer_answer` descarta páginas `internal` y `draft` aunque el servicio las devuelva | `tests/m12/test_service.py` |
| T-M12-02 | Respuesta que cita una página no `approved` se rechaza (comprobación 7) | `tests/m12/test_answer_checks.py` |
| T-M12-03 | Cita a una página de un `save_as` no listado se rechaza (comprobación 6) | `tests/m12/test_answer_checks.py` |
| T-M12-04 | Un flow con G0-17…G0-21 violadas no se publica (un fixture por regla) | `tests/m12/test_static_rules.py` |
| T-M12-05 | `derive_claims` ignora páginas | `tests/m12/test_static_rules.py` |
| T-M12-06 | `FileKnowledgeSource` sobre un árbol `knowledge/` pasa la suite de contrato | `tests/contracts/test_knowledge_contract.py` |

Además: tipos de M0 (`tests/m00/test_knowledge_types.py`), handler de M2 (`tests/m02/test_knowledge.py`), contrato de `AuthzPort.knowledge_view` (`tests/contracts/test_authz_contract.py`) y cableado (`tests/composition/test_knowledge_wiring.py`). El árbol de archivos sintético está en `tests/fixtures/knowledge/kb-base@1.0.0/` (el `agent-registry/knowledge/` de la demo no existe todavía en este repo).

## 8. Evaluación

Respuestas con citas a páginas no aprobadas que escapan (objetivo 0), tasa de `not_found`/`denied` (los eventos `knowledge_read` la dan), precisión del selector de `navigate` al umbral (si se construye).

## 9. Puntos de iteración

- `navigate` y `search` se agregan sin cambiar `read`.
- Servicio real de la unidad 7: reemplaza `FileKnowledgeSource` detrás del puerto.
- Las anclas por título (sin `{#id}` explícito) no existen; si se quieren, cambia solo `sections.extract_section`.

## 10. Definición de terminado

- [x] Decisiones de la sección 11 aprobadas (2026-09-30) y la spec general apuntando a este documento (cierra el tema #10).
- [x] `read` + filtros + comprobaciones 6 y 7 + G0-17…G0-21 con T-M12-01…06 en verde (`uv run pytest tests/m12 tests/contracts/test_knowledge_contract.py`).
- [x] Tipos y contratos en M0 (`PageMeta`, `PageView`, `Purpose`, `RunState.pages`, nodo `knowledge`, `knowledge_from` + `purpose`, evento `knowledge_read`); `contracts/` regenerado y `SCHEMA_VERSION` 1.0.0.
- [x] `FileKnowledgeSource` (y `InMemoryKnowledgeSource`) en `testing/fakes` con su suite de contrato.
- [x] `AuthzPort.knowledge_view` y su doble `TableAuthz`, con prueba de contrato.
- [x] Handler del nodo en M2, reglas en M1, comprobaciones 6 y 7 en M8 y cableado en `composition` (`EngineDeps.knowledge`).
- [x] `.importlinter`: M12 solo usa domain, ports y views; M2 y M8 importan solo su interfaz pública (contrato `knowledge_publica`).
- [x] Interfaz pública exportada y tipada, `mypy` strict, `ruff` y `lint-imports` en verde.
- [x] Sin hora ni azar fuera de `Clock`/`IdSource`, sin `float`, sin `full` en eventos ni `repr`.
- [ ] `navigate` y `search` (fuera de esta construcción).

## 11. Decisiones (2026-09-30, cierran el tema #10)

1. **`read` en el MVP de construcción:** no. El calendario (congelamiento 02/10) no lo permite; se construye en la fase 2 y `navigate` queda fuera del MVP.
2. **Numeración:** G0-17…G0-21 y comprobaciones 6 y 7 del validador de respuesta, como se propone en esta spec.
3. **Hechos de conocimiento fuera de los reclamos (3.5):** una página solo se cita; nunca alimenta un reclamo de éxito.
4. **Caída del `KnowledgeSource`:** el nodo sale por `not_found` y emite un evento con motivo `source_unavailable`; no se añade una salida `error` al esquema del nodo.
5. **Construcción (2026-09-30):** solo `read`; `navigate` está en el esquema con G0-20 activa y en runtime sale por `not_found`. `SCHEMA_VERSION` pasa a **1.0.0** (nodo nuevo y `knowledge_refs` reemplazado: cambio mayor según M0 §9).

## 12. Abiertos

- **`navigate` y `search`:** el selector, su calibración por snapshot y la propiedad `path` del `output_schema` (G0-20) quedan fijados solo por esta spec hasta que se construya.
- **`G0-06`:** `knowledge.not_found` y `knowledge.denied` no están en la lista de ramas de fallo con salida segura. Una respuesta que sigue sin páginas no puede citarlas (comprobación 6), pero el flow podría contestar sin ellas sin que nadie lo revise.
- **Anclas:** el `ruta#ancla` no se verifica al publicar (el manifiesto del snapshot no trae el texto); un ancla inexistente sale en runtime como `not_found`.
- **Servicio real (unidad 7):** el contrato de `KnowledgeSource` (que `read` devuelva `None` tanto para una página ausente como para una fuera de la vista) debe respetarlo, y su `knowledge_view` debe dar la misma vista que `TableAuthz` para `customer_answer`.
