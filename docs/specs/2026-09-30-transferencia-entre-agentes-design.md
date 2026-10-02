# Spec — Transferencia entre agentes: recepción, directorio y especialistas

- Estado: **borrador para revisión; implementado en las ramas `feat/transferencia-entre-agentes` (fases 1 a 6), `feat/transferencia-demo` (fase 7) y `feat/transferencia-otel` (spans OTel de la transferencia, §8, 2026-10-02); pendiente de aprobación del usuario.** Reconciliado con la implementación el 2026-10-01 (decisiones P1 a P7 del plan y las del libro de la ejecución); lo que sigue sin decidir está en §12
- Fecha: 2026-09-30 (rev. 2026-10-01)
- Repo: `agent-core`
- ADR: 0021 (este diseño); se apoya en 0004, 0006, 0013, 0017, 0018, 0019 y 0020
- Specs relacionados: m00, m01, m02, m04, m05, m09, m11, registry (`2026-09-29-registry-design.md`), evaluación (`2026-09-30-evaluacion-y-metricas-design.md`, rama `feat/eval-metrics`)
- Autor: Juan Zapata, con Claude
- Idioma: el texto va en español; **todo identificador de código (tipos, campos, eventos, nodos, enums) va en inglés**

## 1. Propósito y criterio de éxito

El cliente conversa con una sola puerta de entrada. Un **agente de recepción** conversa hasta entender qué necesita, busca en un **directorio** al especialista que lo resuelve y le **transfiere** la conversación. Cada especialista es un agente con su propia release, versionado y evaluación. Publicar un especialista no toca la release de recepción.

**Criterio de éxito (demo):** en una sesión, el cliente escribe su problema a recepción. Recepción lo transfiere a `disputas` y, **en el mismo turno**, `disputas` responde. Después:
- el linaje de la sesión muestra los dos runs con su release exacta, el `transfer_id` y el hash del directorio;
- las dos cadenas de eventos quedan enlazadas por hash.

**Fuera de alcance de la demo (diseñado aquí):** volver a recepción desde un especialista, tope de transferencias por sesión, especialistas que exigen `step_up` y enrutamiento por un nodo `agent`.

## 2. Decisiones

| # | Decisión |
|---|---|
| D1 | Una sesión encadena runs; solo uno abierto a la vez. Cada run conserva su release fija. |
| D2 | Recepción es un agente normal (`conversational`, `invocable_by: [customer]`) sin lógica de negocio. |
| D3 | Directorio por etiqueta: agentes con release activa en `prod` y `routing.directory` igual a la etiqueta. Tiene hash de versión. |
| D4 | Descubrir = tool de lectura `directory/list`, filtrada por el servidor. Elegir = `decide` con opciones de runtime. Transferir = nodo `transfer`. |
| D5 | Destino por `agent_id@prod`, resuelto al transferir. |
| D6 | Contrato de entrada `Agent.accepts`, validado al publicar recepción, al transferir y al publicar el especialista. |
| D7 | Paquete mínimo: `reason`, mensaje disparador (`untrusted_text`) y slots aceptados. Los hechos de tools no viajan. |
| D8 | Se conservan principal, subject y nivel de autenticación; el destino debe aceptarlos. |
| D9 | `transfer_id` + enlace por hash entre cadenas + spans enlazados + linaje por sesión. |
| D10 | Evaluación por agente: en la suite de recepción el escenario termina en `transfer` (no abre el destino); escenarios de especialista desde un paquete; la ficha de enrutamiento entra en la vara del especialista. |
| D11 | Demo: solo la ida. |

## 3. Modelo (M0)

Todo cambio es aditivo salvo donde se indica. `SCHEMA_VERSION` pasó a **1.2.0** (una sola vez para toda la transferencia, ver §12.7) y `contracts/` está regenerado.

### 3.1 Agente

```python
class RoutingCard(Model):
    directory: EntityId                     # etiqueta del directorio, p. ej. "customer-care"
    summary: str = Field(max_length=500)    # qué resuelve, en lenguaje claro
    examples: list[str] = Field(max_length=20)  # mensajes típicos, sintéticos


class AcceptedSlot(Model):
    type: Literal["string", "integer", "decimal", "date", "boolean"]
    required: bool = False


class TransferContract(Model):
    slots: dict[str, AcceptedSlot] = Field(default_factory=dict)


class Agent(Model):                         # campos nuevos
    routing: RoutingCard | None = None      # sin ficha, no aparece en ningún directorio
    accepts: TransferContract | None = None # sin contrato, no recibe transferencias
```

### 3.2 Nodo `transfer` y elección dinámica

