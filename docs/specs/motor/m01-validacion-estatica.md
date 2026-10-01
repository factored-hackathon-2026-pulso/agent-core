# M1 — Esquema de flows y validación estática

- Estado: **rev. 5 · implementado** · Fase 1 (carga, G0-01 a G0-06, `derive_claims`, CLI) y fase 5 (G0-07 a G0-16, chequeos por agente)
- Paquete: `agent_core.flows`
- Origen: spec general §5 (catálogo y reclamos), §6.1, §6.2 (chequeos por agente), §13.1, §13.3
- ADRs: 0004 (catálogo cerrado, G0), 0007 (acción congelada, reclamos), 0009 (políticas, literales), 0011 (`compute`), 0016 (G0-15). ADR 0015 (conocimiento, rev. 4: G0-17 a G0-21; M12).
- Usa: M0 · Lo usan: M2 (`derive_claims`, `release_view`, `JSONLOGIC_OPS`, rutas y plantillas), CLI `agentcore validate`, unidad 2 (gate de release), CI de `agent-registry`
- **Requisito para empezar:** M0 terminado hasta sus tareas 4 (nodos), 5 (entidades, con `ModelProfile` y `Prompt.model_profile` de la rev. 5), 10 (`InMemoryRegistry`) y 12 (CLI).

**Changelog**

- rev. 2 (2026-09-29), revisión de M1:
  - **G0-04** se calcula quitando los nodos que esperan y exigiendo un grafo acíclico. La versión con SCC dejaba pasar bucles que esquivaban el nodo que espera.
  - **G0-05** se endurece:
    - todo camino a W pasa por la arista `C.yes` y W no está en un ciclo sin ella (antes solo "C domina a W");
    - `verify` enlazado por estructura (`W.next.ok == W.next.uncertain == V`);
    - una escritura por `confirm`;
    - un nodo `tool` sin `action_from` no puede usar una tool de escritura;
    - el readback es de clase `read`;
    - el reclamo también se exige desde C (bucles).
  - **`derive_claims` conservador:** propaga por toda tool y por `decide`, lee la plantilla de respaldo y `Prompt.reads`, y cuenta `end.output_map` como lector.
  - **Gramática única** de rutas y de variables de plantilla (`{{ ruta }}`). `Template.reads` se deriva del texto y no se declara a mano.
  - **Loader YAML seguro:** booleanos YAML 1.2, `Decimal`, sin claves duplicadas ni alias. También define la estructura de `agent-registry/` y `pin_release` (publicación simulada para la demo).
  - **`RegistryView`** con tipo de entidad y semántica de rangos; `release_view` para runtime.
  - **`Violation`** gana `flow` y `path`, y su orden pasa a ser total. Se define `FlowSchemaError`.
  - **Reglas nuevas:**
    - G0-15: `model_profile` de los prompts (ADR 0016);
    - G0-16: un flow de modo task no tiene nodos que esperan.
  - **Reglas ampliadas sin cambiar su ID:**
    - G0-01: ids duplicados, JSON Logic, rutas, validadores y `knowledge_refs`;
    - G0-02: tabla completa de referencias;
    - G0-03: `next` a nodos inexistentes y claves desconocidas;
    - G0-10: espacios de nombres.
  - **Pruebas:** T-M1-07 y T-M1-09 se corrigen; T-M1-27 a T-M1-45 son nuevas.
  - Contratos de la CLI (salida y códigos) y definición de terminado sin depender del repo `agent-registry`.
- rev. 3 (2026-09-29), registry (unidad 2, ADR 0017; spec `../2026-09-29-registry-design.md` §15). Cambios aditivos:
  - `ReleaseDecl.knowledge` (opcional): el snapshot de conocimiento que fija la release;
  - el registro de autoría lee `knowledge_snapshots/<id>@<versión>.yaml` (`KnowledgeSnapshot` de M0 rev. 6);
  - `pin_release` incluye el snapshot en la clausura: lo resuelve a su versión exacta, lo agrega a `Release.entities` y fija `Release.knowledge_snapshot`. Sin `knowledge`, la release no lleva snapshot;
  - `AuthoringRegistry` se construye desde objetos en memoria (`from_entities`), sin pasar por disco: el registry arma candidatas desde Postgres;
  - las funciones puras que reutiliza el registry (`validate_flow`, `validate_agent`, `validate_registry`, `derive_claims`, `pin_release`, `Violation`) ya salen de `agent_core.flows`, y `.importlinter` permite a `agent_core.registry` usar `domain`, `ports` y `flows`.
  - no cambian las reglas G0 ni el mensaje "conocimiento no habilitado (tema #10)": el nodo `knowledge` es de M12.
- rev. 5 (2026-09-30), transferencia entre agentes (ADR 0021; M0 `SCHEMA_VERSION` 1.2.0): G0-26, G0-27 y AG-03 (§3.4, §3.13); `decide.choices_from` con resultados `chosen`/`none`/`low_confidence` (G0-03, G0-10, G0-11); nodo `transfer` con el único resultado `rejected`. `transfer` no es terminal (no está en `TERMINAL`).
- rev. 4 (2026-09-30), M12 `read` (M0 rev. 10, `SCHEMA_VERSION` 1.0.0; `m12-conocimiento.md`):
  - **G0-17 a G0-21** (§3.4): G0-18 y G0-21 son reglas de flow (`FLOW_RULES`); G0-17, G0-19 y G0-20 necesitan el snapshot de la release y salen de una función nueva, `validate_flow_for_release(flow, snapshot, reg)`, que `validate_registry` llama por cada release;
  - **G0-01** ya no rechaza el conocimiento: `knowledge_refs` dejó de existir (M0) y el mensaje "conocimiento no habilitado" desaparece. Un `knowledge_refs` en YAML es ahora un error de esquema (campo extra);
  - **G0-03:** los resultados de un nodo `knowledge` dependen del modo (`read`: ok, not_found, denied; `navigate`: más `low_confidence`);
  - **G0-02:** el `selector` de un nodo `navigate` es una referencia a un `decision_model`;
  - `derive_claims` ignora las páginas (§3.6).
  - `pin_release` (la publicación simulada) también corre `validate_flow_for_release` con el snapshot de su release, así que un flow que viola G0-17, G0-19 o G0-20 no se publica; el gate del registry lo hace con `validate_registry`.

## 1. Propósito y límites

Tres cosas:

1. **Carga** de un registro de autoría desde YAML (`agent-registry/`) y **publicación simulada** de una release con referencias exactas para la demo (§3.11). Es la única parte con I/O, y solo lee archivos.
2. **Validación estática**, con funciones puras: gate G0 de un flow aislado y chequeos por agente que el gate de release necesita.
3. **Análisis que comparte con runtime:** `derive_claims`, el subconjunto de JSON Logic, la gramática de rutas y la de variables de plantilla. M2 los importa para que la validación y la ejecución usen exactamente las mismas definiciones.

