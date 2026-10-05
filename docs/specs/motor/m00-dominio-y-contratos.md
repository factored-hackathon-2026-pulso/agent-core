# M0 — Dominio y contratos

- Estado: **rev. 13 · implementado** (fase 1; `write_draft`, transferencia entre agentes y `SCHEMA_VERSION` 1.2.0, 2026-09-30; `Agent.metrics` y `SCHEMA_VERSION` 1.3.0, 2026-10-02) · Fase 1
- Paquetes: `agent_core.domain`, `agent_core.ports`, `testing/fakes`
- Origen: spec general §2, §5 (esquemas de nodos), §8 (estado), §10 (códigos), §14 (dependencias)
- ADRs: 0001 (stack), 0002 (contratos), 0006 (principal y delegación), 0007 (acciones), 0008 (vistas y claves)
- Usa: — · Lo usan: todos los módulos

**Changelog**

- rev. 2 (2026-09-28), revisión de M0:
  - los esquemas de nodos pasan de M1 a M0 (evita el import circular `domain → flows`);
  - los tipos que cruzan fronteras de módulos pasan a M0 (`EscalationRequest`, `RejectedDraft`, `ConfirmationPrompt`, `DecisionModelDef`, `ToolDef`…);
  - puerto `IdSource` para IDs y secretos deterministas;
  - `Decimal` como número JSON y `canonical_bytes()` (JCS) en M0;
  - `RefSpec` (autoría) separado de `EntityRef` (runtime, exacta);
  - las vistas de resultados de tools las calcula M7, no la unidad 3;
  - `OnBehalfOf.grantee` (enmienda de ADR 0006);
  - `UnitOfWork` con lease de turno y versión;
  - `ActionState` con `uncertain` y `denied`; evento `action_cancelled`;
  - `Awaiting.input`; plantillas del motor en `Agent.templates`;
  - `KeyProvider` con claves por propósito;
  - dobles de prueba por consumidor.
- rev. 3 (2026-09-28), métricas de eficiencia y tiempos:
  - `Clock.monotonic_ns()` para medir duraciones (nunca para decidir);
  - `latency_ms` en `tool_called`; uso del LLM (`llm`) en `response_emitted` y `response_failed`;
  - evento nuevo `turn_completed` con la duración del turno y el desglose por etapa;
  - `MEASURED_FIELDS`: campos de medición, que el replay excluye de la comparación.
- rev. 4 (2026-09-28), precisiones para implementar:
  - `decide` pierde `branches`: sus resultados (valores de `branch_on` y `low_confidence`) son claves de `next`, como en todos los nodos;
  - `RunState.inactive_after` (= `last_activity_at + agent.inactivity_ttl`) para que `list_inactive` filtre sin conocer al agente;
  - `acquire_turn` es visible de inmediato (fuera de la transacción); `release_turn` se aplica con el commit;
  - `collect.validator` es opcional (sin validador: texto no vacío), como en el ejemplo `disputa-cargo`;
  - `turn_started.guards` y `client_turn_id` son opcionales (un `start_run` en modo task no tiene texto ni guardas);
  - `ToolStatus` y `Command` viven en `domain` (los usan eventos); `ports` los reexporta;
  - `EnvKeyProvider` vive en `agent_core/adapters/` (lo usa el proceso de la demo, no solo pruebas);
  - `SCHEMA_VERSION` es una constante de `agent_core.domain`; `agentcore contracts` escribe `contracts/VERSION` desde ella.

- rev. 5 (2026-09-28), LLM gateway (unidad 5, ADR 0016):
  - `EntityKind.model_profile` y entidad `ModelProfile` (+ `ModelPrice`, `StructuredMode`); `Prompt.model_profile`;
  - `GenerationResult` con `tokens_in`/`tokens_out`; error `GatewayError(kind)`;
  - `LlmUsage` con `tokens_in`/`tokens_out` y `cost_known`;
  - `UnitOfWork.add_usage` (M4 acumula costo y hits en la transacción del turno); `CostCounters` pasa a M4.
- rev. 7 (2026-09-29), cableado del motor (opción 2 de las llamadas de Understand, decidida por el usuario). Cambio aditivo:
  - `BudgetsUsed.turn_understand_calls: int = 0` (lo suma M4, lo reinicia `begin_turn`); `contracts/` regenerado (`BudgetsUsed`, `RunState`). Los estados guardados siguen siendo válidos (valor por defecto). `SCHEMA_VERSION` no se subió entonces; se subió a 0.3.0 en la rev. 8.
- rev. 8 (2026-09-30), acciones y esquema (`SCHEMA_VERSION` 0.2.0 → **0.3.0**, menor: solo aditivos). Cubre además la rev. 7 y `response_failed`, que no habían subido la versión (M0 §9):
  - `InvalidationReason.args_changed` (M3 rev. 3: la propuesta vigente se cancela si el flow repropone con otros args u otra versión de la tool);
  - `action_dispatched`: `args_hash` (sha256 sin clave) sale del evento y entra `args_fp: Fingerprint | None` (HMAC con clave, ADR 0008); `Action.args_hash` sigue en el estado;
  - `action_verified.result` admite `unavailable` (readback que no contestó: no prueba que el efecto falte; la acción no pasa a `failed`);
  - `EVENT_EMITTERS["response_emitted"] = {M2, M8}` (D6);
  - `Agent.slots_model: RefSpec | None = None` (modelo de la 2.ª llamada de Understand; antes era configuración del despliegue) y su sitio de referencia en M1 (`agent_ref_sites`).
- rev. 6 (2026-09-29), registry (unidad 2, ADR 0017 y 0018; spec `../2026-09-29-registry-design.md` §15). Cambios aditivos:
  - `EntityKind.knowledge_snapshot` y entidad `KnowledgeSnapshot` (manifiesto de páginas, `KnowledgePage`); entra en `RegistryEntity` y en `ENTITY_KIND`;
  - `Release.knowledge_snapshot: EntityRef | None = None` (exacta; `None` = sin conocimiento);
  - `SCHEMA_VERSION` 0.1.0 → 0.2.0 (menor: no rompe a los consumidores de 0.1.0) y `contracts/` regenerado;
  - `RegistryPort` no cambia; `eval_suite`, la documentación por versión y los errores de la API del registry viven en `agent_core.registry`, no aquí.
- rev. 9 (2026-09-30), auditoría del gateway y del registry (`SCHEMA_VERSION` 0.3.0 → 0.4.0 → 0.5.0 → 0.6.0 → **0.7.0**). Pone al día la trazabilidad de versiones y aplica §9 (un campo opcional nuevo sube la versión menor):
  - 0.4.0: ADR 0019 (`AgentNodeConfig.save_as`/`output_schema`, `FactSource.kind = "agent"`, evento `agent_step`) y `ToolDef.description`/`args_schema` del gateway, que se agregaron sin subir la versión y quedan cubiertos por esta entrada;
  - 0.5.0: `IdKind.proposal` e `IdKind.eval_run` (registry);
  - 0.6.0: `GenerationResult.usage_known: bool = True` (`false` si el proveedor no informó el uso; M8 marca `cost_known = false`);
  - 0.7.0: `AgentStepPayload.kind` admite `"failed"` y gana `error_kind: GatewayErrorKind | None = None` (el nodo `agent` deja un `agent_step` cuando el gateway falla, para que la auditoría explique el `gave_up`).
- rev. 12 (2026-09-30), `write_draft` (ADR 0019, spec write-draft; `SCHEMA_VERSION` 1.1.0 → **1.2.0**, menor: solo aditivos y relajaciones):
  - `RiskClass.write_draft`: escritura confinada a un borrador del registry, sin `confirm`; `ToolDef.is_write` la cuenta y exige `readback_by`;
  - `WriteToolConfig` gana la forma `draft: true` con `tool` y `args` propios (`action_from` pasa a opcional; se declara uno u otro); `node_kind` trata ambas como `tool_write`;
  - `Action.write_node_id` identifica el nodo `draft` que creó la acción; `confirm_node_id`, `confirmation_token_hash` y `token_exp` pasan a opcionales y solo se omiten (los tres) en una acción con `write_node_id`;
  - `contracts/` regenerado (`Action`, `Flow`, `Node`, `RiskClass`, `RunState`, `ToolDef`, `WriteToolConfig`, `WriteToolNode`).
- rev. 10 (2026-09-30), M12 `read` (`SCHEMA_VERSION` 0.7.0 → **1.0.0**, mayor: M0 §9, agregar un tipo de nodo y reemplazar un campo de `generate`). Cierra el Abierto "dependiente del tema #10":
  - nodo `knowledge` (`KnowledgeConfig`, `KnowledgeNode`, `RESULTS["knowledge"]`; `low_confidence` es solo de `navigate`);
  - `GenerateConfig`: `knowledge_refs` se elimina y entran `knowledge_from: list[SaveAs]` y `purpose: Purpose = "customer_answer"`;
  - `RunState.pages: dict[SaveAs, list[PageView]]`; `SaveAs` pasa a `domain/base.py`;
  - `domain/knowledge.py` (§2.12): `Purpose`, `PageMeta`, `PageView`, `PageRecord`, `KnowledgeView`, `PageRef`/`PageSpec` y sus parsers;
  - evento `knowledge_read` (`KnowledgeReadPayload`, `FilteredPage`; emisor M12);
  - puertos: `KnowledgeSource` definitivo (`capabilities`, `index`, `read`) y `AuthzPort.knowledge_view(principal, purpose) -> KnowledgeView`.
  Los estados guardados con la versión anterior siguen cargando (`pages` tiene valor por defecto); los flows con `knowledge_refs` dejan de validar.