```python
class TransferPacketSpec(Model):
    reason: str                             # literal del flow, p. ej. "routed"
    slots: list[str] = Field(default_factory=list)   # nombres de slots de este run


class TransferConfig(Model):
    target_from: str                        # ruta "decisions.<save_as>.choice"
    directory_from: SaveAs                  # el hecho que guardó `directory/list`
    packet: TransferPacketSpec


class TransferNode(_NodeBase):
    type: Literal["transfer"]
    config: TransferConfig
```

- **Resultados:** solo `rejected`. `RESULTS["transfer"] = {"rejected"}`. **`transfer` no está en `TERMINAL`** (decisión del usuario): el manejador de M2 solo arma el `TransferRequest`, devuelve `Stop.terminal` y **deja el puntero en el nodo `transfer`, sin seguir ninguna arista**. M2 no puede saber si la transferencia sale bien (el directorio, la release y la elegibilidad son de M4). M4 valida: si es válida, cierra el origen y abre el destino (§5.2); si se rechaza, **M4 mueve el puntero por `next["rejected"]`** y sigue avanzando en el mismo turno.
- **`DecideConfig` gana `choices_from: str | None`**: ruta `facts.<save_as>.value.<campo>` a una **lista de strings** (la tool devuelve `choices`). Con él, `branch_on` es `choice` y el modelo elige entre opciones de runtime en lugar de un `enum` fijo. El motor **deduplica** las opciones conservando el orden y **agrega `"none"`**; una opción real llamada `none`, o una lista vacía, da `none`/`low_confidence` sin llamar al modelo. Los resultados del nodo son `chosen`, `none` y `low_confidence`, y la decisión guarda `choice`. Un `choice` fuera de la lista es `low_confidence`. Sin `choices_from`, el `decide` no cambia.
- **Umbral (P3):** el umbral de `choice` se busca por valor y, si falta, por la etiqueta comodín `"*"`. Sin comodín, cada especialista nuevo exigiría recalibrar antes de recibir conversaciones. Hoy **nada genera `"*"` sin conexión** (`calibrate` no lo emite): hasta que la calibración o un artefacto hecho a mano lo aporte, las elecciones de runtime quedan siempre bajo el umbral (`low_confidence`, el valor seguro). Ver §12.1.
- **Campos del catálogo:** `choices`, `agent_id` y `release_id` del directorio son ids de opciones, no PII. Todo catálogo de campos de producción debe **clasificarlos como `public`**; si no, `decide_choice` recibe las opciones tokenizadas y no puede elegir. `summary` y `examples` de las fichas son texto de autoría del registro: se recomienda clasificarlos `untrusted_text`, para que el modelo de enrutamiento los vea envueltos y no tokenizados (así lo hace el catálogo de la demo, §12.14).

### 3.3 Outcome, cierre y sesión

- `Outcome.transferred` (nuevo). Lo asigna el motor; no es declarable en un `end`.
- `RunClosedPayload.closed_by` gana `"transfer"`.
- `RunState` gana `origin: RunOrigin | None`:

```python
class RunOrigin(Model):
    kind: Literal["transfer"]
    transfer_id: str
    from_run_id: str
    from_agent: EntityRef
    from_release_id: str
    from_event_hash: Sha256Hex              # hash del `turn_completed` del turno que transfirió (cadena de origen)
    depth: PositiveInt                      # transferencias acumuladas en la sesión (1 = la primera)
```

`from_event_hash` es el hash del **último evento de la cadena de origen en el turno de la transferencia, el `turn_completed`** (P2), no el del `run_transferred`: ese hash cubre `run_transferred` y `run_closed` por encadenamiento y está disponible sin cambiar `EventChain`. `depth` cuenta las transferencias para `TurnConfig.max_transfers_per_session` (P5).

- `IdKind.transfer` (nuevo).

### 3.4 Eventos

| Evento | Emisor | Payload |
|---|---|---|
| `run_transferred` | M4 (en el run origen) | `transfer_id`, `to_agent`, `to_release_id`, `to_run_id`, `reason`, `packet_fp`, `directory`, `directory_hash`, `candidates: list[str]` |
| `transfer_received` | M4 (en el run destino) | `transfer_id`, `accepted_slots: list[str]` (solo nombres), `packet_fp` |
| `transfer_rejected` | M4 (en el run origen) | `transfer_id`, `to_agent` (solo si es un id bien formado y está en el snapshot), `reason_code` (`not_in_directory`, `not_eligible`, `accepts_mismatch`, `no_active_release`, `transfer_limit`, `no_turn`), `directory` y `directory_hash` opcionales |
| `run_started` (ampliado) | M4 | gana `origin: RunOrigin \| None`; con `origin = None` el campo **no se serializa**, para que las cadenas selladas antes de 1.2.0 sigan verificando |
| `run_closed` (ampliado) | M4 | `outcome: transferred`, `closed_by: transfer` |