Los esquemas de nodos (`Node`, `RESULTS`, `TERMINAL`, `WAITING`, configs) y `Flow` viven en `agent_core.domain` (M0 §2.5). M1 los usa; no los define.

Los flows de autoría llevan `RefSpec` (rangos permitidos). M1 **no** exige referencias exactas; eso lo exige `RegistryPort` al cargar una release en runtime (M0 §2.2).

**No hace:** ejecutar nodos (M2), ni el resto del gate de release: dueño único por intención, `tools_allowed`, aprobaciones de políticas e interrupciones son de la unidad 2. `pin_release` no es la unidad 2: es un sustituto para la demo y las pruebas.

## 2. Interfaz pública

```python
# --- Diagnóstico
class Violation(BaseModel, frozen=True):
    rule: str                 # "G0-05", "AG-01"
    flow: str | None          # "id@versión"
    node_id: str | None
    path: str | None          # JSON Pointer dentro de la entidad ("/nodes/3/config/args/monto") o ruta de archivo
    message: str              # en español, sin datos de cliente
    def sort_key(self) -> tuple[str, str, str, str, str]   # (rule, flow, node_id, path, message), None → ""
class FlowSchemaError(SchemaError):                        # SchemaError es de M0
    violations: list[Violation]                            # G0-01 o G0-09

# --- Esquema
def parse_flow(raw: JsonValue, *, source: str | None = None) -> Flow   # lanza FlowSchemaError

# --- Vista del registro
class RegistryView(Protocol):
    def resolve(self, kind: EntityKind, ref: RefSpec) -> RegistryEntity | None   # None si no existe; nunca lanza
def release_view(port: RegistryPort, release: Release) -> RegistryView           # runtime: vía release.entities, refs exactas

# --- Validación
def validate_flow(flow: Flow, reg: RegistryView) -> list[Violation]                        # G0-01…G0-11, G0-13…G0-16
def validate_flow_for_agent(flow: Flow, agent: Agent, reg: RegistryView) -> list[Violation]  # G0-12, AG-01 y AG-03
def validate_flow_for_release(flow: Flow, snapshot: KnowledgeSnapshot | None, reg: RegistryView) -> list[Violation]  # G0-17, G0-19, G0-20
def validate_agent(agent: Agent, reg: RegistryView) -> list[Violation]                     # G0-02, G0-12 y AG-03 sobre el agente
def derive_claims(flow: Flow, reg: RegistryView) -> Mapping[str, frozenset[str]]           # lector → ids de confirm

# --- Análisis compartido con runtime
JSONLOGIC_OPS: Mapping[str, tuple[int, int | None]]      # operador → (mín, máx) de argumentos
def jsonlogic_problems(expr: JsonValue) -> list[str]     # operador fuera de la lista o aridad inválida
class Path(BaseModel, frozen=True): ns: Literal["slots", "facts", "decisions", "readback"]; name: str | None; rest: tuple[str, ...]
def parse_path(s: str) -> Path | None                    # None si no es ruta (literal); lanza ValueError si parece ruta y no parsea
def value_paths(value: JsonValue) -> list[Path]          # rutas dentro de args, recursivo
def template_vars(text: str) -> frozenset[str]           # rutas de {{ … }}; lanza ValueError si está mal formada

# --- Registro de autoría
class ReleaseDecl(BaseModel, frozen=True): ...           # §3.11
class AuthoringRegistry:                                 # implementa RegistryView
    def resolve(self, kind, ref) -> RegistryEntity | None
    def all(self, kind: EntityKind) -> list[RegistryEntity]
    def releases(self) -> list[ReleaseDecl]
    def source(self, kind: EntityKind, id: str, version: str) -> str   # ruta del archivo, para los mensajes
def load_yaml(data: str | bytes) -> JsonValue                          # §3.10
def load_registry(root: pathlib.Path) -> tuple[AuthoringRegistry, list[Violation]]
class PinnedRelease(BaseModel, frozen=True): release: Release; entities: list[RegistryEntity]; aliases: dict[str, list[str]]  # agente → alias
def pin_release(reg: AuthoringRegistry, release_id: str) -> PinnedRelease   # lanza SchemaError si una referencia no resuelve o choca

# --- CLI
$ agentcore validate <ruta-registry> [--json]
```

- `WriteToolNode` es el nodo `tool` con `action_from`. En YAML sigue siendo `type: tool` y el discriminador de M0 (`node_kind`) lo decide por la presencia de `action_from`.
- `parse_flow` convierte `pydantic.ValidationError` en `FlowSchemaError` con una `Violation` por error (`path` = JSON Pointer del error) y agrega las comprobaciones de G0-01 que Pydantic no hace (§3.4).
- `testing/fakes` gana `registry_from_directory(root, release_id) -> InMemoryRegistry`: llama a `load_registry` + `pin_release` y carga el resultado en el `InMemoryRegistry` de M0. `testing` puede importar `agent_core.flows`; `agent_core` nunca importa `testing`.

## 3. Comportamiento

### 3.1 Catálogo (MVP)

- **Tipos del MVP:** `decide`, `rule`, `collect`, `tool` (lectura/`compute`), `tool` (escritura), `confirm`, `verify`, `respond`, `escalate`, `end`.
- **Tipos de producción:** `agent`, `subflow` y `await_approval` existen en el esquema. **G0-01 los rechaza** en el MVP con el mensaje "tipo de producción no habilitado".
- **Conocimiento (rev. 4):** el nodo `knowledge` es del catálogo (M12). `respond.generate` lleva `knowledge_from` y `purpose` en lugar de `knowledge_refs`.

`respond` lleva `claims: list[str] = []`; `verify` lleva `save_as`; `confirm` lleva `max_attempts = 2` y `reprompt_template?`. El resto de los esquemas está en M0 §2.5.

### 3.2 Rutas

Una sola gramática para `args`, `rule.expr`, `verify.predicate`, `escalate.priority_expr`, `end.output_map`, plantillas, `allowed_facts` y el `input_view` de `decide` y de `agent`:

```
ruta    := "slots."     nombre
         | "facts."     nombre ( ".value" ( "." campo )* )?
         | "decisions." nombre ( "." campo )+
         | "readback"   ( "." campo )*
nombre  := [a-z][a-z0-9_]*
campo   := [A-Za-z0-9_]+
```

- **Ruta frente a literal:** un string es ruta si empieza por `slots.`, `facts.`, `decisions.`, `readback.` o es exactamente `readback`. Si parece ruta y no parsea, es G0-01. Cualquier otro string es literal (`destino: USD`).
- **Recorrido:** en `args` las rutas se buscan recursivamente dentro de listas y objetos (`value_paths`). M2 resuelve con las mismas funciones.
- `facts.<nombre>` sin `.value` solo se admite en `allowed_facts` (el hecho completo, con su procedencia, para las citas de M8). En los demás lugares se exige `.value` (violación → G0-10).
- **Lectura:** una ruta `facts.<n>…` **lee el hecho** `n`, y `decisions.<n>…` lee la decisión `n`.

