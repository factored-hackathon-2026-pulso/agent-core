# Spec — Transferencia entre agentes: recepción, directorio y especialistas

- Estado: **borrador para revisión**
- Fecha: 2026-09-30
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
| D10 | Evaluación por agente: `transfer` terminal en la suite de recepción; escenarios de especialista desde un paquete; la ficha de enrutamiento entra en la vara del especialista. |
| D11 | Demo: solo la ida. |

## 3. Modelo (M0)

Todo cambio es aditivo salvo donde se indica. Sube `SCHEMA_VERSION` (versión menor) y se regenera `contracts/`.

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

- **Resultados:** solo `rejected`. Si la transferencia sale bien, el nodo es **terminal** para el run, como `escalate`. `RESULTS["transfer"] = {"rejected"}` y `TERMINAL` incluye `transfer`.
- **`DecideConfig` gana `choices_from: str | None`** (ruta a una lista en un hecho). Con él, el modelo de decisión elige entre opciones de runtime en lugar de un `enum` fijo. Los resultados del nodo pasan a ser `chosen`, `none` y `low_confidence`, y la decisión guarda `choice`. Sin `choices_from`, el `decide` no cambia.

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
    from_event_hash: Sha256Hex              # hash del `run_transferred` de origen
```

- `IdKind.transfer` (nuevo).

### 3.4 Eventos

| Evento | Emisor | Payload |
|---|---|---|
| `directory_read` | M2 (al guardar el hecho de `directory/list`) | `directory`, `directory_hash`, `candidates: [{agent_id, release_id}]`, `filtered_out: int` |
| `run_transferred` | M4 | `transfer_id`, `to_agent`, `to_release_id`, `to_run_id`, `reason`, `packet_fp` |
| `transfer_received` | M4 (en el run destino) | `transfer_id`, `accepted_slots: list[str]`, `packet_fp` |
| `transfer_rejected` | M4 (en el run origen) | `transfer_id`, `to_agent`, `reason_code` (`not_in_directory`, `not_eligible`, `accepts_mismatch`, `no_active_release`, `transfer_limit`) |
| `run_started` (ampliado) | M4 | gana `origin: RunOrigin \| None` |
| `run_closed` (ampliado) | M4 | `outcome: transferred`, `closed_by: transfer` |

`packet_fp` es una huella con clave (HMAC, ADR 0008) del paquete en vista `audit`: prueba qué viajó sin guardar el contenido.

## 4. Directorio (registry)

- **Lectura:** `RegistryService.directory(name, principal) -> Directory`. `Directory = {name, hash, entries: [DirectoryEntry]}`, con `DirectoryEntry = {agent_id, release_id, summary, examples, accepts, supported_locales}`.
- **Composición:** releases activas apuntadas por `prod` cuyo agente tiene `routing.directory == name`.
- **Hash:** sha256 de `canonical_bytes` de las parejas `(agent_id, release_id)` ordenadas. Cambia al publicar, promover o revocar un agente del directorio.
- **Elegibilidad** (la filtra el servidor con el principal de la credencial, nunca el modelo):
  - `principal.type ∈ invocable_by`;
  - `subject.kind ∈ subject_kinds`;
  - el locale del run está en `supported_locales`;
  - `principal.auth >= min_auth_level` (en la demo, los que exigen más se excluyen; ver Abiertos);
  - el agente tiene `accepts`.
- **Tool `directory/list@1`** (clase `read`, `args: {directory}`): la sirve un adaptador de `ToolExecutor` sobre el servicio. Devuelve la vista filtrada. El nodo `tool` emite además `directory_read`.
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

1. M2 llega a `transfer` y devuelve un `TransferRequest` (destino, paquete) como `StepOutcome` terminal, igual que una `EscalationRequest`.
2. **Validación**, en este orden. La primera que falle emite `transfer_rejected` y el run sigue por `rejected`:
   - el destino está en el hecho `directory_from` de este run (`not_in_directory`); el modelo no puede inventar un destino;
   - el alias `prod` del destino resuelve a una release activa (`no_active_release`);
   - `AuthzPort.authorize_subject` autoriza al principal y su subject sobre el agente destino, y se repite la elegibilidad de §4 (`not_eligible`);
   - el paquete cumple el `accepts` **de la release resuelta ahora**: los slots requeridos existen, tienen el tipo declarado y no hay slots fuera del contrato (`accepts_mismatch`);
   - el tope de transferencias de la sesión no se superó (`transfer_limit`; en la demo, 1).
3. **Cierre del origen:** emite `run_transferred` y `run_closed {outcome: transferred, closed_by: transfer}`.
4. **Apertura del destino:** crea el run con la misma `session_id`, el mismo principal y subject, la release resuelta y `origin` (con `from_event_hash` = hash de `run_transferred`). Emite `run_started` y `transfer_received`.
5. **Siembra:** los slots del paquete entran en el run destino como slots `validated` con `source: transfer`. El mensaje disparador entra como el texto del turno.
6. **Continuación:** el run destino procesa ese turno completo (guardas, Understand, flow) y su respuesta es la respuesta del turno. El cliente no hace nada.
7. **Atomicidad:** todo va en la misma unidad de trabajo y bajo el lease del turno. Si algo falla, se revierte todo y el turno se reintenta con su `client_turn_id`.

### 5.3 Sesión (M4, M9)

- `UnitOfWork.find_run_by_session` devuelve el run **abierto** de la sesión. Hay un run abierto como máximo (restricción en la base de datos).
- El `410 run_closed` solo aparece cuando el último run de la sesión se cerró por algo distinto de `transfer`.
- `principal_mismatch` se evalúa contra el principal de la sesión, que no cambia al transferir.
- `TurnResult` gana `run_id` y `agent` (el run y el agente que respondieron), para que la app los muestre en la consola.
- **API nueva:** `GET /v1/sessions/{session_id}/lineage` (lectura sujeta a `authorize_read`). Devuelve los runs en orden con su release, sus transferencias, el hash del directorio y el outcome de cada uno.

## 6. Validación estática (M1)

| Regla | Qué comprueba |
|---|---|
| G0-01 | `transfer` entra al catálogo; `target_from` es una ruta `decisions.*.choice`; `directory_from` es un `save_as` |
| G0-03 | `transfer` cablea solo `rejected` |
| G0-26 | `target_from` lee un `decide` con `choices_from` que domina al `transfer`; `directory_from` es el `save_as` de un nodo `tool` con `directory/list` que también lo domina |
| G0-27 | los `packet.slots` son slots de un `collect` del flow |
| G0-22 | la salida de un `agent` no puede ser `target_from` ni entrar en `packet.slots` sin pasar por un `collect` |
| AG-03 | un agente con `transfer` es `conversational`; uno con `accepts` tiene `routing` |
| REL-T1 | al publicar recepción: para cada destino posible del directorio en `prod`, los slots del paquete cumplen su `accepts` (con los tipos del `collect` de origen) |

## 7. Evaluación (integración con el ADR 0020)

- **Recepción:** en evaluación, `transfer` es terminal y no abre el run destino. Las aserciones de escenario ganan `transferred_to: <agent_id>` y `packet_slots`. Métrica `gate` típica: precisión de enrutamiento.
- **Especialistas:** `ScriptedSource` gana `transfer_packet` opcional; el escenario arranca como un run transferido (con `origin` sintético).
- **Vara del especialista:**
  - Su `eval_suite` gana `routing_scenarios` (mensajes que deben llegarle).
  - Al publicar con ficha nueva o cambiada, el gate corre recepción `prod` sobre los `routing_scenarios` de **todos** los especialistas publicados del directorio, más los suyos, con el **directorio candidato**.
  - Falla si los suyos no le llegan o si empeora el enrutamiento de otro (dentro del ruido).
- **Compatibilidad de `accepts`:** si el `accepts` nuevo rompe REL-T1 para una recepción publicada, la propuesta se marca `yardstick_loosened` (aprobación humana aparte).
- **Catálogo del DSL:** suma `engine.directory_read`, `engine.run_transferred`, `engine.transfer_received` y `engine.transfer_rejected`, y la ventana `session`.
- **Métricas sugeridas:** precisión de enrutamiento, transferencias por sesión, tasa de `transfer_rejected` y resolución por sesión.

## 8. Observabilidad

- **Correlación:** `session_id` en todo evento (ya existe en `EngineEvent`) y `transfer_id` en los cuatro eventos de transferencia.
- **Enlace verificable:** `RunOrigin.from_event_hash` ata la cadena del destino al evento exacto del origen. `agentcore replay` verifica el enlace al reproducir una sesión.
- **OTel:** span `agentcore.transfer` (atributos `transfer_id`, `from_agent`, `to_agent`, `to_release_id`, `outcome`) y un *span link* del primer span del run destino al span de la transferencia.
- **Linaje por sesión:** §5.3. El linaje por run (`lineage_for_run`) incluye `origin`.
- **Sin PII:** el paquete solo aparece como `packet_fp`; los slots viajan por las vistas de M7.

## 9. Fallas

| Falla | Comportamiento |
|---|---|
| El directorio no responde | La tool da `error` y el flow sigue por esa rama (aclarar o escalar) |
| El destino no está en el directorio leído | `transfer_rejected(not_in_directory)` y rama `rejected` |
| El destino ya no tiene `prod` activo | `transfer_rejected(no_active_release)` |
| El principal no es elegible | `transfer_rejected(not_eligible)` |
| El paquete no cumple `accepts` | `transfer_rejected(accepts_mismatch)` |
| Se superó el tope de la sesión | `transfer_rejected(transfer_limit)` |
| Falla de la base a mitad de la transferencia | Se revierte el turno completo; reintento con `client_turn_id` |
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
| T-TR-09 | Replay de una sesión con transferencia reproduce los dos runs y verifica el enlace |
| T-TR-10 | `GET /v1/sessions/{id}/lineage` devuelve la cadena con releases, `transfer_id` y hash del directorio |
| T-TR-11 | M1: G0-26, G0-27, G0-22 (salida de `agent` como destino) y AG-03 |
| T-TR-12 | Evaluación: en la suite de recepción `transfer` es terminal y no abre el destino |
| T-TR-13 | Gate del especialista: una ficha que roba escenarios de otro especialista hace fallar el gate |
| T-TR-14 | Un `accepts` que rompe a la recepción publicada se marca `yardstick_loosened` |
| T-TR-15 | Ningún evento de transferencia lleva valores de slots ni datos de vista `full` |

## 11. Fases de construcción (demo: solo la ida)

| Fase | Contenido |
|---|---|
| 1 | M0: tipos, nodo, outcome, eventos, `RunOrigin`, `IdKind.transfer`; `contracts/` |
| 2 | M1: reglas de §6 |
| 3 | Registry: `directory()`, hash y tool `directory/list` (adaptador en `composition`) |
| 4 | M5 y M2: `decide` con `choices_from` (proveedor JEV `choice`) y manejador de `transfer` |
| 5 | M4 y M9: sesión con varios runs, transferencia atómica, `TurnResult.run_id`/`agent`, linaje por sesión |
| 6 | M11: enlace por hash, replay de sesión y spans |
| 7 | Demo: recepción más dos especialistas (`disputas`, a partir de `atencion`, y uno de consulta) con sus suites |
| 8 | Evaluación (§7), en coordinación con la rama de `feat/eval-metrics` |

El congelamiento es el 02/10. Lo realista es llegar a las fases 1 a 5 y una versión mínima de la 6 (enlace por hash y linaje, sin spans). La fase 8 depende de cuándo se integre el diseño de evaluación.

## 12. Abiertos

1. **Proveedores de `choices_from`:** JEV `choice` acepta opciones en runtime; el proveedor `classifier` no. Decidir si `choices_from` exige JEV o un `llm_structured`, y cómo se calibra el umbral con opciones variables.
2. **Especialistas que exigen `step_up`:** ¿se listan con una marca y la transferencia dispara `step_up` antes de crear el destino, o se excluyen (demo)?
3. **Vuelta a recepción:** `Agent.on_out_of_scope: transfer(<agent>)` con el texto pendiente; requiere el tope por sesión.
4. **Tope y presupuesto por sesión:** valores de `max_transfers_per_session` y si hay un presupuesto de costo por sesión, además del de cada run.
5. **Dueño de la tool `directory/list`:** registry (`composition`) o unidad 3.
6. **Ejemplos de las fichas en el prompt:** límite de tamaño total del directorio que ve el modelo (número de especialistas por etiqueta).
7. **`SCHEMA_VERSION`:** este cambio, T1 (ya integrado, 1.1.0), `write_draft` y `Agent.metrics` suben versión; se asigna al integrar.