**No hay evento `directory_read` (P1).** Leer el directorio es un `tool_called` normal; un evento aparte necesitaría un emisor nuevo en M2. El directorio leído (`directory`, `directory_hash`, `candidates`) viaja dentro de `run_transferred` y `transfer_rejected`. Los tres eventos nuevos tienen emisor M4 (índice §6). En la cadena del destino los eventos previos al turno (`run_started`, `transfer_received`) van **antes de `turn_started`** y conservan su propio `turn_id`.

`packet_fp` es una huella con clave (HMAC, ADR 0008) del paquete en vista `audit`: prueba qué viajó sin guardar el contenido.

## 4. Directorio (registry)

- **Lectura:** puerto `AgentDirectory.members(directory)` (M0) sobre el registry (`RegistryDirectory(store, registry, releases)`); la tool arma `DirectorySnapshot = {directory, hash, entries: [DirectoryEntry]}`, con `DirectoryEntry = {agent_id, release_id, summary, examples, accepts, supported_locales}`. Los miembros se ordenan por `agent_id` (punto de código) para que el hash y el orden no dependan del almacén.
- **Composición:** releases activas apuntadas por `prod` cuyo agente tiene `routing.directory == name`.
- **Hash:** sha256 de `canonical_bytes` de las parejas `(agent_id, release_id)` ordenadas. Cambia al publicar, promover o revocar un agente del directorio.
- **Elegibilidad** (la filtra el servidor con el principal de la credencial, nunca el modelo):
  - `principal.type ∈ invocable_by`;
  - `subject.kind ∈ subject_kinds`;
  - el locale del run está en `supported_locales`;
  - `principal.auth >= min_auth_level` (en la demo, los que exigen más se excluyen; ver Abiertos);
  - el agente tiene `accepts`;
  - `AuthzPort.authorize_agent` autoriza al principal sobre el agente.
- **Tool `directory/list@1.0.0`** (clase `read`, `args: {directory, locale}`): `locale` es un argumento (el run lo pasa) y se valida; uno mal formado da `error` (`bad_args`). La sirve `DirectoryToolExecutor` en `agent_core/composition/directory.py`, que envuelve al `ToolExecutor` real y delega las demás tools. **El dueño queda cerrado: vive en `composition`** (registry §18, fila 11). Devuelve la vista filtrada más `choices` (lista de `agent_id`). El nodo `tool` emite un `tool_called` normal (P1).
- **Cableado (fase 7, cerrado):**
  - `EngineDeps.directory` es opcional; con él, `build_engine` envuelve `deps.tools` con `DirectoryToolExecutor` (usa el `authz` y los `ids` de `deps`); sin él, nada cambia.
  - `agentcore serve` construye `RegistryDirectory(store, PostgresRegistry, PostgresRegistry.release)` y lo pasa por `ServePorts.directory`.
  - `DirectoryToolExecutor` y `DIRECTORY_TOOL` se exportan desde `agent_core.composition`; `tests/m04/harness.py` ya no importa el módulo interno.
  - La evaluación del registry (`EngineScenarioHarness`) aún no tiene directorio (fase 8).
- **Frescura:** `PostgresRegistry.release_status` tiene una caché con TTL; una release revocada puede seguir en el directorio hasta un TTL (registry §7.1).
- **Caché:** el motor lee el directorio por run; no hay caché entre runs en la demo.

## 5. Comportamiento en runtime

### 5.1 Flow típico de recepción

```
entender (collect / agent con input_view) → directorio (tool directory/list) → elegir (decide choices_from)
  chosen → avisar (respond "te comunico con…", sin await) → transferir (transfer)
  none | low_confidence → aclarar (respond await) → entender
  transferir.rejected → escalar a una persona
```

### 5.2 Transferencia (M4, dentro del mismo turno y la misma transacción)

1. M2 llega a `transfer` y devuelve un `TransferRequest` (destino, paquete) con `Stop.terminal`, **sin mover el puntero** (§3.2). Si el nodo se alcanza durante `start_run` no hay texto del cliente con que continuar: se rechaza con `no_turn` (P6).
2. **Validación**, en este orden. La primera que falle emite `transfer_rejected` y **M4 sigue por `next["rejected"]` en el mismo turno**:
   - `no_turn` (solo en `start_run`);
   - el destino está en el hecho `directory_from` de este run (`not_in_directory`); el modelo no puede inventar un destino;
   - el alias `prod` del destino resuelve a una release activa que fija a ese agente (`no_active_release`);
   - la elegibilidad de §4 se repite y `AuthzPort.authorize_subject` y `authorize_agent` autorizan al principal y su subject sobre el agente destino (`not_eligible`);
   - el paquete cumple el `accepts` **de la release resuelta ahora**, no el del snapshot: los slots requeridos existen, tienen el tipo declarado y no hay slots fuera del contrato (`accepts_mismatch`);
   - el tope de transferencias de la sesión no se superó (`transfer_limit`; `max_transfers_per_session = 1`, contado por `RunOrigin.depth`, P5). Es la última comprobación.
