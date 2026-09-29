# M1 — Esquema de flows y validación estática

- Estado: borrador · Fase 1 (reglas G0-01 a G0-06) y fase 5 (G0-07 a G0-14)
- Paquete: `agent_core.flows`
- Origen: spec general §5 (catálogo y reclamos), §6.1, §6.2 (chequeos por agente), §13.1, §13.3
- ADRs: 0004 (catálogo cerrado, G0), 0007 (acción congelada, reclamos), 0009 (políticas, literales), 0011 (`compute`)
- Usa: M0 · Lo usan: M2 (esquemas), unidad 2 (gate de release), CI de `agent-registry`

## 1. Propósito y límites

Dos cosas, ambas **funciones puras** sin I/O:

1. **Parseo** de flows de autoría contra los esquemas de nodos. Desde M0 rev. 2 los modelos Pydantic de cada nodo, `Node`, `RESULTS`, `TERMINAL` y `WAITING` viven en `agent_core.domain.nodes` (M0), para que `Flow` sea un tipo de M0 sin import circular. M1 los usa; no los define.
2. **Validación estática** de un flow aislado (gate G0) y de los chequeos por agente que el gate de release necesita.

Los flows de autoría llevan `RefSpec` (rangos permitidos); M1 **no** exige referencias exactas. Eso lo exige el registro al cargar una release en runtime (M0 §2.2).

**No hace:** ejecutar nodos (M2), resolver versiones (unidad 2), ni el resto del gate de release (dueño único por intención, aprobaciones de políticas, interrupciones: unidad 2).

## 2. Interfaz pública

```python
# Esquema: Node, RESULTS, Flow y los configs vienen de agent_core.domain (M0 §2.5)
def parse_flow(raw: dict) -> Flow         # lanza FlowSchemaError con ruta y regla G0-01

# Validación
class Violation:  rule: str; node_id: str | None; message: str
class RegistryView(Protocol):               # vista de autoría del registro, sin I/O en pruebas
    def exists(ref: RefSpec) -> bool
    def tool_def(ref: RefSpec) -> ToolDef                    # tipos de M0
    def model_def(ref: RefSpec) -> DecisionModelDef
    def template(ref: RefSpec) -> Template                   # Template.reads y Template.locales
def validate_flow(flow: Flow, reg: RegistryView) -> list[Violation]
def validate_flow_for_agent(flow: Flow, agent: Agent, reg: RegistryView) -> list[Violation]   # G0-12, modo
def derive_claims(flow: Flow, reg: RegistryView) -> dict[str, frozenset[str]]  # respond_id → confirm_ids

# CLI (para CI de agent-registry)
$ agentcore validate <ruta-registry> [--json]
```

`WriteToolNode` es el nodo `tool` con `action_from`; en YAML sigue siendo `type: tool` y el discriminador de M0 (`node_kind`) lo decide por la presencia de `action_from`. `parse_flow` convierte `pydantic.ValidationError` en `FlowSchemaError` y agrega el rechazo de tipos de producción (G0-01). `InMemoryRegistry` (M0) gana aquí la carga desde YAML de `agent-registry/`.

## 3. Comportamiento

### 3.1 Catálogo (MVP)

`decide`, `rule`, `collect`, `tool` (lectura/`compute`), `tool` (escritura), `confirm`, `verify`, `respond`, `escalate`, `end`. `agent`, `subflow` y `await_approval` existen en el esquema como tipos de producción y **G0-01 los rechaza** en el MVP con el mensaje "tipo de producción no habilitado".

`respond` lleva `claims: list[str] = []`. `verify` lleva `save_as`. `confirm` lleva `max_attempts = 2` y `reprompt_template?`. Los esquemas completos salen de la tabla de §5 de la spec general.

### 3.2 Reglas G0 (flow aislado)

| ID | Regla (§6.1) | Algoritmo |
|---|---|---|
| G0-01 | Nodo fuera del catálogo o `config` inválida | `parse_flow` |
| G0-02 | Referencia inexistente (`tool@v`, `policy@v`, `decision_model@v`, `template_ref`, `prompt_ref`) | `reg.exists` sobre cada ref |
| G0-03 | Nodo inalcanzable o resultado declarado sin `next` | BFS desde el primer nodo; `RESULTS[type] ⊆ next.keys()` (terminales exentos). En `decide`, además, cada valor del enum de `branch_on` en `output_schema` del modelo debe estar en `next` (no hay `branches`, M0 rev. 4) |
| G0-04 | Ciclo sin nodo que espere al principal | componentes fuertemente conexos (Tarjan); cada SCC con ciclo debe contener `collect`, `confirm` o `respond(await: true)` |
| G0-05 | Invariante de escritura | ver 3.3 |
| G0-06 | Rama de fallo que no termina en salida segura | ver 3.4 |
| G0-07 | `agent` con tool que no es `read`/`compute` | solo producción; en MVP lo cubre G0-01 |
| G0-08 | `rule.expr` con literales de negocio | recorrido de JSON Logic: se aceptan `null`, booleanos y valores de enums declarados; cualquier número o texto es violación |
| G0-09 | `respond.generate` sin `fallback_template_ref` | esquema |
| G0-10 | Argumento que lee `decisions.*` en tool no `compute` | recorrido de `args` + `reg.tool_def(...).risk_class` |
| G0-11 | `decide` ramifica por campo no calibrado | `branch_on ∈ reg.model_def(...).calibrated_fields` |
| G0-12 | Falta plantilla/prompt para un locale del agente | `validate_flow_for_agent`: `supported_locales ⊆ reg.template(ref).locales` |
| G0-13 | `claims` con un id que no es `confirm` del flow | búsqueda por id |
| G0-14 | `end` con outcome no declarable, o mezcla de modos | `Outcome.is_declarable`; todos los `end` del flow en un mismo modo |