- rev. 11 (2026-09-30), entrada del nodo `agent` (`SCHEMA_VERSION` 1.0.0 → **1.1.0**, menor: un campo opcional nuevo). `AgentNodeConfig.input_view: list[str] = []`: rutas `slots.*` y `facts.*` que el modelo ve en vista `model` (M1 §3.2, m02 §3.7). Sin él, el modelo solo veía el `goal` fijo y no podía atender lo que pidió la persona. Los flows existentes no cambian (vacío = no ve nada).
- rev. 12 (2026-09-30), transferencia entre agentes (ADR 0021; `SCHEMA_VERSION` 1.1.0 → **1.2.0**, menor: campos opcionales, un nodo, un outcome y eventos nuevos). Cambio de interfaz para todos los módulos:
  - `domain/transfer.py` (§2.13): `RoutingCard`, `AcceptedSlot`, `TransferContract`, `DirectoryEntry`, `DirectorySnapshot`, `TransferPacket`, `RunOrigin`, `directory_hash`, `packet_problem`; `domain/eligibility.py`: `transfer_ineligibility`;
  - `Agent.routing: RoutingCard | None = None` y `Agent.accepts: TransferContract | None = None` (sin ficha el agente no está en ningún directorio; sin contrato no recibe transferencias);
  - nodo `transfer` (`TransferConfig`, `TransferPacketSpec`, `TransferNode`, `RESULTS["transfer"] = {rejected}`); **no** entra en `TERMINAL` (decisión del usuario, R2: tiene rama `rejected`; el éxito cierra el run desde M4, no desde el grafo);
  - `DecideConfig.choices_from: str | None = None` (opciones decididas en runtime, ruta a una lista de strings en un hecho);
  - `Outcome.transferred`: solo lo asigna el motor, no es declarable en ningún modo;
  - `RunState.origin: RunOrigin | None = None` y `RunStartedPayload.origin`;
  - eventos `run_transferred`, `transfer_received` y `transfer_rejected` (emisor M4; solo huella del paquete y nombres de slots) y `RunClosedPayload.closed_by = "transfer"`;
  - `TurnResult.agent: EntityRef | None = None` (el agente que respondió) e `IdKind.transfer`.
  Los estados, eventos y agentes guardados con la versión anterior siguen cargando (todo campo nuevo es opcional).
- rev. 13 (2026-10-02), métricas por agente (ADR 0020; `SCHEMA_VERSION` 1.2.0 → **1.3.0**, menor: un campo opcional nuevo). `Agent.metrics: list[MetricDef] = []` y los tipos del DSL (`domain/metrics.py`, `domain/metric_catalog.py`). La rama de métricas lo había numerado 0.5.0, valor que ya usaba `IdKind.proposal`/`IdKind.eval_run` (rev. 9); al integrarla con la rama principal (1.2.0) pasa a 1.3.0. `contracts/` regenerado (`Agent`).
- rev. 14 (2026-10-05), revisión técnica (`SCHEMA_VERSION` 1.3.0 → **1.4.0**, menor: un campo opcional nuevo). `ToolCallContext.at: UtcDatetime | None = None`: el instante del turno (`Clock`) que el ejecutor usa para `ToolDef.max_auth_age` (ADR 0010); lo llena M2 en `tool_call_context`. `ToolDef.accepts(auth, at)` concentra el chequeo previo: nivel y, si la tool declara `max_auth_age`, antigüedad de la autenticación; sin instante, una tool con `max_auth_age` se rechaza (falla cerrado). Antes `max_auth_age` se declaraba pero nadie lo aplicaba. `contracts/` regenerado (`ToolCallContext`). En la misma versión (sin publicar): `ProblemCode.identity_unavailable` (503) y la excepción `GrantCheckUnavailable`, que `IdentityVerifier.grant_active` lanza cuando el servicio de asignaciones no responde (sigue cerrado, pero M9 ya no lo informa como `delegation_expired`; ADR 0010). `contracts/` regenerado (`ProblemCode`). También `ProblemCode.payload_too_large` (413) para el tope del body de M9. Y slots `list` (entrada del copiloto de sugerencias, ADR 0026, alcance decidido por el usuario el 2026-10-05): `SlotType` suma `list`; `AcceptedSlot` gana `items: dict[str, ItemField]` (campos escalares de cada elemento, sin anidar) y `max_items` (obligatorios para `list`, prohibidos en los demás). Sirve en `Agent.input_schema` y en `accepts`. Un slot escalar se serializa igual que antes (las claves nuevas se omiten si son `None`), así que no cambia el hash de ningún agente publicado. Al modelo un slot llega envuelto como texto no confiable (D8), también cada string de una lista. `contracts/` regenerado (`AcceptedSlot`, `ItemField` y los que lo incluyen). El nodo `suggest` y `RunResult.suggestions` siguen fuera.
- implementación de M0 (2026-09-29), decisiones que el spec no cubría:
  - `loads` rechaza claves duplicadas; `to_jsonable` rechaza claves que colisionan tras `str()`; `RecursionError` se convierte en `ValueError`; se rechaza un `Decimal` con |exponente| > 1000;
  - `dumps` escribe `Decimal` con `format(d, "f")` (no `str(d)`, que puede emitir `1E+3`);
  - `canonical_bytes`: un `Decimal` con exponente >= 0 se canoniza como el entero que representa, igual que su round-trip `dumps`→`loads` (el hash no cambia al persistir y recargar); los escalados (`"500.00"`) siguen como string;
  - alias de agente `[a-z][a-z0-9_-]*`; `AgentSelector.parse("x@")` se rechaza; semver sin ceros a la izquierda y solo dígitos ASCII;
  - `Principal.id` y `PrincipalKey.id` no pueden ser vacíos; `AuthLevel` comparado con otro tipo lanza `TypeError`;
  - `RunState` valida al asignar (con rollback) y `model_copy(update=...)` revalida la coherencia;
  - `EnvKeyProvider`: formato `kid:base64` (alfabeto estándar, estricto, >= 32 bytes, la primera es la vigente, material distinto por propósito);
  - dobles: el lease se puede tomar cuando `now == expires_at`; `put_run_idempotency` duplicado gana el primero (provisorio: M9/M4 deben fijar el conflicto antes del adaptador Postgres); id duplicado en el outbox se ignora; `spent_today` usa el día UTC; `resolve_release` devuelve releases revocadas (M4 escala con `release_revoked`);
  - `contracts/`: un esquema por cada modelo o enum exportado por `domain` y `ports` (modo validación, con alias); pendientes: forma string de las refs, `Decimal` de salida y `Node` sin `discriminator` explícito;
  - guardarraíles: todo módulo tiene prohibido importar `adapters`, `cli` y `contracts`; `domain` no importa `ports`; más APIs de tiempo y azar en `banned-api`.

## 1. Propósito y límites

Define el vocabulario compartido del motor: tipos, esquemas de nodos, enums, eventos, errores, utilidades de serialización canónica y los puertos hacia las unidades 2–7. Todo cruce entre módulos pasa por estos tipos.

**Regla de ubicación:** un tipo vive en M0 si lo producen o consumen dos módulos que no pueden importarse entre sí según `.importlinter`, si aparece en un puerto, o si es un dato del registro. Un tipo que solo usa un módulo vive en ese módulo.

**No hace:** lógica de negocio del motor. No contiene ningún concepto bancario (ADR 0006). No sabe de HTTP ni de Postgres. Las únicas funciones con comportamiento son de datos puros: validadores de modelos, `canonical_bytes`, `is_declarable` y el parseo de referencias.

## 2. Interfaz pública

Todos los tipos son modelos Pydantic v2 con `extra="forbid"`. Todos son `frozen=True` salvo `RunState`, que se actualiza con `model_copy(update=…)` (cada módulo solo toca su parte, índice §5). Se exportan a JSON Schema en `contracts/` (ADR 0002).

**Convenciones transversales:**

- **Tiempo:** todo `datetime` es UTC con zona. Un `datetime` sin zona se rechaza al validar (`AwareDatetime`). Se serializa en ISO 8601 con `Z`.
- **Duraciones:** `timedelta`, serializado como duración ISO 8601 (`PT30M`).
- **Cifras:** `Decimal`, nunca `float`. Única excepción: probabilidades (`p_cal`, `p_raw`, puntajes del detector), que son `float` en `[0, 1]`, nunca NaN.
- **IDs:** `str` opacos generados por `IdSource` (§2.9). Ningún módulo genera IDs por su cuenta.
- **Locale:** `Locale = str` con patrón `^[a-z]{2}$` (`es`, `pt`).

### 2.1 Serialización canónica (`domain/json.py`)

```python
JsonValue = None | bool | int | Decimal | str | list["JsonValue"] | dict[str, "JsonValue"]
def loads(raw: str | bytes) -> JsonValue           # json con parse_float=Decimal; rechaza NaN/Infinity
def dumps(value: JsonValue | BaseModel) -> str      # Decimal como número JSON exacto (format(d, "f"), sin notación exponencial), sin pérdida
def canonical_bytes(value: JsonValue | BaseModel) -> bytes   # JCS (RFC 8785)
def sha256_hex(data: bytes) -> str
```