3. **Cierre del origen:** emite `run_transferred` y `run_closed {outcome: transferred, closed_by: transfer}`. El `packet_fp` es el HMAC de M7 (ADR 0008) sobre el paquete; calcularlo proyecta el paquete y deja sus tokens en el almacén del run origen (igual que el nodo `agent`).
4. **Apertura del destino:** crea el run con la misma `session_id`, el mismo principal y subject, la release resuelta y `origin` (con `from_event_hash` = hash del `turn_completed` del origen, P2). Emite `run_started` y `transfer_received`.
5. **Siembra:** `Slot` no tiene un campo `source`. Los slots del paquete entran en el run destino como `Slot(status="validated", source_turn=1)`; la procedencia queda en `RunState.origin` y en `transfer_received.accepted_slots`. Solo viajan los slots del paquete; el destino no hereda hechos ni decisiones. El mensaje disparador entra como el texto del turno.
6. **Continuación:** el destino **debe declarar `understand`** (AG-03, P4) y procesa el mismo texto con sus guardas, Understand y flow, con **el mismo `turn_id`**. Tiene su propio almacén de tokens (no hereda los del origen). Los mensajes del turno son los del origen y luego los del destino; su respuesta cierra el turno. El cliente no hace nada.
7. **Atomicidad:** la transferencia ocurre en **una sola unidad de trabajo** (un `commit`) y bajo el lease del turno; si el `commit` cae, no queda ningún evento ni resultado y el reintento con el mismo `client_turn_id` la completa. **Alcance:** cubre runs, cadenas, uso y resultados del turno. Los transcripts (`TurnRecorder`) y las escrituras de M3 quedan **fuera** de esa unidad (m04 §11, Abierto). El reintento de un turno que transfirió devuelve el mismo resultado (se guarda bajo el run destino y se busca entre los runs de la sesión). `client_turn_id` debe ser único por sesión.

### 5.3 Sesión (M4, M9)

- `UnitOfWork.find_run_by_session` devuelve el run **abierto** de la sesión. Hay un run abierto como máximo: **lo impone la base** con el índice único parcial `runs_one_open_per_session` (`schema.sql`), y el doble en memoria hace la misma comprobación. La UoW aplica primero las escrituras que cierran (el índice no se puede diferir) y una violación es `VersionConflict("la sesión ya tiene un run abierto …")`. La ruta de Postgres está **sin verificar** (fase 7, sin docker). Ver §12.8 y m04 §3.8 punto 5.
- El `410 run_closed` solo aparece cuando el último run de la sesión se cerró por algo distinto de `transfer`.
- `principal_mismatch` se evalúa contra el principal de la sesión, que no cambia al transferir.
- `TurnResult` gana `run_id` y `agent` (el run y el agente que respondieron), para que la app los muestre en la consola.
- **API nueva:** `GET /v1/sessions/{session_id}/lineage` (lectura sujeta a `authorize_read`). Devuelve los runs en orden con su release, su `origin` y el outcome de cada uno, y exige `authorize_read` por run. Solo el dueño de la sesión la lee (mismo control que `post_turn`; un asesor con delegación recibe `403 principal_mismatch`). **No incluye el hash del directorio**: no está en `RunState` ni en `RunOrigin` (vive en el evento `run_transferred`). Ver §12.9.

## 6. Validación estática (M1)

| Regla | Qué comprueba |
|---|---|
| G0-01 | `transfer` entra al catálogo; `target_from` es una ruta `decisions.*.choice`; `directory_from` es un `save_as` |
| G0-03 | `transfer` cablea solo `rejected`; `decide` con `choices_from` cablea `chosen`, `none` y `low_confidence` |
| G0-26 | `target_from` lee un `decide` con `choices_from` que domina al `transfer`; `directory_from` es el `save_as` de un nodo `tool` con `directory/list` que también lo domina |
| G0-27 | los `packet.slots` son slots de un `collect` del flow |
| G0-22 | **sin cambios en esta entrega** y sin prueba de transferencia. La garantía real contra un destino inventado es G0-26/G0-27 más la comprobación de M4 (el destino debe estar en `snapshot.choices`). Pendiente de endurecer: un `decide` con `choices_from` puede leer un hecho de un nodo `agent` |
| AG-03 | un agente con `transfer` es `conversational`; uno con `accepts` necesita `routing`, `understand` y ser `conversational` |
| REL-T1 (**no construida**, P7) | al publicar recepción: para cada destino posible del directorio en `prod`, los slots del paquete cumplen su `accepts` (con los tipos del `collect` de origen) |