**Espacios de nombres permitidos por lugar** (violación → G0-10):

| Lugar | Espacios |
|---|---|
| `tool.args`, lectura | `slots`, `facts` |
| `tool.args`, `compute` | `slots`, `facts`, `decisions` |
| `confirm.action.args` | `slots`, `facts` |
| `rule.expr` (`var`, `missing`) | `slots`, `facts` |
| `verify.predicate` | `readback`, `slots`, `facts` |
| `verify.by` (`fact:<ruta>`) | `facts` |
| `escalate.priority_expr` | `slots`, `facts` |
| `end.output_map` (valores) | `slots`, `facts` |
| `decide.input_view` | `slots`, `facts` |
| `agent.input_view` | `slots`, `facts` |
| plantillas (`{{ }}`) | `slots`, `facts` |
| `generate.allowed_facts` | `facts` |
| `decide.choices_from` | `facts` (con `.value`) |

### 3.3 Plantillas y prompts

- **Variables de plantilla:** `{{ ruta }}` con espacios opcionales, donde `ruta` sigue §3.2. No hay lógica, filtros ni escape: un `{{` o `}}` que no forma una variable válida es un error. M2 y M8 renderizan con esta misma sintaxis.
- **`Template.reads` se deriva:** al cargar, `reads = ⋃ template_vars(texto)` sobre todos los locales.
  - Si el YAML declara `reads` y difiere de lo derivado → G0-01 en el archivo de la plantilla.
  - La entidad cargada lleva siempre el valor derivado. Un autor no puede omitir una lectura.
- **Prompts:** el texto de un `Prompt` no lleva variables (ADR 0016: se envía tal cual). Un `{{` en un prompt es G0-01. `Prompt.reads` se declara y cuenta como lectura en `derive_claims`.

### 3.4 Reglas G0 (flow aislado)

| ID | Regla | Algoritmo | Fase |
|---|---|---|---|
| G0-01 | Esquema | Lo decide `parse_flow` (y lo repite `validate_flow` para un `Flow` construido a mano). Casos: nodo fuera del catálogo o tipo de producción; `config` inválida; id de nodo duplicado; JSON Logic con operador fuera de `JSONLOGIC_OPS` o aridad inválida en `rule.expr`, `verify.predicate` o `escalate.priority_expr`; ruta mal formada; validador inválido o `decide`, que aún no se ejecuta (§3.4.1, m02 D14); archivo ilegible o con `id`/`version` distintos del nombre del archivo | 1 |
| G0-02 | Referencia inexistente | `reg.resolve(kind, ref)` sobre cada campo de la tabla §3.4.2 | 1 |
| G0-03 | Estructura del grafo | (a) todo destino de `next` es un nodo del flow; (b) toda clave de `next` es un resultado del tipo; (c) todo resultado del tipo tiene `next` (terminales: `next` vacío); (d) todo nodo es alcanzable desde el primero (BFS); (e) en `decide`, `branch_on` es una propiedad de primer nivel de `output_schema.properties` con `enum` de strings, ninguno igual a `low_confidence`, y los resultados del tipo son ese enum más `low_confidence`. Un `decide` con `choices_from` (ADR 0021) no usa el enum: su `branch_on` es `choice` y sus resultados son `chosen`, `none` y `low_confidence`. Un nodo `transfer` solo tiene el resultado `rejected` | 1 |
| G0-04 | Ciclo sin espera | Se quitan los nodos que esperan (`collect`, `confirm`, `respond` con `await: true`) y lo que queda debe ser acíclico, auto-bucles incluidos | 1 |
| G0-05 | Invariante de escritura | §3.5 | 1 |
| G0-06 | Rama de fallo sin salida segura | §3.7 | 1 |
| G0-07 | `agent` con tool que no es `read`/`compute` | `write_draft` tampoco lo es (§3.13) | 5 |
| G0-08 | `rule.expr` con literales de negocio | §3.9 | 5 |
| G0-09 | `respond.generate` sin `fallback_template_ref` | Lo detecta el esquema; `parse_flow` lo reporta como **G0-09**, no como G0-01 | 5 |
| G0-10 | Lectura fuera de su espacio de nombres | Tabla §3.2. En particular, `decisions.*` fuera de una tool `compute`, incluidos `confirm.action.args` y `rule.expr` | 5 |
| G0-11 | `decide` ramifica por un campo no calibrado | `branch_on ∈ model_def.calibrated_fields`; en un `decide` con `choices_from` esto exige `choice ∈ calibrated_fields` | 5 |
| G0-12 | Falta una plantilla o un prompt para un locale del agente | §3.8 | 5 |
| G0-13 | `claims` con un id que no es `confirm` del flow | Búsqueda por id | 5 |
| G0-14 | `end` con outcome no declarable, o mezcla de modos | `is_declarable(outcome, modo)`; todos los `end` en un mismo modo (§3.8) | 5 |
| G0-15 | Prompt sin perfil de modelo (ADR 0016) | Para cada `generate.prompt_ref` que resuelve, `reg.resolve(model_profile, prompt.model_profile)` no es `None` | 5 |
| G0-16 | Flow de modo task con nodos que esperan | Si el modo del flow es `task` (§3.8), no tiene `collect`, `confirm` ni `respond(await: true)`: un run task no tiene turnos que los contesten | 5 |
| G0-17 | Página de `knowledge.read` fuera del snapshot | Cada `pages[i]` (ruta; el ancla no se ve en el manifiesto) está en `snapshot.pages`; sin snapshot, todas fallan. Solo en `validate_flow_for_release` | 5 |
| G0-18 | `respond` `customer_answer` que lee un nodo `knowledge` de otro `purpose` | Para cada `generate.knowledge_from` de un `respond` con `purpose: customer_answer`, ningún nodo `knowledge` con ese `save_as` tiene otro `purpose` | 5 |
| G0-19 | Página fija de un nodo `customer_answer` que no es `public` + `approved` | Sobre `snapshot.pages`; solo en `validate_flow_for_release` | 5 |
| G0-20 | `navigate` con selector que no cubre el scope | `output_schema.properties.path.enum` del selector == rutas del snapshot bajo `scope/` (sin `index.md`), sin repetidos; sin snapshot no se puede verificar. Solo en `validate_flow_for_release` | 5 |
| G0-21 | `knowledge_from` sin un nodo `knowledge` que domine al `respond` | Cada nombre es el `save_as` de algún nodo `knowledge`, y quitando las aristas de salida de esos nodos el `respond` no es alcanzable desde la entrada | 5 |
| G0-24 | Tool de un nodo `agent` sin documentar | Toda tool de `tools_allowed` de un nodo `agent` lleva `description` y un `args_schema` dentro del subconjunto cerrado (`domain.schema`); el mensaje nombra la tool y la palabra clave fuera del subconjunto (§3.13) | 5 |
| G0-26 | `transfer` sin origen de destino o de directorio que lo domine | `target_from` (`decisions.<save_as>.choice`) nombra un `decide` con `choices_from` y `directory_from` es el `save_as` de un nodo `tool` de `directory/list`; quitando la arista `chosen` del `decide` (`none` y `low_confidence` no producen `choice`) y todas las aristas de salida del `tool`, el `transfer` no es alcanzable desde la entrada (ADR 0021, spec de transferencia §6) | 7 |
| G0-27 | Slot del paquete de transferencia no recolectado | Cada `packet.slots[i]` es el `slot` de algún `collect` del flow | 7 |
| G0-23 | Escritura `draft` sin `confirm` mal formada | La tool es `write_draft` con `readback_by: idempotency_key`; `ok` y `uncertain` van al mismo `verify` con `by: idempotency_key`, que ningún otro nodo de escritura comparte; el flow solo vuelve al nodo desde `verified` (§3.13) | 5 |
| G0-25 | Prompt de un nodo `agent` en modo nativo | El `model_profile` del prompt del `agent` es `structured: prompted` (§3.13) | 5 |
| G0-26 | `transfer` sin origen de destino o de directorio que lo domine | `target_from` (`decisions.<save_as>.choice`) nombra un `decide` con `choices_from` y `directory_from` es el `save_as` de un nodo `tool` de `directory/list`; quitando la arista `chosen` del `decide` (`none` y `low_confidence` no producen `choice`) y todas las aristas de salida del `tool`, el `transfer` no es alcanzable desde la entrada (ADR 0021, spec de transferencia §6) | 7 |
| G0-27 | Slot del paquete de transferencia no recolectado | Cada `packet.slots[i]` es el `slot` de algún `collect` del flow | 7 |