- **Entrada:** todo JSON que entra al núcleo (estado persistido, resultados de tools, salidas de modelos, fixtures) se lee con `loads`. Así un número con decimales es `Decimal` desde el primer momento, y `RunState` hace round-trip sin pérdida.
- **`canonical_bytes`:** JCS sobre el valor, con dos reglas previas:
  - `Decimal` → string normalizado sin notación exponencial (`Decimal("500.00")` → `"500.00"`; se conserva la escala, porque es parte del dato);
  - `int` fuera de ±(2⁵³−1) (rango seguro de I-JSON) → string;
  - `float` solo se admite en campos de probabilidad y se serializa según JCS; NaN o infinito lanza `ValueError`.
- **Quién la usa:** M3 (`args_hash`), M7 (huellas con clave) y M11 (cadena de hash). Nadie más implementa canonización.
- Un modelo Pydantic se canoniza con `model_dump(mode="python")` → `canonical_bytes`.

### 2.2 Referencias (`domain/refs.py`)

```python
class EntityKind(StrEnum): agent, flow, decision_model, policy, template, prompt, tool,
                           language_detection, injection_ruleset, model_profile,
                           knowledge_snapshot                                   # rev. 6
class EntityRef:     id: str; version: str          # exacta "X.Y.Z" (semver 2.0 sin prerelease ni build)
                     @classmethod parse(s: str) -> EntityRef      # "id@1.2.0"; rango o sin versión → InvalidRuntimeRef
                     __str__ -> "id@1.2.0"
class RefSpec:       id: str; spec: str | None      # autoría: "1.2.0", "^1", "~1.2", "1", o None (sin versión)
                     @classmethod parse(s: str) -> RefSpec        # "id", "id@^1", "id@1.2.0"
                     is_exact: bool
                     def require_exact(self) -> EntityRef         # lanza InvalidRuntimeRef si no es exacta
class AgentSelector: id: str; alias: str | None; version: str | None   # "id", "id@prod", "id@1.2.0"; por defecto alias "prod"
```

- Patrón de `id`: `^[a-z0-9][a-z0-9_/-]*$` (admite `t/pedir_cargo`).
- **Autoría frente a runtime:** los nodos y entidades guardan `RefSpec`. En `agent-registry` una referencia puede ser un rango o no llevar versión (plantillas y prompts). Al publicar, la unidad 2 reescribe cada referencia a su versión exacta. Al cargar una release en runtime, `RegistryPort` llama `require_exact()` sobre todas las referencias del contenido; una sola referencia no exacta hace fallar la carga con `InvalidRuntimeRef` (§13.11 de la spec general). El motor nunca resuelve rangos.
- Plantillas y prompts son entidades versionadas como las demás.

### 2.3 Identidad y acceso (`domain/identity.py`)

```python
class PrincipalType(StrEnum): customer, advisor, service, builder
class AuthLevel(StrEnum):     anonymous, session, step_up      # ordenado: anonymous < session < step_up
                              def __ge__/__gt__/...            # comparación por rango, no alfabética
class AuthInfo:       level: AuthLevel; at: AwareDatetime; simulated: bool = False   # simulated: OTP de prueba (ADR 0010)
class PrincipalKey:   type: PrincipalType; id: str | None
class Principal:      type: PrincipalType; id: str | None
                      roles: list[str]; scopes: list[str]; attrs: dict[str, str]
                      auth: AuthInfo; exp: AwareDatetime
                      key -> PrincipalKey
class SubjectRef:     kind: str; ref: str
class OnBehalfOf:     subject: SubjectRef; grant_ref: str; grantee: PrincipalKey
                      scopes: list[str]; exp: AwareDatetime
```

Validadores:

- `auth.level == anonymous` ⇔ `id is None`, y solo se admite con `type == customer`.
- `OnBehalfOf.grantee.type == advisor` y `grantee.id` no vacío.

`Principal` llega ya verificado por M9. El snapshot que se guarda en el run es este mismo modelo: no contiene la credencial cruda ni secretos. **`grantee` (enmienda de ADR 0006):** la delegación se emite a un asesor concreto. M9 exige `on_behalf_of.grantee == principal.key`; si no, `403 delegation_mismatch`.

### 2.4 Entidades del registro (`domain/entities.py`)

```python
class Budgets:        max_nodes_per_turn: int; max_model_calls_per_turn: int; max_tokens_per_run: int
                      max_cost_per_run: Decimal; max_wall_ms_per_turn: int
class EngineTemplates:                                # plantillas que usa el motor fuera de los flows
                      clarify: RefSpec; abstain: RefSpec; handoff: RefSpec
                      pending_ack: RefSpec; pending_offer: RefSpec
                      unsupported_language: RefSpec; input_too_large: RefSpec
class Agent:          id: str; version: str; mode: Literal["conversational", "task"]
                      entry_flow: RefSpec; invocable_by: list[PrincipalType]; min_auth_level: AuthLevel
                      subject_kinds: list[str]; supported_locales: list[Locale]; default_locale: Locale
                      tools_allowed: list[RefSpec]; budgets: Budgets; inactivity_ttl: timedelta = 30 min
                      understand: RefSpec | None; slots_model: RefSpec | None; templates: EngineTemplates
                      max_clarifications: int; on_clarify_exhausted: Literal["end", "escalate"]
                      default_target_queue: str; max_repair_turns_per_run: int = 8
                      routing: RoutingCard | None = None; accepts: TransferContract | None = None   # rev. 12 (ADR 0021)
class Flow:           id: str; version: str; priority: int; nodes: list[Node]     # el primer nodo es la entrada
class EscalateAction: type: Literal["escalate"]; target_queue: str; priority: str
class StartFlowAction: type: Literal["start_flow"]; flow: RefSpec
class Interrupt:      id: str; priority: int; action: EscalateAction | StartFlowAction   # discriminada por type
                      signal_policy: RefSpec | None
class LanguageDetection: id: str; version: str; detector: str                # "lingua@<versión exacta>"
                      candidates: list[Locale]; unsupported: list[str]
                      min_letters: int; min_letters_unsupported: int; thresholds_from: str | None
class InjectionRuleset: id: str; version: str; rules: list[InjectionRule]    # InjectionRule: {id, pattern, kind: regex|phrase}
class Release:        id: str; status: Literal["active", "revoked"]
                      entities: dict[EntityKind, dict[str, str]]           # kind → id → versión exacta
                      interrupts: list[Interrupt]
                      language_detection: EntityRef; injection_ruleset: EntityRef | None
                      knowledge_snapshot: EntityRef | None = None            # rev. 6
                      max_input_chars: int = 4000
class Policy:         id: str; version: str; owner: str; expr: JsonValue; rationale: str
class Template:       id: str; version: str; locales: dict[Locale, str]; reads: frozenset[str]
                      # reads: rutas que lee; el loader de M1 las deriva de {{ }} (M1 §3.3)
class Prompt:         id: str; version: str; locales: dict[Locale, str]; reads: frozenset[str]
                      model_profile: RefSpec                                # rev. 5
class StructuredMode(StrEnum): native, prompted
class ModelPrice:     input_per_mtok: Decimal; output_per_mtok: Decimal; source: str; as_of: date
class ModelProfile:   id: str; version: str; endpoint_alias: str; model: str
                      temperature: Decimal; max_tokens: int; timeout_s: int = 8
                      structured: StructuredMode = native; price: ModelPrice
class RiskClass(StrEnum): read, compute, write_draft, write_reversible, write_irreversible, money_movement
class ToolDef:        id: str; version: str; risk_class: RiskClass
                      min_auth_level: AuthLevel; max_auth_age: timedelta | None
                      idempotent: bool; readback_by: Literal["idempotency_key"] | None   # obligatorio en write_*
                      untrusted_fields: list[str]; source: str | None        # tabla de origen para M7
                      confirmation_ttl: timedelta = 5 min
                      description: str | None = None        # catálogo del nodo `agent` (unidad 5): qué hace la tool, para el modelo
                      args_schema: dict | None = None       # catálogo del nodo `agent`: subconjunto cerrado de JSON Schema (`domain.schema`)
                      is_write -> bool                                      # risk_class ∉ {read, compute}
class ProviderSpec:   provider: Literal["jev", "classifier", "llm_structured", "rule"]; config: dict[str, JsonValue]
class CalibrationRef: method: Literal["none", "isotonic", "platt", "temperature"]; run: str | None
class DecisionModelDef: id: str; version: str; output_schema: dict[str, JsonValue]
                      calibrated_fields: list[str]; input_view: list[str]
                      providers: list[ProviderSpec]; calibration: CalibrationRef; thresholds_from: str | None
class KnowledgePage:  path: str; hash: Sha256Hex                            # rev. 6; el texto vive en el BlobStore del registry
                      audience: Literal["public", "internal", "agent_only"]
                      status: Literal["draft", "approved"]; approved_by: str | None
                      lang: Locale; translation_of: str | None
                      valid_from: date | None; valid_to: date | None; source_refs: list[str]
                      # path: relativa y ASCII (sin "..", "//" ni "/" inicial o final);
                      # aprobada si y solo si tiene approved_by; valid_from <= valid_to; source_refs <= 50 de <= 500 caracteres
class KnowledgeSnapshot: id: str; version: str; pages: list[KnowledgePage]  # <= 10 000 páginas
                      # rutas únicas; translation_of apunta a otra página del mismo snapshot
RegistryEntity = Agent | Flow | Policy | Template | Prompt | ToolDef | DecisionModelDef
                 | LanguageDetection | InjectionRuleset | ModelProfile | KnowledgeSnapshot
```