## 7. Evaluación (integración con el ADR 0020) — no construida (P7)

- **Recepción:** en evaluación, el escenario termina en `transfer` y no abre el run destino. Las aserciones de escenario ganan `transferred_to: <agent_id>` y `packet_slots`. Métrica `gate` típica: precisión de enrutamiento.
- **Especialistas:** `ScriptedSource` gana `transfer_packet` opcional; el escenario arranca como un run transferido (con `origin` sintético).
- **Vara del especialista:**
  - Su `eval_suite` gana `routing_scenarios` (mensajes que deben llegarle).
  - Al publicar con ficha nueva o cambiada, el gate corre recepción `prod` sobre los `routing_scenarios` de **todos** los especialistas publicados del directorio, más los suyos, con el **directorio candidato**.
  - Falla si los suyos no le llegan o si empeora el enrutamiento de otro (dentro del ruido).
- **Compatibilidad de `accepts`:** si el `accepts` nuevo rompe REL-T1 para una recepción publicada, la propuesta se marca `yardstick_loosened` (aprobación humana aparte).
- **Catálogo del DSL:** suma `engine.run_transferred`, `engine.transfer_received` y `engine.transfer_rejected` (no hay `engine.directory_read`, P1), y la ventana `session`.
- **Métricas sugeridas:** precisión de enrutamiento, transferencias por sesión, tasa de `transfer_rejected` y resolución por sesión.

## 8. Observabilidad