**Consecuencia de G0-16:** en el MVP un agente task no puede escribir, porque toda escritura exige un `confirm` (G0-05). G0-16 **no se relaja** (ADR 0019): un flow task escribe solo con tools `write_draft` (§3.13) o, en producción, con `await_approval` (ADR 0014).

#### 3.4.1 Validadores de `collect` (G0-01)

| `kind` | `value` |
|---|---|
| `type` | uno de `string`, `integer`, `decimal`, `date`, `boolean` |
| `regex` | string que compila con `re` y tiene 200 caracteres o menos |
| `enum` | lista no vacía de strings sin repetidos |
| `decide` | **no soportado todavía**: G0-01 lo rechaza (el intérprete no sabe qué campo de la decisión valida, m02 D14). Diseño previsto: string `RefSpec` de un `decision_model` (G0-02 lo resuelve) |

#### 3.4.2 Referencias (G0-02)

| Campo | Tipo de entidad |
|---|---|
| `decide.model`, `collect.validator.value` (con `kind: decide`) | `decision_model` |
| `rule.policy` | `policy` |
| `tool.tool`, `confirm.action.tool`, `verify.readback` | `tool` |
| `collect.prompt_ref`, `confirm.summary_template`, `confirm.reprompt_template`, `respond.template_ref`, `generate.fallback_template_ref` | `template` |
| `generate.prompt_ref` | `prompt` |

### 3.5 G0-05: invariante de escritura

Definiciones:

- `G` es el grafo del flow.
- `G − e` es el grafo sin la arista `e`.
- "Alcanzable desde X" incluye los caminos de longitud ≥ 1.

Condiciones por nodo (sin escrituras de por medio):

1. Todo nodo `tool` **sin** `action_from` ni `draft: true` usa una tool de clase `read` o `compute`. Una tool de escritura solo se invoca desde un nodo con `action_from` (con `confirm`) o con `draft: true` (solo `write_draft`, G0-23).
2. Todo `verify.readback` es una tool de clase `read`.

Condiciones para cada nodo de escritura W con `action_from: C`:

3. C existe y es `confirm`. Si no, se reporta y no se evalúa nada más de W.
4. La tool de `C.action` es de escritura (`is_write`) y declara `readback_by == "idempotency_key"`.
5. C tiene **una sola** escritura (un solo W con `action_from: C`).
6. **Paso por `yes`:** en `G − (C, yes)`, W no es alcanzable ni desde la entrada ni desde sus propios sucesores. Esto implica que C domina a W y que no se puede volver a ejecutar W sin una confirmación nueva (p. ej. `V.failed → W` es violación).
7. **Verificación enlazada:** `W.next.ok == W.next.uncertain == V`, con V un `verify` con `by: idempotency_key`. Ningún otro nodo de escritura tiene a V como destino de `ok` o `uncertain`. V es "el `verify` de X", con X = acción de C.
8. **Reclamos:** para cada lector R (§3.6) y cada X ∈ `derive_claims(flow)[R]`:
   - X tiene escritura W y `verify` V (si no, es violación: "reclamo sobre una acción sin escritura verificable");
   - en `G − (V, verified)`, R no es alcanzable desde la entrada **ni desde C**.

   Equivale a "todo camino hasta R pasa por `verified` de V, también cuando el flow vuelve a C y congela una acción nueva".

Las comprobaciones de alcanzabilidad son BFS con una arista quitada, O(N + E) cada una. Con flows de decenas de nodos, eso sobra para el objetivo de §8. El algoritmo se puede cambiar (por ejemplo, a dominadores de aristas) sin tocar la interfaz.

### 3.6 `derive_claims`

**Lectores:** todo `respond` y todo `end` con `output_map`. `derive_claims` devuelve una entrada para cada lector, con conjunto vacío si no reclama nada. `claims(R) = declarados(R) ∪ derivados(R)`, donde `declarados` es `R.config.claims` (solo `respond`).

**Productores:**

| Nodo | Produce | Entradas |
|---|---|---|
| `tool` (lectura o `compute`) | `facts[save_as]` | rutas de `args` |
| escritura | `facts[save_as]` | — |
| `verify` | `facts[save_as]` | la ruta de `by: fact:<ruta>`, si la hay |
| `decide` | `decisions[save_as]` | rutas de `input_view` (`None` ⇒ ninguna) |
| `agent` | `facts[save_as]` | rutas de `input_view` (vacío ⇒ ninguna). Las tools que el modelo llama no cuentan: sus argumentos no son rutas |

**Origen de X:** es el menor punto fijo que contiene:

- `facts[save_as]` de la escritura W de X y `facts[save_as]` de su `verify` V;
- la salida de todo productor que lee algún nombre del origen.

El análisis va por nombre y no depende del camino, así que es conservador: un nombre compartido por dos nodos queda contaminado si cualquiera de los dos lo está.

**Lo que lee R:**

- `respond` con `template_ref`: `template.reads`;
- `respond` con `generate`: `allowed_facts ∪ reads de fallback_template_ref ∪ prompt.reads`;
- `end`: los valores de `output_map`.

`derivados(R)` = {X : R lee algún nombre del origen de X}. Un lector con `claims(R) = ∅` es **seguro**. Las páginas de conocimiento no alimentan reclamos (M12): un nodo `knowledge` no es productor, `knowledge_from` no es una lectura de hechos y `respond` no suma páginas a `claims` (T-M12-05). Si una referencia de R no resuelve, se usa lo que sí resolvió: G0-02 ya rechaza el flow.