`description` y `args_schema` (2026-09-30, unidad 5) son opcionales en M0: solo el catálogo que `LLMAgentPort` le muestra al modelo del nodo `agent` los necesita, y M1 (G0-24) los exige para toda tool de `tools_allowed` de un nodo `agent`. `check_output` (validador del subconjunto cerrado de JSON Schema) es ahora de `agent_core.domain` (`schema.py`), no de M2.

Validadores: `default_locale ∈ supported_locales`; `ToolDef` de escritura exige `readback_by`; `Budgets` con valores positivos.

`LanguageDetection` e `InjectionRuleset` son solo datos; la lógica es de M6. El formato de `thresholds_from` (artefacto de calibración) lo define M5.

**`Agent.metrics` (ADR 0020, `SCHEMA_VERSION` 1.3.0).** Lista opcional de `MetricDef` (máximo 32): las métricas que el agente declara para el gate de evaluación y el monitoreo. El motor las ignora en runtime. Los tipos del DSL están en `domain/metrics.py` y el catálogo cerrado de eventos medibles en `domain/metric_catalog.py`. Spec: `docs/specs/2026-09-30-evaluacion-y-metricas-design.md`.

### 2.5 Esquemas de nodos (`domain/nodes.py`)

Un modelo por tipo, con `id`, `type`, `config` y `next: dict[str, str]` (resultado → id de nodo). Los esquemas salen de la tabla de §5 de la spec general.

```python
class DecideConfig:   model: RefSpec; input_view: list[str] | None; branch_on: str; save_as: str
                      # resultados = valores del enum de branch_on + low_confidence, cableados en next
class RuleConfig:     policy: RefSpec | None; expr: JsonValue | None        # exactamente uno
class CollectConfig:  slot: str; prompt_ref: RefSpec; validator: SlotValidator | None = None; max_attempts: int = 2   # None: texto no vacío
class ToolConfig:     tool: RefSpec; args: dict[str, JsonValue]; save_as: str; step_up_max_attempts: int = 2
class WriteToolConfig: action_from: str | None; draft: bool = False; tool: RefSpec | None; args: dict; save_as: str; step_up_max_attempts: int = 2
                       # con `action_from` (confirm) sin `tool` ni `args`; con `draft: true` (ADR 0019) `tool` y `args` propios
class ConfirmConfig:  action: {tool: RefSpec, args: dict[str, JsonValue]}; summary_template: RefSpec
                      reprompt_template: RefSpec | None; max_attempts: int = 2
class VerifyConfig:   readback: RefSpec; by: str; predicate: JsonValue; save_as: str
                      # by: "idempotency_key" | "fact:<ruta>"
class GenerateConfig: prompt_ref: RefSpec; allowed_facts: list[str]; fallback_template_ref: RefSpec
                      knowledge_from: list[SaveAs] = []        # save_as de nodos `knowledge` (M12; antes knowledge_refs)
                      purpose: Purpose = "customer_answer"     # para quién es la respuesta (el más estricto por defecto)
class RespondConfig:  template_ref: RefSpec | None; generate: GenerateConfig | None   # exactamente uno
                      await_: bool = False (alias "await"); claims: list[str] = []
class EscalateConfig: reason_code: ReasonCodeStr; target_queue: str | None; priority_expr: JsonValue | None
class EndConfig:      outcome: Outcome; output_map: dict[str, str] | None
class KnowledgeConfig: mode: Literal["read", "navigate"]; pages: list[str] = []   # read: "ruta" | "ruta#ancla"
                      scope: PagePath | None; selector: RefSpec | None             # navigate
                      purpose: Purpose; save_as: SaveAs
                      # read exige pages y prohíbe scope/selector; navigate al revés (M12)
# Producción (G0-01 los rechaza en el MVP): AgentNodeConfig, SubflowConfig, AwaitApprovalConfig
# ADR 0019 (SCHEMA_VERSION 0.4.0): `agent` se habilitó. AgentNodeConfig gana `save_as` y
# `output_schema: dict[str, JsonValue]`; FactSource.kind gana "agent"; nuevo evento `agent_step` (emisor M2).
# rev. 11 (SCHEMA_VERSION 1.1.0): AgentNodeConfig gana `input_view: list[str] = []` (rutas slots/facts).
# rev. 12 (SCHEMA_VERSION 1.2.0, ADR 0021): DecideConfig gana `choices_from: str | None = None`; nodo `transfer`:
class TransferPacketSpec: reason: str (^[a-z][a-z0-9_]*$); slots: list[SaveAs] = []
class TransferConfig: target_from: str (^decisions\.<save_as>\.choice$); directory_from: SaveAs; packet: TransferPacketSpec
# rev. 12 (SCHEMA_VERSION 1.2.0): RiskClass.write_draft y WriteToolConfig.draft (m01 §3.13).

Node = Annotated[DecideNode | RuleNode | CollectNode | ToolNode | WriteToolNode | ConfirmNode
                 | VerifyNode | RespondNode | EscalateNode | EndNode | KnowledgeNode
                 | AgentNode | SubflowNode | AwaitApprovalNode | TransferNode, Discriminator(node_kind)]
RESULTS: Mapping[str, frozenset[str]]     # por clave de nodo; "tool" y "tool_write" separados
TERMINAL: frozenset[str] = {"escalate", "end"}      # `transfer` no entra (R2): su éxito cierra el run desde M4
WAITING:  frozenset[str] = {"collect", "confirm"}   # más respond con await: true
```

- **Discriminador:** `node_kind(raw)` devuelve `raw["type"]`, salvo que `type == "tool"` y `config` tenga `action_from` o `draft: true`, en cuyo caso devuelve `"tool_write"`. En YAML sigue siendo `type: tool`.
- `SlotValidator`: `{kind: type|regex|enum|decide, value}`.
- `ReasonCodeStr`: §2.7.
- `target_queue` None en `escalate` significa `agent.default_target_queue`.
- **Los esquemas validan forma, no semántica del grafo.** Alcanzabilidad, dominancia, reclamos y demás son reglas G0 de M1.
- **Nodo `knowledge` (ADR 0015, M12, rev. 10):** `RESULTS["knowledge"] = {ok, not_found, denied, low_confidence}`; un `read` cablea solo los tres primeros (M1 G0-03) y `low_confidence` es de `navigate`. No es terminal ni espera.
- **Nodo `transfer` (ADR 0021, rev. 12):** `RESULTS["transfer"] = {rejected}`. No está en `PRODUCTION_NODE_KINDS` ni en `TERMINAL` ni en `WAITING`. M0 solo valida la forma; que el agente tenga `routing`/`accepts`, que `target_from` apunte a un `decide` con `choices_from` y que `rejected` esté cableado son reglas de M1.

### 2.6 Estado del run (`domain/state.py`)

```python
class Slot:           value: JsonValue; status: Literal["claimed", "validated"]; source_turn: int
class FactSource:     kind: Literal["tool", "compute", "identity", "knowledge"]; ref: str; inputs: list[str] = []
class Fact:           fact_id: str; value: JsonValue; source: FactSource; ts: AwareDatetime   # value en vista full
class Decision:       decision_id: str; value: dict[str, JsonValue]; p_cal: dict[str, float | None]
                      provider_used: str; model_version: str
class ActionState(StrEnum): proposed, confirmed, executing, executed, uncertain, denied,
                            verified, failed, cancelled
class InvalidationReason(StrEnum): cancel, abandoned, interrupt, escalated, token_expired, max_attempts, denied_by_user, args_changed
class Action:         action_id: str; confirm_node_id: str | None; write_node_id: str | None; flow: EntityRef
                      # `confirm_node_id`, `confirmation_token_hash` y `token_exp`: los tres, o ninguno y `write_node_id` (ADR 0019)
                      tool: EntityRef; args: dict[str, JsonValue]; args_hash: str
                      state: ActionState; confirmation_token_hash: str; token_exp: AwareDatetime
                      idempotency_key: str; created_at: AwareDatetime
                      cancel_reason: InvalidationReason | None = None
class ActiveFlow:     flow: EntityRef; node_id: str; local_slots: dict[str, JsonValue] = {}
class PendingIntent:  flow: str; priority: int; mention_order: int
class BudgetsUsed:    run_tokens: int = 0; run_cost: Decimal = 0
                      turn_nodes: int = 0; turn_model_calls: int = 0; turn_understand_calls: int = 0   # rev. 7
                      turn_started_at: AwareDatetime | None
class EncryptedBlob:  kid: str; nonce: str; ciphertext: str         # base64; lo produce M7
class RunState:       run_id: str; session_id: str | None; state_version: int
                      release: str; agent: EntityRef
                      principal: Principal; on_behalf_of: OnBehalfOf | None; subject: SubjectRef | None
                      mode: Literal["conversational", "task"]; locale: Locale
                      status: Literal["open", "closed", "escalated"]; outcome: Outcome | None
                      created_at: AwareDatetime; last_activity_at: AwareDatetime; closed_at: AwareDatetime | None
                      inactive_after: AwareDatetime | None      # last_activity_at + agent.inactivity_ttl; lo mantiene M4
                      awaiting: Awaiting = none; awaiting_node_id: str | None
                      active_flow: ActiveFlow | None; pending_intents: list[PendingIntent]; pending_offer: str | None
                      slots: dict[str, Slot]; facts: dict[str, Fact]; decisions: dict[str, Decision]
                      pages: dict[save_as, list[PageView]]       # páginas de conocimiento, vista model; solo M12
                      pages: dict[save_as, list[PageView]]      # páginas de conocimiento (vista model); solo M12
                      actions: list[Action]; token_map: EncryptedBlob | None; open_questions: list[str]
                      budgets_used: BudgetsUsed; turn_count: int; clarifications_used: int
                      node_attempts: dict[str, int]; repair_turns_used: int; degraded_turns: list[int]
                      handoff_ref: str | None; origin: RunOrigin | None = None   # origin: rev. 12, run creado por una transferencia
```