**Chequeo por agente** (lo llama el gate de release): el modo de los `end` del flow coincide con `agent.mode` (§6.2).

### 3.3 G0-05: invariante de escritura

Para cada nodo de escritura W con `action_from: C`:

1. C existe, es `confirm` y su `action.tool` es la tool de W.
2. **C domina a W**: todo camino desde la entrada hasta W pasa por C (árbol de dominadores, Cooper–Harvey–Kennedy).
3. `next.ok` y `next.uncertain` de W llevan a un `verify` V cuyo `by` es `idempotency_key` sobre la acción de C. (V es "el `verify` de X", con X = acción de C.)
4. `reg.tool_def(W.tool).readback_by == "idempotency_key"`.
5. **Reclamos:** para cada `respond` R y cada X ∈ `derive_claims(flow)[R]`: se quita del grafo la arista `V.verified`; si R sigue siendo alcanzable desde la entrada, es violación. (Equivale a "todo camino hasta R pasa por `verified` de V".)

### 3.4 `derive_claims`

`claims(R) = R.claims ∪ derivados(R)`, donde derivados son las acciones X tales que R lee un hecho en `origen(X)`:

- `origen(X)` = {`save_as` de W, `save_as` de V} ∪ todo hecho `compute` cuyo `args` lee transitivamente alguno de ellos (punto fijo sobre los nodos `tool` de clase `compute`).
- "R lee" = `reg.template(template_ref).reads` ∪ `generate.allowed_facts`.

Un `respond` con `claims(R) = ∅` es **seguro**.

### 3.5 G0-06: salidas seguras

Para cada resultado de fallo (`low_confidence`, `error`, `timeout`, `denied`, `failed`, `max_attempts`, y `uncertain` que no vaya a `verify`), su destino debe ser: `collect`, `respond` seguro, `escalate`, `end(abstained | clarify_exhausted)` o, si el flow es de modo task, `end(failed)`. Un `respond` seguro puede encadenar a otro nodo; se sigue la cadena de `respond` seguros hasta el primer nodo que no lo sea y se evalúa ese.

## 4. Invariantes

- `validate_flow` es determinista y total: nunca lanza excepción por un flow mal formado, devuelve violaciones.
- El orden de las violaciones es estable (por regla y luego por `node_id`), para que el diff en CI sea legible.
- Agregar una regla nunca cambia el resultado de las anteriores.

## 5. Fallas

| Caso | Comportamiento |
|---|---|
| YAML ilegible | una sola violación G0-01 con la ruta del archivo |
| Referencia que `reg` no puede resolver | G0-02; las reglas que dependen de esa ref se omiten para ese nodo (sin falsos positivos en cascada) |

## 6. Eventos que emite

Ninguno. La unidad 2 registra el resultado del gate.

## 7. Pruebas

Un fixture inválido por regla en `tests/m01/fixtures/invalid/g0-XX-<caso>.yaml`, más los válidos en `tests/m01/fixtures/valid/` (incluido `disputa-cargo` de la spec general).

| ID | Caso | §13 |
|---|---|---|
| T-M1-01…14 | Un flow inválido por regla G0-01…G0-14 da exactamente esa violación | 1 |
| T-M1-15 | `disputa-cargo` completo es válido | — |
| T-M1-16 | Escritura sin `confirm` o sin `verify` no se publica | 3 |
| T-M1-17 | `respond` con `claims: [X]` alcanzable antes del `verify` de X no se publica | 3 |
| T-M1-18 | `respond` sin `claims` que lee `facts.<save_as de W>` antes de `verified` no se publica (derivado) | 3 |
| T-M1-19 | `respond` que lee un `compute` derivado de W hereda el reclamo | 3 |
| T-M1-20 | Dos escrituras: `respond` que reclama X, después de `verified` de X y antes del `verify` de Y, se publica | 3 |
| T-M1-21 | `respond` con reclamos en rama `failed`/`denied` no se publica; sin reclamos sí | 3 |
| T-M1-22 | `end(abandoned)` o `end(escalated)` no se publica; `end(resolved)` + `end(completed)` no se publica | 1 |
| T-M1-23 | Agente `task` con flow de `end(resolved)` no pasa el chequeo por agente | 1 |
| T-M1-24 | En flow task, rama de fallo a `end(failed)` se publica | 1 |
| T-M1-25 | `rule.expr` con `500` es violación; con un valor de enum declarado no | — |
| T-M1-26 | Propiedad (`hypothesis`): en grafos aleatorios válidos, quitar cualquier arista `verified` hace aparecer G0-05 si hay un `respond` con reclamos detrás | — |

## 8. Evaluación

Sin métricas de runtime. Se reportan: flows rechazados por regla en CI de `agent-registry` y tiempo de validación del registro completo (objetivo < 2 s).

## 9. Puntos de iteración

- Regla nueva = ID nuevo + fixture inválido + fila en la tabla 3.2. No se renumera nada existente.
- Habilitar un tipo de producción = quitarlo de la lista negra de G0-01 y agregar sus reglas.
- El algoritmo de dominadores se puede cambiar sin tocar la interfaz.

## 10. Definición de terminado

- Fase 1: esquema completo, G0-01…G0-06 con `derive_claims`, T-M1-01…06 y T-M1-15…21.
- Fase 5: G0-07…G0-14, chequeo por agente y el resto de pruebas.
- CLI `agentcore validate` corriendo en CI de `agent-registry`.

## 11. Abiertos

- Numeración de las reglas de conocimiento (tema #10): M12 propone continuar en G0-15.