**Uso en runtime:** M2 obtiene los reclamos de cada `respond` con `derive_claims(flow, release_view(registry, release))`, una vez por `flow@v` (el resultado es puro y cacheable). Así, el conjunto que va en `response_emitted.claims` es el mismo que validó el gate.

### 3.7 G0-06: salidas seguras

Resultados de fallo:

- `decide.low_confidence`;
- `collect.max_attempts`;
- `tool.error`, `tool.timeout`, `tool.denied`;
- escritura: `denied` (`uncertain` ya lo cubre G0-05.7);
- `confirm.max_attempts`;
- `verify.failed`.

El destino de cada uno debe ser una salida segura:

- `collect`;
- `escalate`;
- `end(abstained | clarify_exhausted)`;
- `end(failed)` si el modo del flow es task;
- un `respond` seguro. En ese caso se sigue la cadena de `respond` seguros por su `next` hasta el primer nodo que no lo sea y se evalúa ese. Si la cadena vuelve a un nodo ya visitado, es segura: cada vuelta pasa por un `respond(await)`, por G0-04.

### 3.8 Modo del flow y chequeos por agente

- **Modo del flow:** `task` si tiene al menos un `end` y todos son declarables en modo task; `conversational` si todos lo son en modo conversacional; indefinido si no tiene `end`. La mezcla es G0-14.
- **AG-01** (`validate_flow_for_agent`): si el modo del flow está definido y es distinto de `agent.mode`, es violación. Un flow sin `end` sirve a cualquier modo.
- **G0-12 por flow** (`validate_flow_for_agent`): toda plantilla o prompt que referencia el flow (tabla §3.4.2) tiene `locales.keys() ⊇ agent.supported_locales`.
- **`validate_agent`:**
  - G0-02 sobre `entry_flow`, `understand`, `tools_allowed` y cada campo de `agent.templates`;
  - G0-12 sobre `agent.templates` (las plantillas del motor fuera de los flows).
- **Qué flows usa un agente** (lo aplica la CLI): en cada `ReleaseDecl` que lo incluye, `entry_flow ∪ release.flows ∪` los flows de las interrupciones `start_flow`.

### 3.9 G0-08: literales en `rule.expr`

Se recorre `expr`:

- Los argumentos de `missing` y el primero de `var` son rutas. Están exentos, pero deben parsear.
- El segundo argumento de `var` (valor por defecto) es un literal.
- Toda hoja escalar literal debe ser `null`, un booleano o un string de **E**, donde E es la unión de los valores de los validadores `enum` de los `collect` del mismo flow.
- Cualquier número (incluido `0`) o cualquier otro string es violación.
- Los literales dentro de listas (p. ej. el segundo argumento de `in`) se comprueban uno por uno.

Las cifras de negocio van en una `policy` protegida (ADR 0009). `verify.predicate` y `escalate.priority_expr` no están sujetos a G0-08, pero sí a la lista de operadores (G0-01).

### 3.10 JSON Logic y loader YAML

**`JSONLOGIC_OPS`** es el subconjunto cerrado que usa M2:

| Operador | Argumentos |
|---|---|
| `var` | 1–2 |
| `==`, `!=`, `>`, `>=`, `<`, `<=` | 2 |
| `and`, `or` | ≥ 1 |
| `!` | 1 |
| `in` | 2 |
| `if` | ≥ 3 e impar |
| `missing` | ≥ 1 |

Un nodo JSON Logic es un objeto con exactamente una clave. M2 importa esta constante; no mantiene su propia lista.

**`load_yaml`** usa PyYAML (`SafeLoader` puro, subclaseado con resolvedores propios; el registro es chico y así los tipos quedan estrictos):

- Booleanos solo `true`/`false` (YAML 1.2). `yes`, `no`, `on` y `off` son strings; así `next: {yes: …, no: …}` conserva sus claves.
- Números con punto o exponente → `Decimal` desde el texto; enteros → `int` en base 10 (un cero a la izquierda no es octal). `.nan` e `.inf` no se resuelven: quedan como strings y los rechaza el esquema donde se espera un número. Nunca `float`.
- Sin resolución de fechas ni timestamps: quedan como strings y los valida Pydantic.
- Las claves de un mapeo son siempre el texto del escalar: `true:` en `next` de un `rule` queda `"true"`, y `1:` queda `"1"`. Una clave que no es escalar es error. Una clave duplicada es error.
- Se rechazan los alias y anclas (`&`, `*`) y las etiquetas explícitas (`!!python/…`), y se admite un solo documento por archivo.
- Tamaño máximo: 1 MiB por archivo.

Cualquier error de `load_yaml` es una sola `Violation` G0-01 con la ruta del archivo en `path`.

### 3.11 Estructura del registro y publicación simulada

```
<raíz>/
  agents/  flows/  policies/  templates/  prompts/  tools/  decision_models/
  model_profiles/  language_detection/  injection_rulesets/  knowledge_snapshots/
      <id>@<versión>.yaml      # un id con "/" crea subcarpetas: templates/t/pedir_cargo@1.0.0.yaml
  releases/
      <release_id>.yaml        # ReleaseDecl
```

- **Archivos:**
  - `id` y `version` del contenido deben coincidir con el nombre del archivo (G0-01);
  - solo se leen archivos `.yaml`, y los demás se ignoran;
  - no se siguen enlaces simbólicos que salgan de la raíz.
- **Resolución de `RefSpec`** en `AuthoringRegistry.resolve`, siempre a la mayor versión que cumple (precedencia semver):

  | Forma | Cumple |
  |---|---|
  | sin versión | cualquier versión |
  | `X.Y.Z` | exacta |
  | `^X…` o `X` | mismo mayor, ≥ la indicada |
  | `~X.Y…` o `X.Y` | mismo mayor y menor, ≥ la indicada |

- **`ReleaseDecl`** es la release de autoría, un tipo de M1:

  ```yaml
  id: demo-2026-10
  agents: [{agent: "atencion@^1", aliases: [prod]}]
  flows: ["disputa-cargo@^1", "bloqueo-tarjeta@^1"]      # intenciones de la release (enum de Understand)
  interrupts: [...]                                      # Interrupt de M0
  language_detection: "lang-es-pt@1"
  injection_ruleset: "inj-base@1"                        # opcional
  knowledge: "kb-base@1"                                 # opcional: snapshot de conocimiento (rev. 3)
  max_input_chars: 4000
  ```

- **`pin_release`** (sustituto de la publicación de la unidad 2, solo para la demo y las pruebas):
  1. Calcula la clausura de entidades desde los agentes (`entry_flow`, `understand`, `templates`, `tools_allowed`), `flows`, las interrupciones (flows `start_flow`, `signal_policy`), `language_detection`, `injection_ruleset`, `knowledge` (rev. 3), cada referencia de cada flow (tabla §3.4.2) y el `model_profile` de cada prompt.
  2. Resuelve cada `RefSpec` con la regla anterior. Dos referencias al mismo `(tipo, id)` que resuelven a versiones distintas lanzan `SchemaError`, porque `Release.entities` guarda una versión por id.
  3. Reescribe todas las referencias a su versión exacta y construye `Release(status="active")`. El resultado pasa `require_exact_refs` (M0).
  4. No valida: quien la llama (la CLI o el arnés de la demo) corre la validación antes.