**Estados de acción.** `uncertain` y `denied` son estados explícitos: es el resultado de la escritura (§8.2 paso 5), y M3 los usa en su máquina de estados. `executed` y `uncertain` van a `verify`; `denied` es terminal, sin `verify`.

**Validadores de coherencia de `RunState`:**

- `status == open` ⇒ `outcome is None` y `closed_at is None`.
- `status != open` ⇒ `inactive_after is None`.
- `status == escalated` ⇒ `outcome == escalated` y `handoff_ref` no nulo.
- `status == closed` ⇒ `outcome` no nulo y distinto de `escalated`.
- `awaiting != none` ⇒ `awaiting_node_id` no nulo, salvo la oferta de intención (`awaiting == input` con `pending_offer` no nulo y sin flow activo).
- A lo sumo una acción `proposed` por `confirm_node_id`.
- `mode == task` ⇒ `session_id is None`.

Estos validadores detectan bugs; no reemplazan la lógica de los módulos dueños.

### 2.7 Resultados y enums (`domain/outcomes.py`)

```python
class Outcome(StrEnum):   resolved, abstained, cancelled, clarify_exhausted, completed, failed, abandoned, escalated,
                          transferred                  # rev. 12; solo lo asigna el motor (M4)
DECLARABLE: Mapping[str, frozenset[Outcome]] = {
    "conversational": {resolved, abstained, cancelled, clarify_exhausted},
    "task": {completed, failed}}                          # abandoned, escalated y transferred solo los asigna el motor
def is_declarable(outcome: Outcome, mode: str) -> bool
class ReasonCode(StrEnum): low_confidence, budget_exceeded, tool_failure, customer_request,
                           verification_failed, validation_failed, release_revoked, auth_insufficient
ReasonCodeStr = Annotated[str, ...]   # un ReasonCode o "rule:<id>" | "policy:<id>" | "interrupt:<id>"
class Awaiting(StrEnum):  none, slot, confirmation, step_up, input
class Command(StrEnum):   start_flow, continue_ ("continue"), affirm, deny, clarify, cancel, handoff, out_of_scope, interrupt   # §4.4
Mode = Literal["conversational", "task"]
```

`Awaiting.input` cubre `respond(await: true)` y la oferta de una intención pendiente (M4 §3.3). Es un valor nuevo de la API `/v1` (versión menor: agrega un valor de enum de salida).

### 2.8 Entrada, salida y tipos compartidos (`domain/turn.py`, `domain/shared.py`)

```python
class ConfirmAnswer:  token: str; answer: Literal["yes", "no"]
class TurnInput:      session_id: str; text: str = ""; channel: str; lang: Locale | None
                      client_turn_id: str; confirm: ConfirmAnswer | None
                      # validador: text no vacío, o confirm presente
class RunInput:       agent: AgentSelector; subject: SubjectRef | None; input: dict[str, JsonValue] | None
                      lang: Locale | None; idempotency_key: str
class Message:        kind: Literal["template", "generated"]; text: str; locale: Locale
class ConfirmationPrompt: action_id: str; token: str; expires_at: AwareDatetime; summary: Message
class StepUpPrompt:   required_level: AuthLevel; reason: str
class TurnResult:     run_id: str; turn_id: str; messages: list[Message]; locale: Locale; awaiting: Awaiting
                      confirmation: ConfirmationPrompt | None; step_up: StepUpPrompt | None
                      status; outcome: Outcome | None; handoff_ref: str | None; trace_id: str
class RunResult:      run_id: str; session_id: str | None; release: str; output: dict[str, JsonValue] | None
                      status; outcome: Outcome | None; handoff_ref: str | None
                      first_turn: TurnResult | None; trace_id: str

# Compartidos entre módulos que no pueden importarse
class EscalationRequest: reason_code: ReasonCodeStr; target_queue: str; priority: str   # M2/M4 → M10
class RejectedDraft:  text_model: str; reason: str; failures: list[str]                 # M8 → M11
class Fingerprint:    alg: Literal["HMAC-SHA256"]; kid: str; value: str                  # M7 → M3, M10, M11
class TranscriptEntry: run_id: str; turn_id: str; role: Literal["user", "assistant", "rejected_draft"]
                      text_model: str; reason: str | None
class TranscriptRef:  entry_id: str; fingerprint: Fingerprint
class OutboxMessage:  message_id: str; type: Literal["handoff_created"]; run_id: str
                      payload: dict[str, JsonValue]; created_at: AwareDatetime
```

La API (M9) publica `confirmation.action_summary` como `summary.text`; `ConfirmationPrompt` es la forma interna.

### 2.9 Puertos (`agent_core.ports`)

`typing.Protocol`, síncronos en el MVP (FastAPI los corre en su threadpool). Ningún puerto lee la hora por su cuenta: recibe `now` o usa el `Clock` inyectado.

```python
class Clock:
    def now(self) -> AwareDatetime                                   # siempre UTC
    def monotonic_ns(self) -> int      # solo para medir duraciones (campos de MEASURED_FIELDS); nunca decide nada

class IdSource:
    def new_id(self, kind: IdKind) -> str      # IdKind: run, session, turn, action, decision, fact, call, handoff, event, message, transfer (rev. 12)
    def secret_token(self) -> str              # 128 bits, url-safe; solo para tokens de confirmación

class RegistryPort:
    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release
    def release_status(self, release_id: str) -> Literal["active", "revoked"]
    def get(self, ref: EntityRef, kind: type[T]) -> T              # T ∈ RegistryEntity; referencias no exactas → InvalidRuntimeRef

class ToolCallContext: run_id: str; release: str; principal: Principal; on_behalf_of: OnBehalfOf | None
                       subject: SubjectRef | None; turn_id: str | None
                       at: UtcDatetime | None = None   # instante del turno, para max_auth_age (rev. 14)
class ToolStatus(StrEnum): ok, error, timeout, denied, uncertain, step_up_required   # definido en domain.shared
class ToolResult:      status: ToolStatus; result_full: JsonValue | None; source: str | None
                       call_id: str; error: str | None; required_level: AuthLevel | None
class ToolExecutor:
    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult
    def definition(self, tool: EntityRef) -> ToolDef

class AuthzDecision:   allowed: bool; reason: str | None
class AuthzPort:
    def authorize_agent(self, principal: Principal, agent: Agent, subject: SubjectRef | None) -> AuthzDecision
    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None, subject: SubjectRef | None) -> AuthzDecision
    def bind_params(self, principal: Principal, obo: OnBehalfOf | None, subject: SubjectRef | None) -> dict[str, str]
    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool
    def knowledge_view(self, principal: Principal, purpose: Purpose) -> KnowledgeView    # M12: qué páginas puede leer
    def reportable_attrs(self) -> frozenset[str]

class IdentityVerifier:
    def verify(self, raw_credential: str) -> Principal                 # firma inválida → CredentialsInvalid; NO chequea exp
    def verify_delegation(self, raw: str) -> OnBehalfOf               # ídem
    def grant_active(self, grant_ref: str, now: AwareDatetime) -> bool

class UnitOfWork(Protocol):          # context manager; una instancia = una transacción
    def __enter__(self) -> Self; def __exit__(...) -> None             # sin commit() explícito → rollback
    def acquire_turn(self, run_id: str, turn_id: str, now: AwareDatetime, ttl: timedelta) -> None   # otro lease vigente → TurnInProgress; visible de inmediato
    def release_turn(self, run_id: str, turn_id: str) -> None                                         # se aplica con commit()
    def load_run(self, run_id: str) -> RunState | None
    def find_run_by_session(self, session_id: str) -> RunState | None
    def list_runs_by_session(self, session_id: str) -> list[RunState]   # en orden de creación; find_ prefiere el run abierto
    def save_run(self, state: RunState, expected_version: int) -> RunState   # versión distinta → VersionConflict; devuelve state_version + 1
    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None
    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None
    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None   # (hash del body, resultado)
    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str, result: RunResult) -> None
    def reserve_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str, now: UtcDatetime, ttl: timedelta) -> tuple[str, RunResult] | None   # inmediata; None = reservada; vigente ajena = 409
    def release_run_idempotency(self, principal: PrincipalKey, key: str) -> None
    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None
    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None
    def append_events(self, run_id: str, events: list[EngineEvent]) -> None     # M11 ya los encadenó
    def last_event(self, run_id: str) -> EngineEvent | None                     # para encadenar
    def enqueue_outbox(self, message: OutboxMessage) -> None
    def add_usage(self, principal: PrincipalKey, cost_usd: Decimal, now: AwareDatetime) -> None   # rev. 5: suma costo y 1 hit; lo llama M4
    def list_inactive(self, now: AwareDatetime, limit: int) -> list[str]       # run_ids open con inactive_after < now, por inactive_after
    def commit(self) -> None
UnitOfWorkFactory = Callable[[], UnitOfWork]

class AuditSink:                     # lectura y appends fuera de un turno; la escritura del turno va por la UoW
    def read(self, run_id: str) -> list[EngineEvent]
    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None   # access_denied de M9

class Outbox:                        # lo consume la unidad 4; se escribe por la UoW
    def pending(self, limit: int) -> list[OutboxMessage]
    def mark_delivered(self, message_id: str) -> None

class GenerationResult: output: JsonValue; tokens_in: int; tokens_out: int; cost_usd: Decimal; model: str
                        usage_known: bool = True   # false: el proveedor no informó el uso (M8 marca cost_known = false)
# generate falla con GatewayError (§2.11); detalle en la spec de la unidad 5
class LLMGateway:
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult

class TranscriptStore:
    def append(self, entry: TranscriptEntry) -> str                    # entry_id
    def read(self, run_id: str) -> list[TranscriptEntry]
    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]

class KnowledgeSource:               # M12 (rev. 10); `search` no existe todavía
    def capabilities(self) -> frozenset[str]
    def index(self, snapshot: str, view: KnowledgeView) -> list[PageMeta]          # visibles con `view`, por ruta
    def read(self, path: str, snapshot: str, view: KnowledgeView) -> PageRecord | None   # None: ausente o fuera de la vista

class KeyPurpose(StrEnum): fingerprint, token_map
class KeyProvider:
    def current_kid(self, purpose: KeyPurpose) -> str
    def key(self, purpose: KeyPurpose, kid: str) -> bytes             # kid desconocido → KeyError

class CostCounters:                  # dueño M4 (rev. 5); lee lo que escribe UnitOfWork.add_usage
    def spent_today(self, principal: PrincipalKey, now: AwareDatetime) -> Decimal
    def hits(self, principal: PrincipalKey, window: timedelta, now: AwareDatetime) -> int
```