- **Correlación:** `session_id` en todo evento (ya existe en `EngineEvent`) y `transfer_id` en los tres eventos de transferencia.
- **Enlace verificable:** `RunOrigin.from_event_hash` ata la cadena del destino al `turn_completed` exacto del origen. `verify_transfer_link(target, sink)` (M11, `agent_core/audit/links.py`) lo comprueba: la cadena de origen verifica, el hash es de un `turn_completed` del turno que transfirió, hay un único `run_transferred` que apunta al destino, y los agentes y releases de `origin`, de los `run_started` y de `run_transferred` coinciden. Comprueba solo el enlace, no la integridad de la cadena del destino. **Pendiente:** conectarlo a `agentcore replay` (§12.10).
- **OTel (hecho el 2026-10-02; ADR 0003 #4, m04 §3.9):**
  - El span `agentcore.transfer` es hijo del `invoke_agent` del turno del origen y cubre la validación de la transferencia (§5.2).
  - Atributos, todos ids o enums (lista cerrada de `agent_telemetry`): `agentcore.transfer.id`, `agentcore.transfer.from_agent`, `agentcore.transfer.to_agent`, `agentcore.transfer.to_release_id` y `agentcore.transfer.outcome` (`transferred` | `rejected`). `from_agent` y `to_agent` son ids de agente, sin versión (la versión del destino la da `to_release_id`). Además de los de correlación (`run_id`, `turn_id`, `session_id`, release y agente del origen).
  - En un rechazo va `agentcore.transfer.reason_code` y no hay `to_release_id`; `to_agent` va solo si `transfer_rejected` lo repite, es decir, si el destino está en el directorio que el run leyó.
  - El `invoke_agent` del run destino es **hermano** del del origen (mismo padre y misma traza) y lleva un *span link* al span `agentcore.transfer`. Es hermano solo en el árbol de la traza: en el tiempo el destino corre **dentro** del `invoke_agent` del origen, que sigue abierto mientras se procesa el destino (la duración de la recepción sí incluye el trabajo del especialista). Si el origen no tiene un span padre (hay provider pero no hay span de request), el destino cuelga del `invoke_agent` del origen para quedar en la misma traza. Una transferencia dentro del destino cuelga de su propio `invoke_agent`. Un rechazo no abre turno de destino y no enlaza nada.
  - **No se persiste nada** (ni evento, ni campo de `RunOrigin`; sin cambio de M0 ni de `SCHEMA_VERSION`): el enlace de la traza vive solo en el exportador, y el enlace verificable es el de la cadena (arriba). La telemetría no cambia eventos ni hashes (las dos cadenas son idénticas con o sin ella) y en el replay es no-op. Si la telemetría falla, M4 lo contiene y el turno sigue sin ella.
  - **Límites:** la telemetría es de mejor esfuerzo. Si el turno se revierte después (falla del `commit`), los spans ya emitidos se exportan igual. Sin endpoint OTLP (`OTEL_EXPORTER_OTLP_ENDPOINT` o `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) no hay trazas.
- **Linaje por sesión:** §5.3. **Pendiente:** el linaje por run del registry (`RunLineage`, `lineage_for_run`) no tiene `origin` y no lo lee; hoy `origin` solo se ve en el linaje por sesión (§5.3).
- **Sin PII:** el paquete solo aparece como `packet_fp`; los slots viajan por las vistas de M7.

## 9. Fallas

| Falla | Comportamiento |
|---|---|
| El directorio no responde | La tool da `error` y el flow sigue por esa rama (aclarar o escalar) |
| El destino no está en el directorio leído | `transfer_rejected(not_in_directory)` y rama `rejected` |
| El destino ya no tiene `prod` activo | `transfer_rejected(no_active_release)` |
| Transferencia pedida en `start_run` | `transfer_rejected(no_turn)` |
| El principal no es elegible | `transfer_rejected(not_eligible)` |
| El paquete no cumple `accepts` | `transfer_rejected(accepts_mismatch)` |
| Se superó el tope de la sesión | `transfer_rejected(transfer_limit)` |
| Falla de la base a mitad de la transferencia | Se revierte la unidad del turno (runs, cadenas, uso y resultados); reintento con `client_turn_id`. No cubre transcripts ni escrituras de M3 (§5.2.7) |
| El run destino escala en su primer turno | Normal: la sesión termina con el handoff del especialista; el linaje muestra la cadena |

## 10. Pruebas

| Id | Caso |
|---|---|
| T-TR-01 | Transferencia de punta a punta: el especialista responde en el mismo turno y la sesión tiene dos runs |
| T-TR-02 | El `run_started` del destino lleva `origin` con el hash de `run_transferred`; alterar un evento del origen rompe la verificación |
| T-TR-03 | Un destino fuera del directorio leído se rechaza (`not_in_directory`) aunque exista en el registry |
| T-TR-04 | Un cliente no elegible no ve al especialista en el directorio y no puede ser transferido a él |
| T-TR-05 | Un paquete que no cumple `accepts` se rechaza y el run sigue por `rejected` |
| T-TR-06 | Publicar un especialista lo agrega al directorio sin publicar recepción; el hash del directorio cambia |
| T-TR-07 | Atomicidad: una falla inyectada a mitad no deja un run cerrado sin destino ni un destino sin origen |
| T-TR-08 | `principal_mismatch` se mantiene en toda la sesión |
| T-TR-09 | Replay de una sesión con transferencia reproduce los dos runs y verifica el enlace (el replay existe en modo `fixture`: el enlace se verifica sobre las cadenas grabadas, m11 decisión 24; conectar `verify_transfer_link` sobre el almacén de auditoría a la salida de `agentcore replay` queda pendiente) |
| T-TR-10 | `GET /v1/sessions/{id}/lineage` devuelve la cadena con releases y `transfer_id`; **el hash del directorio no está** (§12.9) |
| T-TR-11 | M1: G0-26, G0-27 y AG-03 (`tests/m01/test_transfer_rules.py`). G0-22 no tiene prueba de transferencia (ver §6) |
| T-TR-12 | Evaluación: en la suite de recepción el escenario termina en `transfer` y no abre el destino |
| T-TR-13 | Gate del especialista: una ficha que roba escenarios de otro especialista hace fallar el gate |
| T-TR-14 | Un `accepts` que rompe a la recepción publicada se marca `yardstick_loosened` |
| T-TR-15 | Ningún evento de transferencia lleva valores de slots ni datos de vista `full` |
| T-TR-16 | Spans OTel de la transferencia (§8; `tests/m04/test_telemetry_port.py` y `tests/composition/test_transfer_spans.py`): el span `agentcore.transfer` lleva los atributos `agentcore.transfer.*` de una transferencia válida (con `to_release_id`) y de un rechazo (con `reason_code`, sin `to_release_id`, y `to_agent` solo si el evento lo repite); el `invoke_agent` del destino es hermano del del origen y tiene exactamente un link al span de la transferencia; un rechazo no enlaza nada; solo ids y enums; las cadenas de los dos runs son idénticas con y sin telemetría (también con una que falla al abrir el span, en `link` o en `finish`) |

## 11. Fases de construcción (demo: solo la ida)

| Fase | Contenido | Estado |
|---|---|---|
| 1 | M0: tipos, nodo, outcome, eventos, `RunOrigin`, `IdKind.transfer`; `contracts/` | hecha |
| 2 | M1: reglas de §6 (sin REL-T1) | hecha |
| 3 | Registry: directorio, hash y tool `directory/list` (adaptador en `composition`) | hecha; cableada en la raíz de composición en la fase 7 (§4) |
| 4 | M5 y M2: `decide` con `choices_from` y manejador de `transfer` | hecha |
| 5 | M4 y M9: sesión con varios runs, transferencia atómica, `TurnResult.run_id`/`agent`, linaje por sesión | hecha |
| 6 | M11: enlace por hash (`verify_transfer_link`) | hecha; el replay de sesión llegó en la fase 7 y los spans OTel de §8 el 2026-10-02 |
| 7 | Demo: recepción más dos especialistas (`disputas`, a partir de `atencion`, y `consultas`); las suites de evaluación pasan a la fase 8 | hecha (plan `docs/superpowers/plans/2026-10-01-transferencia-fase7-demo.md`): registro `tests/fixtures/registry-transfer-demo` (tres agentes, una release cada uno), umbral `"*"` hecho a mano (`calibrations/cal-transfer-demo.json`, no es una calibración), demo en proceso y por HTTP (`tests/composition/test_transfer_demo.py`), fixture grabado `tests/fixtures/runs-transfer/transferencia.yaml`; sin suites de evaluación (decisión del usuario) |
| 8 | Evaluación (§7) y REL-T1, en coordinación con la rama `feat/eval-metrics` | pendiente, con su propio plan |

Hay una prueba de punta a punta sin hechos sembrados (`tests/m04/test_transfer_e2e.py`): `collect`, `directory/list`, `decide` con `choices_from` y `transfer`; el especialista continúa en el destino. Los pendientes de las fases 6 a 8 y los de esta spec están en `TEMAS-ABIERTOS-PENDIENTES.md` #19.

## 12. Abiertos

1. **Proveedores de `choices_from`: cerrado solo en la parte del umbral.** P3 resuelve cómo se busca el umbral con opciones variables (valor o comodín `"*"`). **Sigue abierto:** proveedor (JEV `choice` o `llm_structured`) y cómo se genera `"*"`: nada lo genera sin conexión, así que hoy las elecciones de runtime son siempre `low_confidence` hasta que la calibración o un artefacto hecho a mano lo aporte. La demo necesita decidirlo.
2. **Especialistas que exigen `step_up`:** ¿se listan con una marca y la transferencia dispara `step_up` antes de crear el destino, o se excluyen (demo)? **Abierto**; hoy se excluyen.
3. **Vuelta a recepción:** `Agent.on_out_of_scope: transfer(<agent>)` con el texto pendiente; requiere el tope por sesión. **Abierto.**
4. **Tope y presupuesto por sesión:** `max_transfers_per_session = 1` es un valor de la demo (P5). **Abierto:** el valor definitivo y si hay un presupuesto de costo por sesión, además del de cada run.
5. ~~**Dueño de la tool `directory/list`.**~~ **Cerrado:** `composition` (registry §18, fila 11), implementado en `agent_core/composition/directory.py`.
6. **Ejemplos de las fichas en el prompt:** límite de tamaño total del directorio que ve el modelo (número de especialistas por etiqueta). **Abierto.**
7. **`SCHEMA_VERSION`:** este cambio llevó la versión a **1.2.0**, una sola vez; no se vuelve a subir por esta spec. (T1, ya integrado, era 1.1.0; `write_draft` y `Agent.metrics` suben versión cuando se integren.)

Abiertos nuevos de la implementación (no resueltos aquí):

8. ~~**Una restricción en la base para "a lo sumo un run abierto por sesión".**~~ **Cerrado (fase 7):** índice único parcial `runs_one_open_per_session ON runs (session_id) WHERE status = 'open' AND session_id IS NOT NULL` en `adapters/sql/schema.sql`. `PostgresUoW._apply` escribe primero los runs que no quedan abiertos y traduce el `UniqueViolation` del índice (por `diag.constraint_name`) a `VersionConflict` con mensaje propio; el doble en memoria lo comprueba sobre el estado final del commit. **Postgres sin verificar** (sin docker): ver m04 §3.8 punto 5. Riesgo de despliegue: una base existente con dos runs abiertos en una sesión hace fallar la creación del índice; la consulta previa está en el comentario de `schema.sql` (no hay migraciones automáticas).
9. **Hash del directorio en el linaje de la sesión** (§5.3, T-TR-10). Opciones: (a) leer `run_transferred` de la cadena del origen con un puerto de lectura nuevo en `ApiDeps`; (b) `directory_hash` opcional en `RunOrigin` (cambio de M0: regenerar `contracts/`); (c) retirarlo de la spec y dejarlo solo en el replay. Registrado en m09 §11.
10. **Replay de sesión: cerrado en parte (fase 7).** El replay `fixture` reproduce una sesión que transfiere (m11, decisión 24): `RecordedIds` reparte `to_run_id` y `transfer_id` grabados; el fixture lleva las cadenas de los runs destino (`linked`); antes de correr el motor, cada una pasa `check_chain` y su enlace con el origen se comprueba sobre lo grabado con el núcleo puro de `verify_transfer_link` (`from_event_hash`, `transfer_id`, agentes y releases; un fallo da `chain_broken` y nombra el run enlazado); los puertos grabados y la comparación van sobre `events + linked`; `run_started.origin.from_event_hash` no se compara después del motor (es un hash de cadena que el replay no reproduce), porque ya se comprobó antes. **Siguen pendientes:** (a) conectar `verify_transfer_link` sobre el almacén de auditoría a `agentcore replay`; (b) el replay por `run_id` (modo `audit`) no sigue la sesión. El runner con varias releases y el fixture grabado de la demo están hechos (fase 7, m11 T-M11-13). `ReplayReport` y la salida `--json` de la CLI ganaron `chain_broken_run` y `chain_broken_reason`.
11. **Alcance de la atomicidad y escrituras de M3 en el turno de una transferencia** (§5.2.7, m04 §11): ¿puede el destino escribir en ese turno?, ¿qué pasa con una escritura del origen seguida de una caída antes del `commit`?
12. **`client_turn_id` único por sesión:** el reintento busca el resultado entre los runs de la sesión; hoy se documenta como requisito, no se comprueba.
13. **Rutas de Postgres sin verificar** (no hubo docker): `aliases_named` del registry, los métodos de sesión de la unidad de trabajo (`find_run_by_session`, `list_runs_by_session`) y el índice `runs_one_open_per_session` con su traducción a `VersionConflict` (fase 7). Hay que correr `docker compose up -d postgres && uv run pytest tests/integration`.
14. **Catálogos de campos de producción:** deben clasificar como `public` los campos del directorio (`choices`, `agent_id`, `release_id`); si no, `decide_choice` recibe las opciones tokenizadas (§3.2). El catálogo de la demo (`testing/engine_world.CATALOG`) clasifica `directory.choices`, `directory.entries.agent_id` y `directory.entries.release_id` como `public`, y `directory.entries.summary` y `directory.entries.examples` como `untrusted_text` (texto de autoría del registro: llega envuelto al modelo de enrutamiento, no tokenizado). Los catálogos de producción deben hacer lo mismo: sigue siendo trabajo de quien despliega, porque `DEFAULT_CATALOG` de M7 no cambia (decisión del usuario, fase 7). **Hueco conocido:** `accepts` y `supported_locales` de las fichas no tienen regla en ningún catálogo, así que un router real los vería tokenizados (`⟦pii:n⟧`). No se arregla con una línea de catálogo (los nombres de slot son dinámicos y la búsqueda es por último segmento). Opciones: proyectarlos fuera de la entrada del router en el flow, o trabajo de catálogo de producción. Con el proveedor guionado de la demo no se nota.
15. **Pendientes de las fases 6 a 8:** REL-T1 y evaluación (§7) (los agentes de la demo están hechos, fase 7; los spans OTel de la transferencia también, §8); ver `TEMAS-ABIERTOS-PENDIENTES.md` #19.
16. **Cableado de `directory/list` en la raíz de composición:** **Cerrado (fase 7)** (§4).
17. **Autorización de la tool y de M4 no coinciden del todo:** `DirectoryToolExecutor` filtra con `authorize_agent` solamente, mientras M4 llama también `authorize_subject`; y `locale` es un argumento del flow (la prueba de punta a punta fija `"es"`). Un especialista listado puede entonces rechazarse con `not_eligible` (falla seguro).
18. **Endurecer G0-22/G0-26:** `decide.choices_from` puede leer un hecho de un nodo `agent` (§6).
19. **Linaje por run del registry sin `origin`** (§8).
20. **Limitaciones de la demo (fase 7):** (a) los flows de los especialistas (`disputa-cargo`, `consulta-pqr`) no leen el slot `problema` transferido: vuelven a preguntar al usuario; (b) el bucle `aclarar` de recepción no tiene tope propio (lo acota solo el número de turnos del usuario; cada vuelta es un turno, así que G0-04 y G0-06 se cumplen); (c) `agentcore serve` en modo demo no transfiere (no hay proveedor de la elección, Abierto 1).
21. **`VersionConflict` del índice de run abierto llega al cliente como `500 internal_error`:** ni M4 ni M9 lo capturan (m04 §3.8 punto 5, m09 §3.5). Falta decidir si se mapea a 409 o se reintenta.
22. **Decisiones del usuario sobre la fase 7 (plan, preguntas 1 a 5):** Q1 sí, el fixture de M11 se extiende con `linked` y el replay deja de comparar `run_started.origin.from_event_hash`; Q2 (consecuencia de Q1) `check_fixture` exime los sha256 hex exactos; Q3 `directory.choices`, `agent_id` y `release_id` son `public` y `summary`/`examples` `untrusted_text`, solo en el catálogo de la demo; Q4 se acepta que `serve` en modo demo no transfiera; Q5 no hay suites de evaluación en esta fase.