### 3.12 CLI `agentcore validate`

`agentcore validate <ruta> [--json]`:

1. `load_registry(ruta)`.
2. `validate_flow` sobre cada versión de cada flow cargado.
3. `validate_agent` sobre cada versión de cada agente.
4. Por cada `ReleaseDecl` y cada agente que incluye, `validate_flow_for_agent` sobre sus flows (§3.8).

**Salida:**

- Texto: una línea por violación, en el orden de `sort_key`: `G0-05 disputa-cargo@1.0.0 radicar /nodes/7: <mensaje>`, y al final el total por regla.
- `--json`: `{"format": 1, "ok": bool, "violations": [Violation…], "counts": {regla: n}}`, con la lista ordenada.

**Códigos de salida:** `0` sin violaciones; `1` con violaciones; `2` error de uso o raíz inexistente. El tiempo de validación se mide con `SystemClock.monotonic_ns()` y sale solo en la salida de texto.

### 3.13 Agentes internos (ADR 0019)

**Implementado (nodo `agent`, solo lectura y cálculo):**
- G0-01 ya no rechaza `agent` (`PRODUCTION_NODE_KINDS` = `subflow`, `await_approval`).
- **Referencias:** `tools_allowed` y `prompt_ref` son sitios de referencia (G0-02) y `pin_release` los fija.
- **G0-07** es alcanzable. **G0-15** cubre también el `prompt_ref` del `agent`. **G0-12** (locales) alcanza al prompt por ser un sitio de plantilla. **G0-06** trata `gave_up` como rama de fallo.
- **G0-22:** ninguna ruta `facts.<save_as>` de un nodo `agent` se lee en `rule.expr`, `verify.predicate`, `tool.args`, `confirm.action.args`, `escalate.priority_expr` ni `end.output_map`. Solo `respond` (plantilla, `allowed_facts` y su plantilla de respaldo) y el `input_view` de un `decide` o de otro `agent` pueden leerla. Para que un valor del agente llegue a una escritura debe pasar por un `collect` (la persona lo da) o por un `decide` con esquema.
- **`input_view` del `agent`** (2026-09-30, `SCHEMA_VERSION` 1.1.0): solo admite rutas (un literal o una ruta mal formada → G0-01), solo `slots` y `facts` con `.value` (G0-10). En `derive_claims` el `agent` es productor (§3.6): si su `input_view` lee el hecho de una escritura verificada, quien lea su salida reclama esa acción, como con `decide`. Pruebas en `tests/m01/test_agent_node.py`.
- **G0-24** (2026-09-30, unidad 5; **implementada**): toda tool de `tools_allowed` de un nodo `agent` lleva `description` y un `args_schema` dentro del subconjunto cerrado. Es el catálogo que `LLMAgentPort` le muestra al modelo; sin él falla cerrado en runtime, así que la regla lo detecta al validar. No alcanza a las tools fuera de un nodo `agent`.
- Pruebas: `tests/m01/test_agent_node.py`.

**Transferencia entre agentes (ADR 0021, rev. 5):**
- **`decide.choices_from`:** solo admite una ruta `facts.<x>.value...` (un literal o una ruta mal formada → G0-01; otro espacio de nombres o falta `.value` → G0-10). Ver G0-03 y G0-11 en §3.4.
- **G0-26 y G0-27** (§3.4) son reglas de flow (`FLOW_RULES`). Para G0-26 solo la arista `chosen` del `decide` cuenta como productora del `choice`: un `none` o `low_confidence` que llegue al `transfer` es violación.
- **G0-06** no cambia: `transfer` no cuenta como salida segura, pero el `transfer` de un flow de recepción se alcanza por el resultado `chosen` de un `decide` (no una rama de fallo) y su `rejected` va a un `escalate`.
- **AG-03** (`validate_flow_for_agent` y `validate_agent`): un flow con `transfer` exige `agent.mode == conversational`; un agente con `accepts` exige `routing`, `understand` y modo conversacional.
- Pruebas: `tests/m01/test_transfer_rules.py` (T-M1-47).
**Implementado (clase `write_draft`, fase 3 de la spec write-draft, 2026-09-30):**
- **Forma `draft` del nodo de escritura:** `tool` con `draft: true` declara su propia `tool` y `args` (sin `action_from`). Es un nodo `tool_write` más: ramas `ok`, `uncertain` y `denied`.
- **G0-05.1:** un nodo `tool` normal admite `read` y `compute`; una escritura va en un nodo con `action_from` (con `confirm`) o con `draft: true` (G0-23).
- **G0-23:** la tool de una escritura draft es `write_draft` con `readback_by: idempotency_key`; `next.ok == next.uncertain == V`, con V un `verify` con `by: idempotency_key`; ningún otro nodo de escritura tiene a V como destino de `ok` o `uncertain`; el flow solo vuelve al nodo desde la rama `verified` de V. No exige `confirm`.
- **Reclamos:** `respond.claims` y `derive_claims` usan el id del nodo draft como identificador de la acción (en vez del `confirm`); G0-13 acepta ese id; el invariante de G0-05.8 es el mismo.
- **G0-22, excepción acotada (ADR 0019 §1):** el `config.args` de una escritura draft puede leer **solo** `facts.<save_as>.value.changes` de un nodo `agent` cuyo `output_schema` es `agent_core.flows.DRAFT_OUTPUT_SCHEMA` (si dos agentes comparten `save_as`, valen las condiciones de todos); el resto de los `args` (p. ej. `origin`, `proposal_id`) lo fija el flow. El `save_as` de esa escritura queda marcado como salida de agente: no lo puede leer una `rule`, un `verify`, un `confirm` ni un `end`. Todos los demás destinos siguen vetados.
- **G0-25:** el prompt del `prompt_ref` de un nodo `agent` tiene un `model_profile` con `structured: prompted`.
- **AG-02** (`validate_flow_for_agent`): un agente cuyos flows referencian una tool `write_draft` tiene `invocable_by ⊆ {builder}` y `subject_kinds` vacío (los `subject_kinds` son cadenas libres: una lista de «datos de clientes» no es comprobable; con la lista vacía el agente nunca recibe subject, M9 §85).
- Pruebas: `tests/m01/test_draft_writes.py` y `tests/m01/test_agent_node.py`.