Notas sobre los puertos:

- **Vistas (decisión de la rev. 2):** `ToolResult` trae solo `result_full` y su `source`. Las vistas `model` y `audit` las calcula M7 dentro del núcleo con el `token_map` del run. La unidad 3 aporta la clasificación (`FieldClassification`) y `ToolDef.untrusted_fields`; nunca ve el vault (ADR 0008: el `token_map` no sale del núcleo).
- **Estados de tool:** lectura y `compute` devuelven `ok|error|timeout|denied|step_up_required`. Escritura devuelve `ok|denied|uncertain|step_up_required`, y `step_up_required` siempre antes de cualquier efecto. Un fake o adaptador que devuelve otro estado viola el contrato.
- **Concurrencia (decisión de la rev. 2):** el `409 turn_in_progress` sale de `acquire_turn`, un lease con TTL que se toma **antes** de cargar el estado y se libera en el commit final del turno. `acquire_turn` es visible para otras transacciones de inmediato (en Postgres, un `UPDATE … WHERE lease vencido` en autocommit); `release_turn` se aplica con el `commit()`. `save_run(expected_version)` sigue en cada commit (los dos de M3 y el del turno) como defensa. Si el proceso se cae, el lease vence solo.
- **Identidad:** `verify` solo valida la firma. La vigencia (`exp`), la coincidencia con el run y el `grantee` los chequea M9 con el `Clock`, porque cada fallo tiene su código y su registro.
- **Claves:** `fingerprint` y `token_map` usan claves distintas; nunca la misma clave para HMAC y cifrado.
- **`DecisionProvider`** no es un puerto de M0: es la interfaz de adaptadores internos de M5.

### 2.10 Eventos (`domain/events.py`)

```python
class EngineEvent:    event_id: str; type: str; run_id: str; turn_id: str | None; session_id: str | None
                      release: str; ts: AwareDatetime; payload: <modelo por tipo>
                      seq: int | None = None; prev_hash: str | None = None; hash: str | None = None   # los asigna M11
AnyEvent = Annotated[RunStarted | TurnStarted | ..., Field(discriminator="type")]
EVENT_EMITTERS: Mapping[str, frozenset[str]]      # tipo → módulos emisores (índice §6); lo usa T-M0-14
MEASURED_FIELDS: Mapping[str, frozenset[str]]     # tipo → campos de medición del payload; el replay los excluye (M11)
class LlmUsage:  calls: int; latency_ms: int; tokens_in: int; tokens_out: int; cost_usd: Decimal
                cost_known: bool; models: list[str]      # cost_known = false si alguna llamada no informó uso
class TurnStages: guards_ms: int | None; understand_ms: int | None; flow_ms: int | None; response_ms: int | None
```

Todo payload está en **vista `audit`**: sin `pii_direct` en claro, sin tokens reversibles. Lo que viene de datos de cliente va como vista `audit` + `Fingerprint`.

| Evento | Payload | Emisor |
|---|---|---|
| `run_started` | `agent, mode, subject_kind, principal_type, locale, reportable_attrs: dict[str,str], origin?: RunOrigin` | M4 |
| `turn_started` | `client_turn_id?, guards?: {lang: {detector, letters, top2, decision, locale_prior, locale}, injection: {flagged, signals, ruleset}, size_ok}` | M4 (con la salida de M6) |
| `command_emitted` | `command, flow?, interrupt?, additional_flows, above_threshold, decision_id, source: understand\|button` | M4 |
| `node_entered` | `flow: EntityRef, node_id, node_type, resume_kind` | M2 |
| `decision_made` | `decision_id, model: EntityRef, provider_used, model_version, fallback_depth, value (audit), p_cal, p_raw, top_k, above_threshold, latency_ms, tokens, cost_usd, locale` | M5 |
| `rule_evaluated` | `node_id, policy: EntityRef?, inputs (audit), result: bool` | M2 |
| `tool_called` | `node_id, tool: EntityRef, call_id, status, args (audit), result (audit)?, result_fp?, error?, attempt, action_id?, latency_ms` | M2 (lectura y `compute`), M3 (escritura) |
| `knowledge_read` | `node_id, purpose, result: ok\|not_found\|denied, refs, filtered_out: [{ref, reason}], missing, reason?: source_unavailable\|navigate_unavailable\|no_snapshot` (solo referencias y motivos, nunca texto de páginas) | M12 |
| `step_up_requested` | `node_id, required_level, attempt` | M2 |
| `action_confirmed` | `action_id, source: understand\|button` | M3 |
| `action_cancelled` | `action_id, reason: InvalidationReason` | M3 |
| `action_dispatched` | `action_id, tool: EntityRef, args_fp: Fingerprint?` | M3 |
| `action_verified` | `action_id, result: verified\|failed\|unavailable, readback_call_id` | M3 |
| `expiry_evaluated` | `now, last_activity_at, ttl, expired: bool` | M4 |
| `response_emitted` | `node_id?, kind, validator: {ok, failures, regenerations}, fallback_used, claims: list[str], transcript_fp: Fingerprint?, llm: LlmUsage?` | M8 desde `generate`; M2 desde `respond(template_ref)` (D6); M4 rellena `transcript_fp` |
| `response_failed` | `node_id?, reason_code: "validation_failed", validator: {ok, failures, regenerations}, claims: list[str], llm: LlmUsage?` | M8 (`respond(generate)` terminó en `EscalationRequest`; sin texto de borradores) |
| `turn_completed` | `client_turn_id?, entry: start_run\|turn, duration_ms, stages: TurnStages, degraded: bool, awaiting: Awaiting` | M4 |
| `injection_flagged` | `signals, ruleset, scope: user_text\|untrusted_field` | M6 (M4 lo agrega) |
| `access_denied` | `reason: principal_expired\|delegation_expired\|delegation_mismatch\|principal_mismatch\|subject_forbidden\|agent_forbidden\|tool_denied, tool?` | M9, M2 (`tool_denied`) |
| `escalated` | `reason_code, target_queue, priority, handoff_ref` | M10 |
| `handoff_resolved` | `handoff_ref, resolution_code, handoff_quality, reader_type` | M10 |
| `run_transferred` | `transfer_id, to_agent: EntityRef, to_release_id, to_run_id, reason, packet_fp: Fingerprint, directory, directory_hash, candidates: list[str]` (cadena origen; sin valores de slots) | M4 |
| `transfer_received` | `transfer_id, accepted_slots: list[str], packet_fp: Fingerprint` (cadena destino) | M4 |
| `transfer_rejected` | `transfer_id, to_agent?, reason_code: not_in_directory\|no_active_release\|not_eligible\|accepts_mismatch\|transfer_limit\|no_turn, directory?, directory_hash?` (cadena origen) | M4 |
| `run_closed` | `outcome, closed_by: flow\|abandonment\|escalation\|revocation\|transfer` | **solo M4** |

Evento saliente (outbox, no va a la cadena): `handoff_created {handoff_ref, run_id, target_queue, priority, reason_code, language, reportable_attrs}`, en un `OutboxMessage`.