**Transferencia entre agentes (ADR 0021, rev. 5):**
- **`decide.choices_from`:** solo admite una ruta `facts.<x>.value...` (un literal o una ruta mal formada → G0-01; otro espacio de nombres o falta `.value` → G0-10). Ver G0-03 y G0-11 en §3.4.
- **G0-26 y G0-27** (§3.4) son reglas de flow (`FLOW_RULES`). Para G0-26 solo la arista `chosen` del `decide` cuenta como productora del `choice`: un `none` o `low_confidence` que llegue al `transfer` es violación.
- **G0-06** no cambia: `transfer` no cuenta como salida segura, pero el `transfer` de un flow de recepción se alcanza por el resultado `chosen` de un `decide` (no una rama de fallo) y su `rejected` va a un `escalate`.
- **AG-03** (`validate_flow_for_agent` y `validate_agent`): un flow con `transfer` exige `agent.mode == conversational`; un agente con `accepts` exige `routing`, `understand` y modo conversacional.
- Pruebas: `tests/m01/test_transfer_rules.py` (T-M1-47).

## 4. Invariantes

- **Determinismo y totalidad:** `validate_*` y `derive_claims` son deterministas y nunca lanzan excepción ante un flow mal formado; devuelven violaciones. Una `RegistryView` que lanza es un bug de la vista.
- **Orden total** por `sort_key`: el mismo registro produce la misma lista, byte a byte, sin importar el orden de los archivos en disco.
- **Sin cascada:**
  - si una referencia no resuelve (G0-02), las reglas que necesitan esa entidad se omiten para ese nodo;
  - las reglas de grafo (G0-03d, G0-04 a G0-06) trabajan sobre las aristas cuyo destino existe;
  - `validate_flow` corre las reglas de grafo solo si no hubo G0-01.
- Agregar una regla nunca cambia el resultado de las anteriores.
- Sin `float` en ningún valor cargado (§3.10).
- La validación y la ejecución comparten definiciones: M2 usa `JSONLOGIC_OPS`, `parse_path`, `value_paths`, `template_vars` y `derive_claims` de M1; no las reimplementa.

## 5. Fallas

| Caso | Comportamiento |
|---|---|
| YAML ilegible, demasiado grande o con alias, etiquetas o claves duplicadas | una sola violación G0-01 con la ruta del archivo |
| `id`/`version` distintos del nombre del archivo | G0-01; la entidad no se carga |
| Referencia que `reg` no puede resolver | G0-02; las reglas que dependen de esa referencia se omiten para ese nodo |
| `pin_release` con referencias sin resolver o en conflicto | `SchemaError` que las lista; no hay release parcial |
| Raíz inexistente en la CLI | código `2` |

## 6. Eventos que emite

Ninguno. La unidad 2 registra el resultado del gate.

## 7. Pruebas

- **Fixtures:**
  - un caso inválido por regla, construido como mutación mínima de un flow válido de prueba en `tests/m01/cases.py`, para que dé **solo** esa violación;
  - los flows válidos de prueba en `tests/m01/cases.py`;
  - un registro de ejemplo en `tests/m01/fixtures/registry/` con `disputa-cargo` completo (§12) y sus plantillas, tools, modelo, política, agente y release.
- **Datos:** todo es sintético (CLAUDE.md, regla 5).

| ID | Caso | §13 | Fase |
|---|---|---|---|
| T-M1-01…14 | Un flow inválido por regla G0-01…G0-14 da exactamente esa violación. En el MVP, T-M1-07 comprueba que un nodo `agent` da G0-01; T-M1-09 comprueba que `parse_flow` reporta G0-09 | 1 | 1 (01–06) · 5 (07–14) |
| T-M1-15 | `disputa-cargo` completo es válido | — | 1 |
| T-M1-16 | Escritura sin `confirm` o sin `verify` no se publica | 3 | 1 |
| T-M1-17 | `respond` con `claims: [X]` alcanzable antes del `verify` de X no se publica | 3 | 1 |
| T-M1-18 | `respond` sin `claims` que lee `facts.<save_as de W>` antes de `verified` no se publica (derivado) | 3 | 1 |
| T-M1-19 | `respond` que lee un `compute` derivado de W hereda el reclamo | 3 | 1 |
| T-M1-20 | Dos escrituras: `respond` que reclama X, después de `verified` de X y antes del `verify` de Y, se publica | 3 | 1 |
| T-M1-21 | `respond` con reclamos en rama `failed`/`denied` no se publica; sin reclamos sí | 3 | 1 |
| T-M1-22 | `end(abandoned)` o `end(escalated)` no se publica; `end(resolved)` + `end(completed)` no se publica | 1 | 5 |
| T-M1-23 | Agente `task` con flow de `end(resolved)` no pasa el chequeo por agente (AG-01) | 1 | 5 |
| T-M1-24 | En flow task, rama de fallo a `end(failed)` se publica | 1 | 5 |
| T-M1-25 | `rule.expr` con `500` o `0` es violación; con un valor de un `enum` de `collect` del flow no; las rutas de `var`/`missing` no cuentan | — | 5 |
| T-M1-26 | Propiedad (`hypothesis`): en flows válidos generados (esqueleto `confirm → escritura → verify → respond` con ramas seguras aleatorias), quitar cualquier arista `verified` hace aparecer G0-05 si hay un lector con reclamos detrás | — | 5 |
| T-M1-27 | `generate.prompt_ref` cuyo `model_profile` no existe → G0-15 | — | 5 |
| T-M1-28 | Flow de modo task con `collect`, `confirm` o `respond(await)` → G0-16 | 1 | 5 |
| T-M1-29 | `load_yaml`: `yes`/`no`/`on`/`off` quedan como strings y `true`/`false` como booleanos (salvo como claves, que quedan como texto); `500.00` → `Decimal`; `012` → `12`; clave duplicada, alias, etiqueta `!!python` y dos documentos son error; una fecha sin comillas y `.nan` quedan como strings | — | 1 |
| T-M1-30 | G0-04: componente con un `collect` y un bucle A↔B que lo esquiva es violación; auto-bucle sin espera es violación; `confirm` con `unclear` a sí mismo no | — | 1 |
| T-M1-31 | G0-05.6: `C.no → … → W` es violación; `V.failed → W` es violación | 3 | 1 |
| T-M1-32 | G0-05.1/2/5: nodo `tool` sin `action_from` con tool de escritura; `readback` que no es `read`; dos escrituras con el mismo `action_from` | 3 | 1 |
| T-M1-33 | G0-05.7: `ok` y `uncertain` a nodos distintos, o a un nodo intermedio antes del `verify`, es violación | 3 | 1 |
| T-M1-34 | G0-05.8: el flow vuelve a C después de `verified`; un `respond` alcanzable por `C.no` que reclama X es violación | 3 | 1 |
| T-M1-35 | `derive_claims` conservador: hereda por la plantilla de respaldo, por una tool de lectura que lee el resultado de W, por `decide` + `compute`, y por `end.output_map` | 3 | 1 |
| T-M1-36 | Plantillas: `reads` derivado de `{{ }}`; `reads` declarado distinto → G0-01; `{{` mal formado → G0-01; prompt con `{{` → G0-01 | — | 1 |
| T-M1-37 | G0-03: `next` a un nodo inexistente, clave de `next` desconocida, terminal con `next`, enum de `decide` que incluye `low_confidence`; id duplicado → G0-01 | 1 | 1 |
| T-M1-38 | G0-01: operador JSON Logic fuera de la lista o aridad inválida (en `rule`, `predicate` y `priority_expr`); ruta mal formada; regex que no compila (un `knowledge_refs` ya no existe: lo rechaza el esquema de M0) | 1 | 1 |
| T-M1-39 | G0-10: `rule` que lee `decisions.*`; `confirm.action.args` con `decisions.*`; plantilla con `decisions.*` | — | 5 |
| T-M1-40 | Sin cascada: una referencia que no resuelve da solo G0-02 para ese nodo | — | 1 |
| T-M1-41 | Determinismo: la lista sale en el orden de `sort_key`; reordenar los nodos (salvo el primero) da las mismas violaciones salvo los índices de `path`; `validate_flow` no lanza sobre flows mal formados generados con `hypothesis` | — | 1 |
| T-M1-42 | `AuthoringRegistry.resolve`: sin versión, exacta, `^`, `~`, `X` y `X.Y` eligen la mayor versión que cumple | 11 | 1 |
| T-M1-43 | `pin_release` sobre el registro de ejemplo: todas las referencias quedan exactas; cargado en `InMemoryRegistry`, `get` funciona; dos rangos que resuelven a versiones distintas del mismo id lanzan | 11 | 1 |
| T-M1-44 | CLI: código `0` sobre el registro de ejemplo, `1` con un flow inválido y `2` con una raíz inexistente; `--json` con el formato de §3.12 y la lista ordenada | — | 1 |
| T-M1-45 | `derive_claims` da lo mismo con `AuthoringRegistry` que con `release_view` sobre la release fijada | — | 1 |
| T-M1-46 | G0-24: una tool de `tools_allowed` de un nodo `agent` sin `description` o sin `args_schema`, o con un `args_schema` fuera del subconjunto (el mensaje nombra la palabra clave), falla; una tool sin documentar fuera de un nodo `agent` no dispara G0-24 (`tests/m01/test_agent_node.py`) | — | 5 |
| T-M1-47 | Transferencia: el flow de recepción es válido; `decide` con `choices_from` exige `chosen`/`none`/`low_confidence` y `branch_on: choice` (G0-03), lee solo `facts` (G0-10); G0-26 (destino y directorio que dominan al `transfer`, camino que esquiva el `decide`), G0-27 y AG-03 (`tests/m01/test_transfer_rules.py`) | — | 7 |