**Reglas:**

- Los eventos son inmutables. Única excepción: M4 rellena `response_emitted.payload.transcript_fp` con `model_copy` después de `record_turn` y antes de pasarlos a M11. El orden del turno se mantiene.
- **Campos de medición** (`MEASURED_FIELDS`): `decision_made.latency_ms`, `tool_called.latency_ms`, `response_emitted.llm`, `response_failed.llm`, `turn_completed.duration_ms` y `turn_completed.stages`.
  - Se miden con `Clock.monotonic_ns()` (o los reporta el proveedor) y no son deterministas.
  - Ninguna decisión del motor (transición, regla, vencimiento, reintento) puede depender de ellos: solo se registran.
  - `llm` es `None` si la respuesta no llamó al gateway (plantilla o modo degradado); `calls` cuenta generación + regeneraciones.
  - Una duración negativa o no medible se registra como `0`.
- `seq`, `prev_hash` y `hash` los asigna solo M11 al encadenar. Para replay, dos eventos se comparan con `event_id`, `seq`, `prev_hash`, `hash`, `ts` y los campos de `MEASURED_FIELDS` excluidos.
- Un `access_denied` sin run existente (por ejemplo, credenciales vencidas en `POST /v1/runs`) no tiene cadena: va solo al log de seguridad de M9.
- Un campo opcional nuevo o un tipo de evento nuevo es versión menor de `contracts/`.

### 2.11 Errores (`domain/errors.py`)

```python
class DomainError(Exception)                          # errores internos; no son HTTP
class InvalidRuntimeRef(DomainError)                  # referencia no exacta en runtime
class SchemaError(DomainError)                        # dato del registro que no valida
class IllegalTransition(DomainError)                  # bug de máquina de estados (M3)
class VersionConflict(DomainError)                    # save_run con versión vieja
class TurnInProgress(DomainError)                     # acquire_turn con lease vigente
class CredentialsInvalid(DomainError)                 # IdentityVerifier: firma inválida
class GatewayErrorKind(StrEnum): timeout, unavailable, rate_limited, invalid_output, refused
class GatewayError(DomainError)                       # LLMGateway: kind + uso parcial (tokens_in, tokens_out, cost_usd, model: opcionales)

class ProblemCode(StrEnum):
    credentials_invalid, principal_expired,                                           # 401
    subject_forbidden, agent_forbidden, version_pin_forbidden, delegation_expired,
    delegation_mismatch, principal_mismatch,                                          # 403
    not_found,                                                                        # 404
    turn_in_progress, handoff_already_resolved, idempotency_conflict,                 # 409
    idempotency_in_progress,                                                          # 409
    run_closed,                                                                       # 410
    invalid_request,                                                                  # 422
    rate_limited, cost_budget_exceeded,                                               # 429
    internal_error                                                                    # 500
PROBLEM_STATUS: Mapping[ProblemCode, int]
class EngineError(Exception): code: ProblemCode; detail: str
```

- M9 traduce `EngineError` a `application/problem+json` y convierte `TurnInProgress` en `409` y `CredentialsInvalid` en `401`.
- Cualquier otro `DomainError` que llegue a M9 es un `500 internal_error` con `trace_id`, sin detalle interno.
- `idempotency_conflict`: la misma `Idempotency-Key` y el mismo principal con otro body.
- `idempotency_in_progress`: la misma `Idempotency-Key` y el mismo principal mientras otra petición con esa clave sigue en curso (reserva vigente sin resultado). Es reintentable; el cliente distingue por `code`, no por el texto del detalle.
- `agent_forbidden`: falla `authorize_agent` (tipo de principal, `subject_kind` o nivel de autenticación).

### 2.12 Conocimiento (`domain/knowledge.py`)

Tipos de M12 que cruzan fronteras (M1 valida el nodo, M8 valida las citas, M12 lee y filtra; entre ellos no se pueden importar). Ver `m12-conocimiento.md` §2.

```python
Audience = Literal["public", "internal", "agent_only"]; PageStatus = Literal["draft", "approved"]
Purpose = Literal["customer_answer", "advisor_view", "agent_guidance"]
class PageMeta:    path; anchor; snapshot; type; audience; status; approved_by; lang; translation_of
                   valid_from; valid_to; source_refs      # aprobada ⇔ tiene approved_by; valid_from ≤ valid_to
class PageView:    ref: str; meta: PageMeta; content_model: str     # ref = "ruta@snapshot#ancla", coincide con meta
                   source -> FactSource{kind: knowledge, ref}       # propiedad derivada
class PageRecord:  meta: PageMeta; content: str                     # vista full; `content` fuera de repr y de la serialización
class KnowledgeView: audiences: frozenset[Audience]; approved_only: bool
class PageRef:     path; snapshot; anchor | None                    # "ruta@snapshot#ancla"
class PageSpec:    path; anchor | None                              # "ruta#ancla" (autoría)
def page_ref(path, snapshot, anchor=None) -> str
def parse_page_ref(text) -> PageRef | None       # None si no es una cita a una página (un fact_id nunca lo es)
def parse_page_spec(text) -> PageSpec            # lanza ValueError
def check_page_path(path) -> str                 # rechaza `..`, `//` y `/` final
```

- La ruta solo admite `[A-Za-z0-9_./-]` y no admite `@`; el primer `@` de una cita separa ruta y snapshot, y el snapshot (`id@versión`) puede llevar `@`.
- `KnowledgePage` (manifiesto del snapshot, §2.4) reutiliza `PagePath`, `Audience` y `PageStatus`.

### 2.13 Transferencia entre agentes (`domain/transfer.py`, `domain/eligibility.py`)

Tipos del ADR 0021 (spec `2026-09-30-transferencia-entre-agentes-design.md` §3). Todos son datos: M0 no consulta puertos.

```python
SlotType = Literal["string", "integer", "decimal", "date", "boolean"]
class RoutingCard:        directory: EntityId; summary: str (1..500); examples: list[str] = []   # qué resuelve; lo ve el agente de recepción
class AcceptedSlot:       type: SlotType; required: bool = False
class TransferContract:   slots: dict[str, AcceptedSlot] = {}                                    # contrato de entrada del especialista
class DirectoryEntry:     agent_id: EntityId; release_id: str; summary: str; examples: list[str]
                          accepts: TransferContract; supported_locales: list[Locale]
class DirectorySnapshot:  directory: EntityId; hash: Sha256Hex; entries: list[DirectoryEntry]
                          # entries: filtradas para el principal; hash: del directorio completo
                          choices -> list[str]                                                   # los agent_id de las entradas
class TransferPacket:     reason: str; trigger: str; slots: dict[str, JsonValue] = {}            # trigger: texto del usuario en vista `model`
class RunOrigin:          kind: Literal["transfer"]; transfer_id: str; from_run_id: str; from_agent: EntityRef
                          from_release_id: str; from_event_hash: Sha256Hex; depth: PositiveInt
def directory_hash(pairs: Iterable[tuple[str, str]]) -> str          # sha256 de los pares (agent_id, release_id) ordenados
def packet_problem(contract, slots) -> str | None                    # missing_required_slot | slot_not_accepted | slot_type_mismatch
def transfer_ineligibility(agent: Agent, principal: Principal, subject: SubjectRef | None, locale: Locale) -> str | None
```

- `directory_hash` cambia al publicar, promover o revocar: es la huella de lo que el modelo pudo ver.
- `RunOrigin.from_event_hash` es el hash del último evento de la cadena origen al cerrar el turno de la transferencia (`turn_completed`); cubre `run_transferred` y `run_closed` por encadenamiento (plan P2). `depth` cuenta las transferencias de la sesión (plan P5).
- `packet_problem` devuelve un código sin valores; nunca incluye datos del paquete. `decimal` admite `Decimal` y `int`, `date` un string ISO `YYYY-MM-DD`, y `bool` no cuenta como `integer` ni `decimal`.
- `transfer_ineligibility` es pura y devuelve el primer motivo que falla, en este orden: `mode` (solo conversacionales), `no_contract`, `principal_type`, `subject_kind`, `locale`, `auth_level`; `None` si es elegible. Nunca incluye datos del principal.

## 3. Comportamiento

- `EntityRef.parse` y `RefSpec.require_exact` rechazan rangos y referencias sin versión con `InvalidRuntimeRef`.
- `is_declarable(outcome, mode)` es la única fuente de outcomes declarables; la usan M1 (G0-14) y M2.
- `AuthLevel` se compara por rango.
- `canonical_bytes` es determinista: el mismo valor con claves en otro orden produce los mismos bytes.
- `FakeClock.advance(timedelta)` avanza `now()` y `monotonic_ns()` por igual; `FakeIds` es secuencial por `kind` (`action-0001`…) y se puede sembrar con valores grabados para el replay.
- **Lint de tiempo y aleatoriedad:** `ruff` con `flake8-tidy-imports.banned-api` prohíbe `datetime.datetime.now`, `datetime.datetime.utcnow`, `datetime.date.today`, `time.time`, `time.monotonic`, `time.monotonic_ns`, `time.perf_counter`, `uuid.uuid1`, `uuid.uuid4`, `random` y `secrets` en todo el repo. Las únicas excepciones (`per-file-ignores`) son `agent_core/adapters/system_clock.py` y `agent_core/adapters/system_ids.py`.
- **Contratos:**
  - `uv run agentcore contracts` escribe `contracts/schemas/<Tipo>.json` para los tipos públicos, los eventos y los esquemas de nodos, más `contracts/VERSION` (semver);
  - `uv run agentcore contracts --check` falla si difieren de lo commiteado; corre en CI;
  - `contracts/openapi.json` lo agrega M9 al mismo comando;
  - `agent_core.domain.SCHEMA_VERSION` es la fuente; el comando escribe `contracts/VERSION` con ese valor y `--check` compara.

## 4. Invariantes

- Ningún tipo de M0 nombra conceptos de negocio (cliente bancario, tarjeta, disputa). `customer` y `advisor` son tipos de principal (ADR 0006), no conceptos bancarios.
- Todo evento y todo `RunState` se serializan con `canonical_bytes` sin error: sin `float` NaN, fechas en ISO 8601 UTC y `Decimal` como string normalizado.
- `RunState` hace round-trip sin pérdida: `RunState.model_validate(loads(dumps(s))) == s`, incluidos los `Decimal` dentro de hechos y argumentos.
- `domain` y `ports` no importan ningún otro módulo de `agent_core` (`.importlinter`).
- Ningún código fuera de los dos adaptadores de sistema obtiene la hora ni aleatoriedad por su cuenta.

## 5. Fallas

M0 no maneja fallas: define `DomainError`, `EngineError`, `ProblemCode` y `PROBLEM_STATUS`. Un dato que no valida contra un modelo de M0 lanza `pydantic.ValidationError`. El registro la convierte en `SchemaError` y M9 en `422 invalid_request`.

## 6. Eventos que emite

Ninguno. Define el esquema de todos (§2.10).

## 7. Pruebas

| ID | Qué prueba | §13 |
|---|---|---|
| T-M0-01 | `EntityRef.parse("tool@^1")`, `"tool@1"` y `"tool"` lanzan `InvalidRuntimeRef`; `"tool@1.2.0"` pasa; `RefSpec.parse("tool@^1").require_exact()` lanza | 11 (rangos) |
| T-M0-02 | `is_declarable` rechaza `abandoned`/`escalated` en ambos modos y los outcomes de un modo en el otro | 1 |
| T-M0-03 | Round-trip de `RunState` con todas las partes pobladas, incluidos `Decimal` en hechos, argumentos y presupuestos | — |
| T-M0-04 | Todo tipo de evento se serializa con `canonical_bytes` y su `sha256` es estable ante reordenar claves | 6 |
| T-M0-05 | `agentcore contracts --check` pasa sobre lo commiteado y falla si se modifica un tipo sin regenerar | — |
| T-M0-06 | Lint: `ruff` rechaza `datetime.now()`, `uuid4()` y `random` fuera de los adaptadores de sistema (fixture con un archivo que los usa) | — |
| T-M0-07 | `canonical_bytes`: `Decimal("500.00")` → `"500.00"`; claves ordenadas; NaN lanza; un `int` fuera de ±(2⁵³−1) va como string | 6 |
| T-M0-08 | `Principal`: anónimo con `id` o `advisor` anónimo no validan; `AuthLevel.session >= AuthLevel.anonymous`; `OnBehalfOf` exige `grantee` asesor | 5 |
| T-M0-09 | `RunState`: cada regla de coherencia de §2.6 rechaza su caso inválido | — |
| T-M0-10 | Un `datetime` sin zona se rechaza en cualquier modelo | — |
| T-M0-11 | `RefSpec.parse` acepta `id`, `id@^1`, `id@~1.2`, `id@1.2.0` y `t/pedir_cargo`; rechaza ids con mayúsculas o espacios | — |
| T-M0-12 | `Node`: `type: tool` con `action_from` valida como `WriteToolNode` y sin él como `ToolNode`; `respond` con `template_ref` y `generate` a la vez no valida; `RESULTS` cubre los 10 tipos del MVP | — |
| T-M0-13 | `ReasonCodeStr` acepta los códigos y los prefijos `rule:`, `policy:` e `interrupt:`; rechaza otros | — |
| T-M0-14 | Cada tipo de `AnyEvent` tiene entrada en `EVENT_EMITTERS` y coincide con la tabla §6 del índice | — |
| T-M0-15 | Cada entrada de `MEASURED_FIELDS` nombra un tipo de `AnyEvent` y campos que existen en su payload | — |
| T-M0-16 | (rev. 12) `directory_hash` no depende del orden y cambia con la release; `packet_problem` y `transfer_ineligibility` devuelven códigos sin datos (`tests/m00/test_transfer_types.py`) | — |
| T-M0-17 | (rev. 12) El nodo `transfer` valida, `RESULTS["transfer"] == {rejected}` y no está en `TERMINAL`; `Outcome.transferred` no es declarable; `RunOrigin.depth >= 1`; los tres eventos nuevos y `closed_by="transfer"` validan y los emite M4 (`tests/m00/test_transfer_nodes_events.py`) | — |
| T-M0-C-* | Suites de contrato de cada puerto, parametrizadas por implementación; en fase 1 corren contra los dobles de §10 | — |

Suites de contrato de la fase 1 (lo mínimo que cada una verifica):

- `Clock`: siempre con zona UTC; `FakeClock.advance` es monotónico; `monotonic_ns()` nunca decrece.
- `IdSource`: IDs únicos por `kind`; `FakeIds` reproduce la misma secuencia con la misma semilla; `secret_token` tiene ≥ 128 bits.
- `RegistryPort`: `get` con referencia no exacta → `InvalidRuntimeRef`; release revocada → `release_status` da `revoked`.
- `ToolExecutor`: una escritura solo devuelve `ok|denied|uncertain|step_up_required`; la misma `idempotency_key` devuelve el mismo recurso; `step_up_required` no produce efecto.
- `UnitOfWork`: sin `commit` no persiste nada (salvo el lease de `acquire_turn`, visible de inmediato para otra UoW); `save_run` con versión vieja → `VersionConflict`; un segundo `acquire_turn` con lease vigente → `TurnInProgress`, y vencido lo toma; eventos y outbox se commitean con el estado o no se commitea ninguno; las fallas inyectables `after_commit` y `on_commit` funcionan.
- `AuditSink`/`Outbox`: lo commiteado por la UoW se lee en orden; `mark_delivered` saca el mensaje de `pending`.
- `KeyProvider`: los propósitos dan claves distintas; un `kid` anterior sigue disponible después de rotar.

## 8. Evaluación

No tiene métricas propias. Los esquemas de eventos son la entrada de la unidad 6.

## 9. Puntos de iteración

- Agregar un campo opcional a un tipo o evento, o un valor a un enum de salida: versión menor de `contracts/`.
- Agregar un tipo de evento: versión menor; M11 y la unidad 4 lo aceptan sin cambios.
- Cambiar un campo existente, un puerto o un esquema de nodo, o agregar un tipo de nodo: versión mayor; se anuncia a las unidades dueñas.
- Un adaptador real (Postgres, unidad 3, JEV…) se agrega sin tocar M0: implementa el Protocol y pasa su suite de contrato.

## 10. Definición de terminado

- Tipos, enums, esquemas de nodos, eventos, errores, `canonical_bytes` y puertos con docstrings, exportados desde `agent_core.domain` y `agent_core.ports`.
- `SystemClock` y `SystemIds` en `agent_core/adapters/`.
- **Dobles de la fase 1** en `testing/fakes/`, con su suite de contrato en verde:
  - `FakeClock`;
  - `FakeIds`;
  - `InMemoryRegistry` (se carga con objetos Python; la carga desde YAML la agrega M1);
  - `FakeToolExecutor` (guionable);
  - `InMemoryUoW` (con fallas inyectables);
  - `InMemoryAuditSink`;
  - `InMemoryOutbox`;
  - `FakeKeyProvider`; y `EnvKeyProvider` en `agent_core/adapters/` (etiquetado como secreto de demo).
- Los demás dobles los entrega el primer módulo que los usa (índice §4, columna "Lo entrega").
- `contracts/` generado, `contracts/VERSION = 0.2.0` y `--check` en CI.
- T-M0-01…15 y T-M0-C-* en verde; `lint-imports`, `mypy --strict agent_core/domain agent_core/ports` y `ruff` en verde.

## 11. Abiertos

- Ninguno bloqueante para la fase 1.
- **Agentes internos (ADR 0019):** `AgentNodeConfig.save_as`/`output_schema`, `FactSource.kind = "agent"` y el evento `agent_step` están implementados (SCHEMA_VERSION 0.4.0, `contracts/` regenerado; hoy 0.7.0). `RiskClass.write_draft` se implementó en la rev. 12 (`SCHEMA_VERSION` 1.2.0). Falta decidir si `Agent.default_target_queue` pasa a ser opcional para agentes que nunca escalan.
- ~~**Dependiente del tema #10:** el nodo `knowledge`, `RunState.pages`, `PageView` y la forma final de `KnowledgeSource`~~ **Resuelto 2026-09-30 (rev. 10, `SCHEMA_VERSION` 1.0.0):** entraron con M12 `read`.
- ~~**Formato de la credencial** (`raw_credential`)~~ **Resuelto 2026-09-29 (M9 §3.8):** JWS compacto Ed25519 con `kid`; no cambia el puerto.