## 8. Evaluación

Sin métricas de runtime. Se reportan:

- flows rechazados por regla en el CI de `agent-registry`;
- tiempo de validación del registro completo, con un objetivo de menos de 2 s (T-M1-44 lo registra sobre un registro sintético de 50 flows de 100 nodos).

## 9. Puntos de iteración

- Regla nueva = ID nuevo + fixture inválido + fila en la tabla §3.4. No se renumera nada existente.
- Habilitar un tipo de producción = quitarlo de la lista negra de G0-01 y agregar sus reglas.
- Los algoritmos de alcanzabilidad se pueden cambiar sin tocar la interfaz.
- La sintaxis de plantilla puede crecer (filtros de formato) si `template_vars` sigue extrayendo todas las rutas leídas.

## 10. Definición de terminado

- **Fase 1:**
  - `load_yaml`, `load_registry` y `parse_flow`;
  - G0-01 a G0-06 con las ampliaciones de la rev. 2;
  - `derive_claims`, `JSONLOGIC_OPS`, rutas, `template_vars`, `release_view` y `pin_release`;
  - CLI con texto y `--json`;
  - `registry_from_directory` en `testing/fakes`;
  - pruebas de fase 1 de §7 en verde;
  - `agentcore validate tests/m01/fixtures/registry` corriendo en el CI de `agent-core`.
- **Fase 5:** G0-07 a G0-16, AG-01, `validate_agent` y el resto de las pruebas.
- La integración en el CI de `agent-registry` se hace cuando exista el repo; no bloquea el cierre de M1.

## 11. Abiertos

- ~~**Numeración de las reglas de conocimiento** (tema #10)~~ **Resuelto 2026-09-30:** G0-17 a G0-21 (rev. 4); pruebas en `tests/m12/test_static_rules.py` (T-M12-04). Las reglas de agentes internos (§3.13) empiezan en G0-22 (G0-24 se sumó con la unidad 5).
- **Agentes internos (ADR 0019):** G0-23 y AG-02 están solo especificadas. Falta decidir si G0-22 debe cubrir también los `facts` de un `tool` `compute` que reciba una salida del agente, y si un `collect.prompt_ref` puede mostrar la salida del `agent` (hoy lo rechaza G0-22).
- **Formato de `decide.input_view`:** lo define M5. M1 lo trata como una lista de rutas (§3.2); si M5 cambia la forma, cambia la tabla de productores de §3.6.

## 12. Apéndice: nodos omitidos de `disputa-cargo`

La spec general §5 abrevia el ejemplo. El fixture de T-M1-15 (`tests/m01/fixtures/registry/flows/disputa-cargo@1.0.0.yaml`) sigue el `DISPUTA_CARGO` de M0 (`tests/m00/fixtures.py`) con dos diferencias: `elegir` lee `facts.candidatas.value` (la gramática de §3.2 exige `.value` en `args`), y `no_confirmado` va a `end(abstained)`, porque `end(cancelled)` no es una salida segura para `confirm.max_attempts` (G0-06, spec general §6.1.6). Los nodos omitidos en la spec general son:

```yaml
  - {id: aclarar, type: respond, config: {template_ref: t/aclarar_cargo, await: true}, next: {next: pedir_cargo}}   # seguro
  - {id: no_confirmado, type: respond, config: {template_ref: t/no_confirmado}, next: {next: fin_abstenido}}      # seguro
  - {id: fin_abstenido, type: end, config: {outcome: abstained}}
  - {id: fin_cancelado, type: end, config: {outcome: cancelled}}
  - {id: esc_sin_datos, type: escalate, config: {reason_code: low_confidence}}
  - {id: esc_tool, type: escalate, config: {reason_code: tool_failure}}
  - {id: esc_monto, type: escalate, config: {reason_code: "policy:escalamiento-disputa-monto", target_queue: disputas}}
  - {id: esc_verif, type: escalate, config: {reason_code: verification_failed}}
```

- **Plantillas:** `t/pqr_radicado` lee `{{ facts.pqr_verificada.value.id }}`; las demás no leen hechos. Todas tienen `es` y `pt`.
- **Modelo `match-cargo`:** `output_schema.properties.match.enum = [unica, ninguna, varias]` y `calibrated_fields: [match]`.
- **Modo:** el flow es conversacional (`end(resolved|cancelled|abstained)`). En la fase 1 lo corre un arnés que maneja M2 con `Resume` guionados, sin M4 (índice §7).
