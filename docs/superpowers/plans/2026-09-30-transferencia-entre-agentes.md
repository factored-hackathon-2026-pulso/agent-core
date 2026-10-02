# Transferencia entre agentes — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que un agente de recepción transfiera, en el mismo turno, la conversación a un especialista elegido de un directorio, con las dos cadenas de auditoría enlazadas por hash y el linaje de la sesión consultable.

**Architecture:**
- M0 define los tipos: ficha y contrato del agente, directorio, nodo `transfer`, `RunOrigin` y los eventos.
- M1 valida los flows. M5 elige entre opciones de runtime con `decide_with_schema`.
- M2 produce un `TransferRequest` terminal y M4 lo resuelve: rechaza por la rama `rejected` o cierra el run origen y procesa el mismo turno en un run nuevo de la misma sesión.
- El directorio es un puerto nuevo (`AgentDirectory`) con implementación en memoria y en el registry. La tool `directory/list` lo sirve desde `composition`.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, Postgres 16 (solo pruebas de integración), `uv`.

**Spec:** `docs/specs/2026-09-30-transferencia-entre-agentes-design.md` (ADR 0021). Leer también `docs/specs/motor/00-indice.md`, m00, m01, m02, m04, m05, m09 y m11.

## Global Constraints

- Todo el código va en inglés: identificadores, comentarios y docstrings. Los mensajes de violación de M1 y los documentos de `docs/` siguen en español.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`: instantes del `Clock` e ids del `IdSource`.
- Dinero y cifras con `Decimal`. JSON de entrada con `agent_core.domain.loads`; canonización con `canonical_bytes`.
- Fixtures y pruebas solo con datos sintéticos.
- Nada en vista `full` sale a modelos, logs ni eventos: el paquete de transferencia aparece en eventos solo como huella (`packet_fp`) y como nombres de slots.
- Un módulo solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de los módulos permitidos en `.importlinter`.
- Tras cambiar M0: `uv run agentcore contracts` y avisar el cambio de interfaz. `SCHEMA_VERSION` sube de versión menor respecto de la vigente en `main` al empezar (hoy 1.1.0 → 1.2.0; si otra rama ya la subió, la siguiente libre).
- Cada tarea termina con `uv run pytest <carpetas tocadas>`, `uv run lint-imports`, `uv run mypy` y `uv run ruff check .` en verde, y actualiza el spec del módulo que toca en el mismo commit.
- Commits con la línea `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. En PowerShell, el mensaje va en un archivo (`git commit -F <archivo>`).

## Decisiones que este plan fija (revisar antes de ejecutar)

Cierran abiertos o simplifican la spec. La tarea 12 actualiza la spec con ellas.

| # | Decisión | Por qué |
|---|---|---|
| P1 | No hay evento `directory_read`. El directorio leído (`directory`, `directory_hash`, `candidates`) va dentro de `run_transferred` y `transfer_rejected`. | Leer el directorio es un `tool_called` normal; un evento aparte necesitaría un emisor nuevo en M2. |
| P2 | `RunOrigin.from_event_hash` es el hash del **último evento de la cadena origen al cerrar el turno de la transferencia** (el `turn_completed`). | Ese hash cubre `run_transferred` y `run_closed` por encadenamiento y está disponible sin cambiar `EventChain`. |
| P3 | Un `decide` con `choices_from` usa `branch_on: choice`. `choices_from` apunta a una **lista de strings** en un hecho (la tool devuelve `choices`). El motor agrega `"none"` a las opciones. El umbral se busca por valor y, si falta, por la etiqueta comodín `"*"`. | Sin comodín, cada especialista nuevo exigiría recalibrar antes de recibir conversaciones (abierto 1 de la spec). |
| P4 | El especialista **debe declarar `understand`** (AG-03). El run destino procesa el mismo texto con guardas, Understand y flow. Usa el mismo `turn_id`. | Es el camino de un turno normal; no hay un modo de arranque nuevo. |
| P5 | `TurnConfig.max_transfers_per_session = 1` (demo: solo la ida). La cuenta viaja en `RunOrigin.depth`. | Tope simple, sin presupuesto de sesión. |
| P6 | Una transferencia pedida durante `start_run` se rechaza (`no_turn`). | No hay texto del cliente para continuar. |
| P7 | Fuera de este plan: REL-T1 y la evaluación (spec §7), los agentes de la demo (fase 7), los spans OTel y el replay completo de sesiones. Aquí solo se verifica el enlace entre cadenas. | Dependen de `feat/eval-metrics` o del contenido de la demo; cada uno va en su plan. |

## Review Focus

1. **Reintento del mismo turno tras la transferencia.** El cliente reenvía el mismo `client_turn_id` (timeout del lado de la app). Se espera la misma respuesta combinada, sin un tercer run. Prueba en la tarea 9 (`test_retry_after_transfer_returns_the_same_result`).
2. **Destino inventado por el modelo.** La decisión trae un `agent_id` que no está en el directorio leído en este run, aunque exista y esté publicado. Se espera `transfer_rejected(not_in_directory)` y la rama `rejected`. Prueba en la tarea 8.
3. **El especialista no tiene release `prod` activa al transferir.** Se publicó, apareció en el directorio y luego se revocó. Se espera `no_active_release`, no un error 500. Prueba en la tarea 8.
4. **Lectura posterior del run origen.** Un `access_denied` llega a la cadena origen después de la transferencia. La verificación del enlace debe seguir pasando, porque busca el hash en la cadena y no exige que sea el último. Prueba en la tarea 10.
5. **Falla a mitad de la transferencia.** Se inyecta una caída en el commit. Se espera que no quede un run origen cerrado sin destino ni un destino sin origen. Prueba en la tarea 9 (`test_crash_on_commit_leaves_nothing`).

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `agent_core/domain/transfer.py` (nuevo) | `RoutingCard`, `AcceptedSlot`, `TransferContract`, `DirectoryEntry`, `DirectorySnapshot`, `TransferPacket`, `RunOrigin`, `directory_hash`, `packet_problem` |
| `agent_core/domain/eligibility.py` (nuevo) | `transfer_ineligibility(agent, principal, subject, locale)` |
| `agent_core/domain/entities.py` | `Agent.routing`, `Agent.accepts` |
| `agent_core/domain/nodes.py` | `TransferConfig`, `TransferNode`, `DecideConfig.choices_from`, `RESULTS`, `TERMINAL` |
| `agent_core/domain/outcomes.py` | `Outcome.transferred` |
| `agent_core/domain/state.py` | `RunState.origin` |
| `agent_core/domain/events.py` | `RunTransferred`, `TransferReceived`, `TransferRejected`, `RunStartedPayload.origin`, `closed_by="transfer"` |
| `agent_core/domain/turn.py` | `TurnResult.agent` |
| `agent_core/ports/ids.py` | `IdKind.transfer` |
| `agent_core/ports/directory.py` (nuevo) | puerto `AgentDirectory` |
| `agent_core/ports/uow.py` | `list_runs_by_session`; `find_run_by_session` devuelve el run abierto o, si no hay, el más nuevo |
| `agent_core/flows/rules/transfer.py` (nuevo) | G0-26, G0-27 |
| `agent_core/flows/schema.py`, `rules/structure.py`, `rules/phase5.py`, `agent.py`, `validate.py` | G0-01, G0-03, G0-10, G0-11, AG-03 |
| `agent_core/decision/service.py` | umbral comodín `"*"` |
| `agent_core/interpreter/ports.py`, `context.py`, `handlers/base.py`, `handlers/decide.py`, `handlers/transfer.py` (nuevo), `handlers/__init__.py`, `loop.py` | `decide_choice`, `TransferRequest`, manejador |
| `agent_core/composition/decision.py` | `DecisionAdapter.decide_choice` |
| `agent_core/composition/directory.py` (nuevo) | `DirectoryToolExecutor`, `DIRECTORY_TOOL` |
| `agent_core/registry/directory.py` (nuevo), `store.py`, `memory.py`, `postgres/store.py` | `RegistryDirectory` y `aliases_named` |
| `testing/fakes/directory.py` (nuevo), `testing/fakes/decision.py`, `testing/fakes/storage.py` | dobles |
| `agent_core/adapters/postgres_uow.py` | sesión con varios runs |
| `agent_core/turn/transfer.py` (nuevo), `turn/engine.py`, `turn/events.py`, `turn/frame.py`, `turn/closing.py`, `turn/config.py`, `turn/results.py` | transferencia en M4 |
| `agent_core/api/app.py`, `api/schemas.py` | `GET /v1/sessions/{id}/lineage` |
| `agent_core/audit/links.py` (nuevo), `audit/__init__.py` | `verify_transfer_link` |

---

### Task 1: M0 — contrato del agente, directorio y elegibilidad

**Files:**
- Create: `agent_core/domain/transfer.py`, `agent_core/domain/eligibility.py`
- Modify: `agent_core/domain/entities.py` (clase `Agent`), `agent_core/domain/__init__.py` (exportar y `__all__`)
- Test: `tests/m00/test_transfer_types.py` (nuevo)

**Interfaces:**
- Produces:
  - `RoutingCard(directory: EntityId, summary: str, examples: list[str])`
  - `AcceptedSlot(type: Literal["string","integer","decimal","date","boolean"], required: bool = False)`
  - `TransferContract(slots: dict[str, AcceptedSlot])`
  - `DirectoryEntry(agent_id: EntityId, release_id: str, summary: str, examples: list[str], accepts: TransferContract, supported_locales: list[Locale])`
  - `DirectorySnapshot(directory: EntityId, hash: Sha256Hex, entries: list[DirectoryEntry])`, con la propiedad `choices -> list[str]`
  - `TransferPacket(reason: str, trigger: str, slots: dict[str, JsonValue])`
  - `RunOrigin(kind: Literal["transfer"], transfer_id: str, from_run_id: str, from_agent: EntityRef, from_release_id: str, from_event_hash: Sha256Hex, depth: PositiveInt)`
  - `directory_hash(pairs: Iterable[tuple[str, str]]) -> str`
  - `packet_problem(contract: TransferContract, slots: Mapping[str, JsonValue]) -> str | None`
  - `transfer_ineligibility(agent: Agent, principal: Principal, subject: SubjectRef | None, locale: Locale) -> str | None`, que devuelve `None` si es elegible o un motivo sin datos
  - `Agent.routing: RoutingCard | None = None` y `Agent.accepts: TransferContract | None = None`

- [ ] **Step 1: Write the failing tests**

```python
"""Transfer types (ADR 0021, spec §3.1): routing card, input contract, directory and eligibility."""

from decimal import Decimal

import pytest

from agent_core.domain import (
    AcceptedSlot,
    DirectoryEntry,
    DirectorySnapshot,
    TransferContract,
    directory_hash,
    packet_problem,
    transfer_ineligibility,
)
from testing.builders import principal
from tests.m04.harness import agent_data
from agent_core.domain import Agent, SubjectRef

CONTRACT = TransferContract(slots={"problem": AcceptedSlot(type="string", required=True),
                                   "amount": AcceptedSlot(type="decimal")})
CUSTOMER_SUBJECT = SubjectRef(kind="customer", ref="cust-001")


def _specialist(**over: object) -> Agent:
    data = agent_data("disputas", routing={"directory": "customer-care", "summary": "Disputes",
                                           "examples": ["no reconozco un cargo"]},
                      accepts=CONTRACT.model_dump(mode="json"), understand="understand@1.0.0")
    return Agent.model_validate(data | over)


def test_directory_hash_is_order_independent_and_changes_with_releases() -> None:
    a = directory_hash([("disputas", "rel-1"), ("saldos", "rel-2")])
    assert a == directory_hash([("saldos", "rel-2"), ("disputas", "rel-1")])
    assert a != directory_hash([("disputas", "rel-9"), ("saldos", "rel-2")])
    assert len(a) == 64


def test_snapshot_choices_are_the_agent_ids_in_order() -> None:
    entry = DirectoryEntry(agent_id="disputas", release_id="rel-1", summary="s", examples=[],
                           accepts=CONTRACT, supported_locales=["es"])
    snap = DirectorySnapshot(directory="customer-care", hash="a" * 64, entries=[entry])
    assert snap.choices == ["disputas"]


@pytest.mark.parametrize(("slots", "problem"), [
    ({"problem": "x"}, None),
    ({"problem": "x", "amount": Decimal("12.50")}, None),
    ({}, "missing_required_slot"),
    ({"problem": "x", "other": "y"}, "slot_not_accepted"),
    ({"problem": 5}, "slot_type_mismatch"),
    ({"problem": "x", "amount": 1.5}, "slot_type_mismatch"),  # float is never a decimal
])
def test_packet_problem(slots: dict[str, object], problem: str | None) -> None:
    assert packet_problem(CONTRACT, slots) == problem  # type: ignore[arg-type]


def test_eligibility_reasons_carry_no_data() -> None:
    agent = _specialist()
    assert transfer_ineligibility(agent, principal(), CUSTOMER_SUBJECT, "es") is None
    assert transfer_ineligibility(agent, principal(type="advisor", id="adv-1"), CUSTOMER_SUBJECT,
                                  "es") == "principal_type"
    assert transfer_ineligibility(agent, principal(), SubjectRef(kind="account", ref="a-1"),
                                  "es") == "subject_kind"
    assert transfer_ineligibility(agent, principal(), CUSTOMER_SUBJECT, "en") == "locale"
    step_up = _specialist(min_auth_level="step_up")
    assert transfer_ineligibility(step_up, principal(), CUSTOMER_SUBJECT, "es") == "auth_level"
    no_contract = _specialist(accepts=None)
    assert transfer_ineligibility(no_contract, principal(), CUSTOMER_SUBJECT, "es") == "no_contract"
    task = _specialist(mode="task", subject_kinds=[])
    assert transfer_ineligibility(task, principal(), None, "es") == "mode"
```

Note: `tests/m04/harness.agent_data` already builds a valid `Agent` dict; `routing`, `accepts` and `understand` override it. If importing the M4 harness from `tests/m00` causes an import cycle, copy `agent_data` into `tests/m00/samples.py` instead.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/m00/test_transfer_types.py -q`
Expected: FAIL with `ImportError: cannot import name 'AcceptedSlot'`.

- [ ] **Step 3: Implement `agent_core/domain/transfer.py`**

```python
"""Agent-to-agent transfer types (ADR 0021, spec §3): routing card, input contract, directory, packet."""

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, PositiveInt

from agent_core.domain.base import EntityId, Locale, Model, Sha256Hex
from agent_core.domain.json import JsonValue, canonical_bytes, sha256_hex
from agent_core.domain.refs import EntityRef

SlotType = Literal["string", "integer", "decimal", "date", "boolean"]


class RoutingCard(Model):
    """What a specialist solves, as shown to the reception agent (spec §3.1)."""

    directory: EntityId
    summary: str = Field(min_length=1, max_length=500)
    examples: list[str] = Field(default_factory=list, max_length=20)


class AcceptedSlot(Model):
    type: SlotType
    required: bool = False


class TransferContract(Model):
    """The slots a specialist accepts in a transfer packet (spec §3.1, D6)."""

    slots: dict[str, AcceptedSlot] = Field(default_factory=dict)


class DirectoryEntry(Model):
    agent_id: EntityId
    release_id: str = Field(min_length=1)
    summary: str
    examples: list[str] = Field(default_factory=list)
    accepts: TransferContract
    supported_locales: list[Locale]


class DirectorySnapshot(Model):
    """The directory as one run read it: filtered for its principal, with the hash of the full directory."""

    directory: EntityId
    hash: Sha256Hex
    entries: list[DirectoryEntry] = Field(default_factory=list)

    @property
    def choices(self) -> list[str]:
        return [entry.agent_id for entry in self.entries]


class TransferPacket(Model):
    """What travels to the specialist (D7). `trigger` is the user text in the `model` view."""

    reason: str = Field(min_length=1)
    trigger: str
    slots: dict[str, JsonValue] = Field(default_factory=dict)


class RunOrigin(Model):
    """Why a run exists when another run transferred to it (spec §3.3, P2, P5)."""

    kind: Literal["transfer"]
    transfer_id: str
    from_run_id: str
    from_agent: EntityRef
    from_release_id: str
    from_event_hash: Sha256Hex
    depth: PositiveInt


def directory_hash(pairs: Iterable[tuple[str, str]]) -> str:
    """sha256 of the sorted `(agent_id, release_id)` pairs: changes on publish, promote or revoke."""
    return sha256_hex(canonical_bytes(sorted([list(pair) for pair in pairs])))


def _matches(kind: SlotType, value: JsonValue) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "decimal":
        return isinstance(value, Decimal | int) and not isinstance(value, bool)
    return isinstance(value, str) and _is_iso_date(value)


def _is_iso_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def packet_problem(contract: TransferContract, slots: Mapping[str, JsonValue]) -> str | None:
    """`None` if the slots fit the contract; otherwise a reason code without values."""
    for name, spec in contract.slots.items():
        if spec.required and name not in slots:
            return "missing_required_slot"
    for name, value in slots.items():
        spec = contract.slots.get(name)
        if spec is None:
            return "slot_not_accepted"
        if not _matches(spec.type, value):
            return "slot_type_mismatch"
    return None
```

Check that `canonical_bytes` and `sha256_hex` are exported where this imports them (`agent_core/domain/json.py`). If `sha256_hex` lives elsewhere, import it from there; do not reimplement it.

- [ ] **Step 4: Implement `agent_core/domain/eligibility.py`**

```python
"""Whether a principal may be transferred to an agent (ADR 0021 D8, spec §4). Pure: no ports."""

from agent_core.domain.base import Locale
from agent_core.domain.entities import Agent
from agent_core.domain.identity import Principal, SubjectRef


def transfer_ineligibility(agent: Agent, principal: Principal, subject: SubjectRef | None,
                           locale: Locale) -> str | None:
    """`None` if eligible; otherwise the first failing reason, with no principal data."""
    if agent.mode != "conversational":
        return "mode"
    if agent.accepts is None:
        return "no_contract"
    if principal.type not in agent.invocable_by:
        return "principal_type"
    if subject is not None and subject.kind not in agent.subject_kinds:
        return "subject_kind"
    if locale not in agent.supported_locales:
        return "locale"
    if principal.auth.level < agent.min_auth_level:
        return "auth_level"
    return None
```

- [ ] **Step 5: Add the fields to `Agent`** (`agent_core/domain/entities.py`)

Add `from agent_core.domain.transfer import RoutingCard, TransferContract` to the imports and, after `max_repair_turns_per_run`:

```python
    routing: RoutingCard | None = None    # without a card the agent is in no directory (ADR 0021)
    accepts: TransferContract | None = None  # without a contract the agent receives no transfers
```

Export from `agent_core/domain/__init__.py` (import and `__all__`): `AcceptedSlot`, `DirectoryEntry`, `DirectorySnapshot`, `RoutingCard`, `RunOrigin`, `SlotType`, `TransferContract`, `TransferPacket`, `directory_hash`, `packet_problem`, `transfer_ineligibility`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/m00 -q`
Expected: PASS, except `tests/m00/test_contracts.py::test_committed_contracts_are_current`. That one is fixed in Task 2, which regenerates `contracts/` once for all of M0.

- [ ] **Step 7: Commit**

```bash
git add agent_core/domain tests/m00/test_transfer_types.py
git commit -F msg.txt   # feat(m0): routing card, transfer contract, directory types and eligibility
```

---

### Task 2: M0 — nodo `transfer`, outcome, origen, eventos y contratos

**Files:**
- Modify: `agent_core/domain/nodes.py`, `outcomes.py`, `state.py`, `events.py`, `turn.py`, `version.py`, `__init__.py`; `agent_core/ports/ids.py`; `agent_core/turn/events.py` (solo el alias `ClosedBy`)
- Modify: `docs/specs/motor/m00-dominio-y-contratos.md` (rev. 12 en el changelog y el bloque de nodos/eventos)
- Test: `tests/m00/test_transfer_nodes_events.py` (nuevo); actualizar `tests/m00/test_public_api.py` (versión)

**Interfaces:**
- Produces:
  - `TransferPacketSpec(reason: str, slots: list[str] = [])` y `TransferConfig(target_from: str, directory_from: SaveAs, packet: TransferPacketSpec)`
  - `TransferNode(type="transfer")`; `RESULTS["transfer"] == {"rejected"}`; `"transfer" in TERMINAL`
  - `DecideConfig.choices_from: str | None = None`
  - `Outcome.transferred`, no declarable en ningún modo
  - `RunState.origin: RunOrigin | None = None`
  - `RunStartedPayload.origin: RunOrigin | None = None`
  - `RunClosedPayload.closed_by` acepta `"transfer"`
  - eventos `RunTransferred`, `TransferReceived` y `TransferRejected`, con los payloads de abajo
  - `TurnResult.agent: EntityRef | None = None`
  - `IdKind.transfer`

- [ ] **Step 1: Write the failing tests**

```python
"""Transfer node, outcome, origin and events (ADR 0021, spec §3.2-§3.4)."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core import domain
from agent_core.domain import (
    DECLARABLE,
    RESULTS,
    TERMINAL,
    AnyEvent,
    EVENT_EMITTERS,
    Flow,
    Outcome,
    RunOrigin,
    TransferNode,
    is_declarable,
)
from agent_core.ports import IdKind
from testing.builders import run_state
from tests.m00.samples import make_event

TRANSFER: dict[str, Any] = {"id": "transfer", "type": "transfer",
                            "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                                       "packet": {"reason": "routed", "slots": ["problem"]}},
                            "next": {"rejected": "esc"}}
ORIGIN: dict[str, Any] = {"kind": "transfer", "transfer_id": "transfer-0001", "from_run_id": "run-0001",
                          "from_agent": "reception@1.0.0", "from_release_id": "rel-1",
                          "from_event_hash": "a" * 64, "depth": 1}
FP: dict[str, Any] = {"alg": "HMAC-SHA256", "kid": "test-1", "value": "c" * 64}  # `Fingerprint` (domain/shared.py)


def test_transfer_node_parses_and_is_terminal() -> None:
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [
        TRANSFER, {"id": "esc", "type": "escalate", "config": {"reason_code": "transfer_rejected"}}]})
    assert isinstance(flow.nodes[0], TransferNode)
    assert RESULTS["transfer"] == frozenset({"rejected"})
    assert "transfer" in TERMINAL


def test_decide_accepts_choices_from() -> None:
    node = {"id": "route", "type": "decide",
            "config": {"model": "router@1.0.0", "branch_on": "choice", "save_as": "route",
                       "choices_from": "facts.directory.value.choices"},
            "next": {"chosen": "transfer", "none": "esc", "low_confidence": "esc"}}
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [node]})
    assert flow.nodes[0].config.choices_from == "facts.directory.value.choices"  # type: ignore[union-attr]


def test_transferred_is_never_declarable() -> None:
    assert not any(is_declarable(Outcome.transferred, mode) for mode in DECLARABLE)


def test_run_state_carries_origin_and_closes_as_transferred() -> None:
    state = run_state(origin=ORIGIN)
    assert isinstance(state.origin, RunOrigin) and state.origin.depth == 1
    closed = state.model_copy(update={"status": "closed", "outcome": "transferred",
                                      "closed_at": state.created_at, "inactive_after": None})
    assert closed.outcome is Outcome.transferred


def test_origin_depth_is_positive() -> None:
    with pytest.raises(ValidationError):
        RunOrigin.model_validate(ORIGIN | {"depth": 0})


@pytest.mark.parametrize(("kind", "payload"), [
    ("run_transferred", {"transfer_id": "transfer-0001", "to_agent": "disputas@1.0.0", "to_release_id": "rel-2",
                         "to_run_id": "run-0002", "reason": "routed", "packet_fp": FP, "directory": "customer-care",
                         "directory_hash": "b" * 64, "candidates": ["disputas"]}),
    ("transfer_received", {"transfer_id": "transfer-0001", "accepted_slots": ["problem"], "packet_fp": FP}),
    ("transfer_rejected", {"transfer_id": "transfer-0001", "to_agent": "disputas", "reason_code": "not_in_directory",
                           "directory": "customer-care", "directory_hash": "b" * 64}),
    ("run_closed", {"outcome": "transferred", "closed_by": "transfer"}),
    ("run_started", {"agent": "disputas@1.0.0", "mode": "conversational", "principal_type": "customer",
                     "locale": "es", "origin": ORIGIN}),
])
def test_transfer_events_validate(kind: str, payload: dict[str, Any]) -> None:
    event = TypeAdapter(AnyEvent).validate_python({**make_event(kind), "payload": payload})
    assert event.type == kind
    assert EVENT_EMITTERS[kind] == frozenset({"M4"})


def test_transfer_rejected_reason_is_closed() -> None:
    bad = {"transfer_id": "t", "to_agent": None, "reason_code": "because", "directory": None, "directory_hash": None}
    with pytest.raises(ValidationError):
        TypeAdapter(AnyEvent).validate_python({**make_event("transfer_rejected"), "payload": bad})


def test_id_kind_and_turn_result_agent() -> None:
    assert IdKind.transfer.value == "transfer"
    assert "agent" in domain.TurnResult.model_fields
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/m00/test_transfer_nodes_events.py -q`
Expected: FAIL on importing `TransferNode`.

- [ ] **Step 3: Implement the node** (`agent_core/domain/nodes.py`)

After `AwaitApprovalConfig`:

```python
class TransferPacketSpec(Model):
    """What the transfer node sends: a literal reason and the names of validated slots (ADR 0021 D7)."""

    reason: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    slots: list[SaveAs] = Field(default_factory=list)


class TransferConfig(Model):
    """`target_from` is a `decisions.<save_as>.choice` path; `directory_from` the fact of `directory/list`."""

    target_from: Annotated[str, StringConstraints(pattern=r"^decisions\.[a-z][a-z0-9_]*\.choice$")]
    directory_from: SaveAs
    packet: TransferPacketSpec
```

After `AwaitApprovalNode`:

```python
class TransferNode(_NodeBase):
    """`transfer` node (ADR 0021): hands the conversation to a specialist; terminal on success."""
    type: Literal["transfer"]
    config: TransferConfig
```

Add `| Annotated[TransferNode, Tag("transfer")]` to `Node`. Add `"transfer": frozenset({"rejected"})` to `RESULTS` and change `TERMINAL` to `frozenset({"escalate", "end", "transfer"})`.

In `DecideConfig` add, after `input_view`:

```python
    choices_from: str | None = None  # path to a list of strings in a fact; options decided at runtime (ADR 0021)
```

Check `G0-03`: it uses `RESULTS[kind]` for `transfer`. `decide` with `choices_from` is handled in Task 3.

- [ ] **Step 4: Implement outcome, state, events and ids**

- `agent_core/domain/outcomes.py`: add `transferred = "transferred"` to `Outcome`. Do not add it to any `DECLARABLE` set: only the engine assigns it.
- `agent_core/domain/state.py`: import `RunOrigin` from `agent_core.domain.transfer` and add `origin: RunOrigin | None = None` after `handoff_ref`.
- `agent_core/domain/events.py`:

```python
TransferRejectReason = Literal[
    "not_in_directory", "no_active_release", "not_eligible", "accepts_mismatch", "transfer_limit", "no_turn"]


class RunTransferredPayload(Model):
    """Payload of `run_transferred` (audit view; ADR 0021, P1). No slot values: only the packet fingerprint."""
    transfer_id: str
    to_agent: EntityRef
    to_release_id: str
    to_run_id: str
    reason: str
    packet_fp: Fingerprint
    directory: str
    directory_hash: Sha256Hex
    candidates: list[str]


class TransferReceivedPayload(Model):
    """Payload of `transfer_received` (audit view)."""
    transfer_id: str
    accepted_slots: list[str]
    packet_fp: Fingerprint


class TransferRejectedPayload(Model):
    """Payload of `transfer_rejected` (audit view)."""
    transfer_id: str
    to_agent: str | None = None
    reason_code: TransferRejectReason
    directory: str | None = None
    directory_hash: Sha256Hex | None = None
```

`Fingerprint` is the model `{alg: "HMAC-SHA256", kid, value}` from `agent_core/domain/shared.py`, the same one `ViewService.project(...).fingerprint` returns.

Add `origin: RunOrigin | None = None` to `RunStartedPayload`; change `RunClosedPayload.closed_by` to `Literal["flow", "abandonment", "escalation", "revocation", "transfer"]`; add the three event classes (`type: Literal["run_transferred"]` and so on), include them in `AnyEvent` and `_EVENT_CLASSES`, and add `"run_transferred"`, `"transfer_received"` and `"transfer_rejected"` with `frozenset({"M4"})` to `EVENT_EMITTERS`.

- `agent_core/domain/turn.py`: `TurnResult.agent: EntityRef | None = None` (the agent that answered).
- `agent_core/ports/ids.py`: `transfer = "transfer"`.
- `agent_core/turn/events.py`: `ClosedBy = Literal["flow", "abandonment", "escalation", "revocation", "transfer"]`.
- `agent_core/domain/version.py`: next minor version (see Global Constraints). Update `tests/m00/test_public_api.py`.
- Export the new names from `agent_core/domain/__init__.py`.

- [ ] **Step 5: Regenerate contracts and run the tests**

Run: `uv run agentcore contracts`, then `uv run pytest tests/m00 tests/m01 tests/m02 tests/m04 -q`
Expected: PASS. If an M1 test fails because `transfer` is in `PRODUCTION_NODE_KINDS`-like checks, that is Task 3; `transfer` must **not** be added to `PRODUCTION_NODE_KINDS`.

- [ ] **Step 6: Update the m00 spec and commit**

In `docs/specs/motor/m00-dominio-y-contratos.md`, add a changelog entry like this one, using the version actually set:

> rev. 12 (2026-09-30), transferencia entre agentes (ADR 0021; `SCHEMA_VERSION` → 1.2.0): `Agent.routing`/`accepts`, `domain/transfer.py`, nodo `transfer`, `DecideConfig.choices_from`, `Outcome.transferred`, `RunState.origin`, eventos `run_transferred`/`transfer_received`/`transfer_rejected`, `RunStartedPayload.origin`, `closed_by="transfer"`, `TurnResult.agent`, `IdKind.transfer`.

```bash
git add agent_core contracts tests/m00 docs/specs/motor/m00-dominio-y-contratos.md
git commit -F msg.txt   # feat(m0): transfer node, origin, events and contracts (interface change)
```

---

### Task 3: M1 — reglas del nodo `transfer` y de `decide` con opciones

**Files:**
- Create: `agent_core/flows/rules/transfer.py`
- Modify: `agent_core/flows/schema.py` (`_required_paths`), `agent_core/flows/rules/structure.py` (`g0_03`), `agent_core/flows/rules/phase5.py` (`_read_sites`, `g0_11`), `agent_core/flows/agent.py` (AG-03), `agent_core/flows/validate.py` (`FLOW_RULES`)
- Modify: `docs/specs/motor/m01-validacion-estatica.md` (§3.2 tabla de lugares, §3.4 G0-26/G0-27, §3.13 AG-03)
- Test: `tests/m01/test_transfer_rules.py` (nuevo)

**Interfaces:**
- Consumes: `TransferNode`, `DecideConfig.choices_from` (Task 2).
- Produces:
  - `g0_26(ctx: Ctx) -> Iterator[Violation]` y `g0_27(ctx: Ctx) -> Iterator[Violation]`
  - AG-03 en `validate_flow_for_agent` y en `validate_agent`

Rules:
- **G0-01:** `choices_from` is a path (`_required_paths`). The schema already rejects a malformed `target_from`.
- **G0-03:** a `decide` with `choices_from` has the results `{"chosen", "none", "low_confidence"}`, and its `branch_on` must be `"choice"`.
- **G0-10:** `choices_from` reads only `facts` (with `.value`).
- **G0-11:** `"choice"` is in the model's `calibrated_fields`.
- **G0-26:** `target_from` names a `decide` with `choices_from` that dominates the `transfer`, and `directory_from` is the `save_as` of a `tool` node whose tool is `directory/list` that also dominates it.
- **G0-27:** each `packet.slots` is the `slot` of some `collect` in the flow.
- **AG-03:** a flow with `transfer` requires `agent.mode == "conversational"`; an agent with `accepts` has `routing`, has `understand` and is `conversational`.

- [ ] **Step 1: Write the failing tests**

```python
"""Static rules for transfer flows (ADR 0021, spec §6): G0-03, G0-10, G0-11, G0-26, G0-27 and AG-03."""

from copy import deepcopy
from typing import Any

from agent_core.domain import Agent, DecisionModelDef, Flow, ToolDef
from agent_core.flows.agent import validate_agent, validate_flow_for_agent
from tests.m01.cases import AGENT, check, node, registry, rules

DIRECTORY_TOOL = ToolDef.model_validate({
    "id": "directory/list", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
    "idempotent": True, "description": "Lists specialists", "args_schema": {"type": "object"}})
ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0",
    "output_schema": {"type": "object", "properties": {"choice": {"type": "string"}}},
    "calibrated_fields": ["choice"], "providers": [{"provider": "llm_structured"}],
    "calibration": {"method": "none"}})


def reception() -> dict[str, Any]:
    return deepcopy({"id": "reception", "version": "1.0.0", "priority": 10, "nodes": [
        {"id": "ask", "type": "collect", "config": {"slot": "problem", "prompt_ref": "t/pedir"},
         "next": {"ok": "directory", "max_attempts": "esc"}},
        {"id": "directory", "type": "tool",
         "config": {"tool": "directory/list@1", "args": {"directory": "customer-care"}, "save_as": "directory"},
         "next": {"ok": "route", "error": "esc", "timeout": "esc", "denied": "esc"}},
        {"id": "route", "type": "decide",
         "config": {"model": "router@1", "branch_on": "choice", "save_as": "route",
                    "choices_from": "facts.directory.value.choices", "input_view": ["slots.problem"]},
         "next": {"chosen": "transfer", "none": "esc", "low_confidence": "esc"}},
        {"id": "transfer", "type": "transfer",
         "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                    "packet": {"reason": "routed", "slots": ["problem"]}},
         "next": {"rejected": "esc"}},
        {"id": "esc", "type": "escalate", "config": {"reason_code": "transfer_rejected"}},
    ]})


def reg() -> Any:
    return registry(DIRECTORY_TOOL, ROUTER)


def test_reception_flow_is_valid() -> None:
    assert check(reception(), reg()) == []


def test_choices_decide_needs_its_three_results() -> None:
    d = reception()
    del node(d, "route")["next"]["none"]
    assert rules(check(d, reg())) == {"G0-03"}


def test_choices_decide_branches_on_choice() -> None:
    d = reception()
    node(d, "route")["config"]["branch_on"] = "other"
    assert "G0-03" in rules(check(d, reg()))


def test_choices_from_reads_only_facts() -> None:
    d = reception()
    node(d, "route")["config"]["choices_from"] = "slots.problem"
    assert "G0-10" in rules(check(d, reg()))


def test_g0_26_target_must_come_from_a_dominating_choices_decide() -> None:
    d = reception()
    node(d, "transfer")["config"]["target_from"] = "decisions.other.choice"
    assert "G0-26" in rules(check(d, reg()))


def test_g0_26_directory_must_come_from_directory_list() -> None:
    d = reception()
    node(d, "transfer")["config"]["directory_from"] = "problem"
    assert "G0-26" in rules(check(d, reg()))


def test_g0_26_a_path_around_the_decide_is_a_violation() -> None:
    d = reception()
    node(d, "directory")["next"]["ok"] = "transfer"  # skips `route`
    found = rules(check(d, reg()))
    assert "G0-26" in found


def test_g0_27_packet_slots_must_be_collected() -> None:
    d = reception()
    node(d, "transfer")["config"]["packet"]["slots"] = ["problem", "card"]
    assert rules(check(d, reg())) == {"G0-27"}


def test_ag_03_transfer_needs_a_conversational_agent() -> None:
    flow = Flow.model_validate(reception())
    task = Agent.model_validate(AGENT | {"mode": "task", "entry_flow": "reception@1"})
    assert "AG-03" in {v.rule for v in validate_flow_for_agent(flow, task, reg())}


def test_ag_03_an_agent_with_accepts_needs_routing_and_understand() -> None:
    agent = Agent.model_validate(AGENT | {"accepts": {"slots": {}}})
    assert "AG-03" in {v.rule for v in validate_agent(agent, reg())}
```

Note: `tests/m01/cases.py` has `AGENT` without `understand`. If `validate_agent` reports a G0-02 for `understand`, add a decision model to the registry. AG-03 must appear either way.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/m01/test_transfer_rules.py -q`
Expected: several FAIL (no G0-26, G0-27 or AG-03; G0-03 reports the `chosen`/`none` keys as unknown).

- [ ] **Step 3: Implement G0-01, G0-03, G0-10 and G0-11**

`agent_core/flows/schema.py`, in `_required_paths`, after the `DecideNode | AgentNode` block:

```python
    if isinstance(node, DecideNode) and node.config.choices_from is not None:
        yield ("/config/choices_from", node.config.choices_from)
```

`agent_core/flows/rules/structure.py`, inside `g0_03`, before the `if isinstance(node, DecideNode):` block:

```python
        if isinstance(node, DecideNode) and node.config.choices_from is not None:
            if node.config.branch_on != "choice":
                yield ctx.v("G0-03", ident, "un decide con choices_from ramifica por 'choice'", "/config/branch_on")
            expected = CHOICE_RESULTS
        elif isinstance(node, DecideNode):
            ...  # existing enum branch, unchanged
```

with `CHOICE_RESULTS = frozenset({"chosen", "none", "low_confidence"})` at module level (export it; Task 6 uses it).

`agent_core/flows/rules/phase5.py`, in `_read_sites`, add a site for `choices_from`. Use a new branch, because a `decide` already yields `/config/input_view`:

```python
    if isinstance(node, DecideNode) and node.config.choices_from is not None:
        yield _Site("/config/choices_from", _parsed([node.config.choices_from]), frozenset({"facts"}))
```

Make it a separate `if` placed **before** the existing `if/elif` chain, so the `elif` chain still runs. `g0_11` needs no change: `branch_on == "choice"` must be in `calibrated_fields` like any other field.

- [ ] **Step 4: Implement G0-26 and G0-27** (`agent_core/flows/rules/transfer.py`)

```python
"""G0-26 and G0-27: where a transfer gets its target, its directory and its packet (ADR 0021, spec §6)."""

from collections.abc import Iterator

from agent_core.domain import CollectNode, DecideNode, ToolNode, TransferNode
from agent_core.flows.context import Ctx
from agent_core.flows.violations import Violation, clip

DIRECTORY_TOOL_ID = "directory/list"


def _dominates(ctx: Ctx, producers: list[str], target: str) -> bool:
    """No path from the entry reaches `target` without going through one of `producers`."""
    cut = frozenset((p, result) for p in producers for result in ctx.graph.nodes[p].next)
    return target not in ctx.graph.from_entry(cut)


def g0_26(ctx: Ctx) -> Iterator[Violation]:
    for node in ctx.flow.nodes:
        if not isinstance(node, TransferNode):
            continue
        decide_name = node.config.target_from.split(".")[1]
        deciders = [n.id for n in ctx.flow.nodes if isinstance(n, DecideNode)
                    and n.config.save_as == decide_name and n.config.choices_from is not None]
        if not deciders or not _dominates(ctx, deciders, node.id):
            yield ctx.v("G0-26", node.id, f"target_from {clip(node.config.target_from)} no sale de un decide con "
                        "choices_from que domine al transfer", "/config/target_from")
        tools = [n.id for n in ctx.flow.nodes if isinstance(n, ToolNode)
                 and n.config.save_as == node.config.directory_from and n.config.tool.id == DIRECTORY_TOOL_ID]
        if not tools or not _dominates(ctx, tools, node.id):
            yield ctx.v("G0-26", node.id, f"directory_from {clip(node.config.directory_from)!r} no es el hecho de "
                        f"un nodo tool {DIRECTORY_TOOL_ID} que domine al transfer", "/config/directory_from")


def g0_27(ctx: Ctx) -> Iterator[Violation]:
    collected = {n.config.slot for n in ctx.flow.nodes if isinstance(n, CollectNode)}
    for node in ctx.flow.nodes:
        if not isinstance(node, TransferNode):
            continue
        for i, slot in enumerate(node.config.packet.slots):
            if slot not in collected:
                yield ctx.v("G0-27", node.id, f"el slot {clip(slot)!r} del paquete no lo recolecta ningún collect",
                            f"/config/packet/slots/{i}")
```

Add `g0_26` and `g0_27` to `FLOW_RULES` in `agent_core/flows/validate.py`. A dangling node id in a `_dominates` call is not possible: `ctx.graph.nodes` holds every node, and G0-03 already reports broken `next`.

- [ ] **Step 5: Implement AG-03** (`agent_core/flows/agent.py`)

In `validate_flow_for_agent`, after AG-01:

```python
    if agent.mode != "conversational" and any(isinstance(n, TransferNode) for n in flow.nodes):
        found.append(Violation(rule="AG-03", flow=label,
                               message=f"el agente {_agent_label(agent)} es {agent.mode}: transfer solo en "
                               "agentes conversacionales"))
```

In `validate_agent`, before `return`:

```python
    if agent.accepts is not None:
        missing = [name for name, value in (("routing", agent.routing), ("understand", agent.understand))
                   if value is None]
        if missing or agent.mode != "conversational":
            what = ", ".join(missing) if missing else "modo conversacional"
            found.append(Violation(rule="AG-03", path=where,
                                   message=clip(f"un agente con accepts necesita {what}", 240)))
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/m01 -q`
Expected: PASS.

- [ ] **Step 7: Update m01 and commit**

In m01:
- §3.2: add the row `decide.choices_from | facts`.
- §3.4: add G0-26 and G0-27.
- §3.13: add AG-03.
- §3.4: note that a `decide` with `choices_from` has the results `chosen`, `none` and `low_confidence`.

```bash
git add agent_core/flows tests/m01/test_transfer_rules.py docs/specs/motor/m01-validacion-estatica.md
git commit -F msg.txt   # feat(m1): G0-26, G0-27, AG-03 and decide with runtime choices
```

---

### Task 4: Directorio — puerto, doble en memoria, registry y tool `directory/list`

**Files:**
- Create: `agent_core/ports/directory.py`, `testing/fakes/directory.py`, `agent_core/registry/directory.py`, `agent_core/composition/directory.py`
- Modify: `agent_core/ports/__init__.py`; `agent_core/registry/store.py`, `memory.py`, `postgres/store.py` (`aliases_named`); `agent_core/registry/__init__.py`; `testing/fakes/registry.py` (`aliases`); `.importlinter` (si `composition` necesita permiso nuevo; normalmente no)
- Modify: `docs/specs/2026-09-29-registry-design.md` (§7.1 directorio, §18)
- Test: `tests/contracts/test_directory_contract.py` (nuevo), `tests/composition/test_directory_tool.py` (nuevo; si la carpeta no existe, `tests/registry/test_directory_tool.py`)

**Interfaces:**
- Consumes: `Agent.routing`, `DirectoryEntry`, `DirectorySnapshot`, `directory_hash` y `transfer_ineligibility` (Task 1).
- Produces:
  - `AgentDirectory` (Protocol), con `def members(self, directory: str) -> list[DirectoryMember]`; `DirectoryMember = tuple[str, Agent]` (`release_id`, agente publicado), ordenado por `agent.id`
  - `InMemoryDirectory(registry: InMemoryRegistry)`
  - `RegistryDirectory(store: RegistryStore, registry: RegistryPort)`
  - `DirectoryToolExecutor(inner: ToolExecutor, directory: AgentDirectory, authz: AuthzPort, ids: IdSource)`, que implementa `ToolExecutor`
  - `DIRECTORY_TOOL: ToolDef` (`directory/list@1.0.0`, `read`)
  - el resultado de la tool es `DirectorySnapshot.model_dump(mode="json") | {"choices": [...]}`

- [ ] **Step 1: Write the failing contract test**

```python
"""`AgentDirectory` contract: in-memory and registry-backed (ADR 0021, spec §4)."""

from collections.abc import Iterator

import pytest

from agent_core.domain import Agent, AgentSelector, Release
from agent_core.ports import AgentDirectory
from testing.fakes.directory import InMemoryDirectory
from testing.fakes.registry import InMemoryRegistry
from tests.m04.harness import agent_data

CARD = {"directory": "customer-care", "summary": "s", "examples": []}


def _agent(agent_id: str, directory: str | None = "customer-care") -> Agent:
    card = None if directory is None else CARD | {"directory": directory}
    return Agent.model_validate(agent_data(agent_id, routing=card, accepts={"slots": {}},
                                           understand="understand@1.0.0"))


def _release(release_id: str, agent: Agent) -> Release:
    return Release.model_validate({"id": release_id, "status": "active", "language_detection": "lang@1.0.0",
                                   "entities": {"agent": {agent.id: agent.version}}})


@pytest.fixture
def memory() -> Iterator[tuple[InMemoryRegistry, AgentDirectory]]:
    registry = InMemoryRegistry()
    yield registry, InMemoryDirectory(registry)


def test_members_are_prod_agents_with_the_tag(memory: tuple[InMemoryRegistry, AgentDirectory]) -> None:
    registry, directory = memory
    for agent_id, tag in (("disputas", "customer-care"), ("saldos", "customer-care"), ("interno", "staff"),
                          ("sin-ficha", None)):
        agent = _agent(agent_id, tag)
        registry.add(agent)
        registry.add_release(_release(f"rel-{agent_id}", agent), agent_id, alias="prod")
    assert [(rid, a.id) for rid, a in directory.members("customer-care")] == [
        ("rel-disputas", "disputas"), ("rel-saldos", "saldos")]


def test_revoked_releases_are_not_members(memory: tuple[InMemoryRegistry, AgentDirectory]) -> None:
    registry, directory = memory
    agent = _agent("disputas")
    registry.add(agent)
    registry.add_release(_release("rel-1", agent), "disputas", alias="prod")
    registry.revoke("rel-1")
    assert directory.members("customer-care") == []
```

Mirror these two checks for `RegistryDirectory` in `tests/integration/test_registry_postgres.py`: import a seed with two agents carrying `routing`, then assert `members`. Mark the test `integration`.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `uv run pytest tests/contracts/test_directory_contract.py -q`
Expected: FAIL (`AgentDirectory` does not exist).

- [ ] **Step 3: Implement the port and the in-memory fake**

`agent_core/ports/directory.py`:

```python
from typing import Protocol

from agent_core.domain.entities import Agent

DirectoryMember = tuple[str, Agent]  # (release_id, published agent)


class AgentDirectory(Protocol):
    """Published agents (alias `prod`, active release) whose routing card carries a directory tag (ADR 0021).

    Not filtered by principal: eligibility is the caller's job. Sorted by agent id."""

    def members(self, directory: str) -> list[DirectoryMember]: ...
```

Export `AgentDirectory` and `DirectoryMember` from `agent_core/ports/__init__.py`.

`testing/fakes/registry.py`: add

```python
    def aliases(self, alias: str) -> list[tuple[str, str]]:
        """`(agent_id, release_id)` for every agent with this alias, sorted by agent id."""
        return sorted((agent_id, rid) for (agent_id, name), rid in self._aliases.items() if name == alias)
```

`testing/fakes/directory.py`:

```python
"""In-memory `AgentDirectory` over `InMemoryRegistry` (ADR 0021)."""

from agent_core.domain import Agent, EntityKind, EntityRef
from agent_core.ports import DirectoryMember
from testing.fakes.registry import InMemoryRegistry


class InMemoryDirectory:
    def __init__(self, registry: InMemoryRegistry) -> None:
        self._registry = registry

    def members(self, directory: str) -> list[DirectoryMember]:
        found: list[DirectoryMember] = []
        for agent_id, release_id in self._registry.aliases("prod"):
            if self._registry.release_status(release_id) != "active":
                continue
            release = self._registry.resolve_release_by_id(release_id)
            version = release.entities.get(EntityKind.agent, {}).get(agent_id)
            if version is None:
                continue
            agent = self._registry.get(EntityRef(id=agent_id, version=version), Agent)
            if agent.routing is not None and agent.routing.directory == directory:
                found.append((release_id, agent))
        return found
```

Add to `InMemoryRegistry`:

```python
    def resolve_release_by_id(self, release_id: str) -> Release:
        return self._releases[release_id].model_copy(deep=True)
```

- [ ] **Step 4: Implement the registry-backed directory**

Add `def aliases_named(self, alias: str) -> list[tuple[str, str]]: ...` to `RegistryTx`. Implement it in:
- `registry/memory.py`: `sorted((a, rid) for (a, name), rid in self._s.aliases.items() if name == alias)`;
- `registry/postgres/store.py`: `SELECT agent_id, release_id FROM reg_aliases WHERE alias = %s ORDER BY agent_id`. Check the real table and column names in that file before writing the SQL.

`agent_core/registry/directory.py`:

```python
"""`AgentDirectory` over the registry: `prod` aliases of active releases with a matching routing card."""

from agent_core.domain import Agent, EntityKind, EntityRef
from agent_core.ports import DirectoryMember, RegistryPort
from agent_core.registry.store import RegistryStore


class RegistryDirectory:
    def __init__(self, store: RegistryStore, registry: RegistryPort, releases: "ReleaseReader") -> None:
        self._store, self._registry, self._releases = store, registry, releases

    def members(self, directory: str) -> list[DirectoryMember]:
        with self._store.transaction() as tx:
            pairs = tx.aliases_named("prod")
        found: list[DirectoryMember] = []
        for agent_id, release_id in pairs:
            if self._registry.release_status(release_id) != "active":
                continue
            version = self._releases(release_id).entities.get(EntityKind.agent, {}).get(agent_id)
            if version is None:
                continue
            agent = self._registry.get(EntityRef(id=agent_id, version=version), Agent)
            if agent.routing is not None and agent.routing.directory == directory:
                found.append((release_id, agent))
        return found
```

`ReleaseReader` is `Callable[[str], Release]`, the same type as `EngineDeps.releases` (`PostgresRegistry.release`). Define it as a type alias in this module. Export `RegistryDirectory` from `agent_core/registry/__init__.py`.

- [ ] **Step 5: Write the failing tool test**

```python
"""`directory/list` tool: filtered by eligibility, hash of the full directory (ADR 0021, spec §4)."""

from agent_core.composition.directory import DIRECTORY_TOOL, DirectoryToolExecutor
from agent_core.domain import Agent, EntityRef, Release, ToolStatus, directory_hash
from agent_core.ports import ToolCallContext
from testing.builders import principal
from testing.fakes.directory import InMemoryDirectory
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.tools import FakeToolExecutor
from tests.m02.harness import AllowAllAuthz
from tests.m04.harness import agent_data

TOOL = EntityRef(id="directory/list", version="1.0.0")


def _world() -> tuple[DirectoryToolExecutor, ToolCallContext]:
    registry = InMemoryRegistry()
    for agent_id, over in (("disputas", {}), ("asesores", {"invocable_by": ["advisor"]})):
        agent = Agent.model_validate(agent_data(agent_id, routing={"directory": "customer-care", "summary": agent_id,
                                                                   "examples": []},
                                                accepts={"slots": {}}, understand="understand@1.0.0", **over))
        registry.add(agent)
        registry.add_release(Release.model_validate({"id": f"rel-{agent_id}", "status": "active",
                                                     "language_detection": "lang@1.0.0",
                                                     "entities": {"agent": {agent_id: "1.0.0"}}}), agent_id)
    ids = FakeIds()
    executor = DirectoryToolExecutor(FakeToolExecutor(ids), InMemoryDirectory(registry), AllowAllAuthz(), ids)
    ctx = ToolCallContext(run_id="run-0001", release="rel-reception", principal=principal(),
                          subject={"kind": "customer", "ref": "cust-001"})  # type: ignore[arg-type]
    return executor, ctx


def test_lists_only_eligible_agents_and_hashes_the_full_directory() -> None:
    executor, ctx = _world()
    result = executor.execute(TOOL, {"directory": "customer-care", "locale": "es"}, {}, ctx)
    assert result.status is ToolStatus.ok
    assert result.result_full["choices"] == ["disputas"]  # type: ignore[index]
    assert result.result_full["hash"] == directory_hash(  # type: ignore[index]
        [("asesores", "rel-asesores"), ("disputas", "rel-disputas")])


def test_definition_is_a_documented_read_tool() -> None:
    executor, _ = _world()
    definition = executor.definition(TOOL)
    assert definition == DIRECTORY_TOOL and definition.risk_class.value == "read"
    assert definition.description and definition.args_schema is not None


def test_other_tools_go_to_the_inner_executor() -> None:
    executor, ctx = _world()
    other = EntityRef(id="otra", version="1.0.0")
    result = executor.execute(other, {}, {}, ctx)
    assert result.status is not ToolStatus.ok  # FakeToolExecutor knows nothing about `otra`
```

`AllowAllAuthz.authorize_agent` returns allowed. The `asesores` agent is excluded by `transfer_ineligibility` (`principal_type`), not by authz. Check `FakeToolExecutor` behaviour for an unknown tool (it may raise `KeyError`); adapt the last assertion to what it does.

- [ ] **Step 6: Implement `agent_core/composition/directory.py`**

```python
"""`directory/list` tool (ADR 0021, spec §4): the directory filtered for the caller, served by composition."""

from agent_core.domain import (
    DirectoryEntry,
    DirectorySnapshot,
    EntityRef,
    JsonValue,
    ToolDef,
    ToolStatus,
    directory_hash,
    transfer_ineligibility,
)
from agent_core.ports import AgentDirectory, AuthzPort, IdKind, IdSource, ToolCallContext, ToolExecutor, ToolResult

DIRECTORY_TOOL = ToolDef.model_validate({
    "id": "directory/list", "version": "1.0.0", "risk_class": "read", "min_auth_level": "anonymous",
    "idempotent": True, "source": "directory",
    "description": "Lists the specialists of a directory the current person can be transferred to.",
    "args_schema": {"type": "object", "properties": {"directory": {"type": "string"},
                                                     "locale": {"type": "string"}},
                    "required": ["directory", "locale"]},
})


class DirectoryToolExecutor:
    """Wraps a `ToolExecutor`: serves `directory/list` and delegates every other tool."""

    def __init__(self, inner: ToolExecutor, directory: AgentDirectory, authz: AuthzPort, ids: IdSource) -> None:
        self._inner, self._directory, self._authz, self._ids = inner, directory, authz, ids

    def definition(self, tool: EntityRef) -> ToolDef:
        if (tool.id, tool.version) == (DIRECTORY_TOOL.id, DIRECTORY_TOOL.version):
            return DIRECTORY_TOOL
        return self._inner.definition(tool)

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if (tool.id, tool.version) != (DIRECTORY_TOOL.id, DIRECTORY_TOOL.version):
            return self._inner.execute(tool, args, bound_params, ctx, idempotency_key)
        call_id = self._ids.new_id(IdKind.call)
        name, locale = args.get("directory"), args.get("locale")
        if not isinstance(name, str) or not isinstance(locale, str):
            return ToolResult(status=ToolStatus.error, call_id=call_id, error="bad_args")
        members = self._directory.members(name)
        entries = [
            DirectoryEntry(agent_id=agent.id, release_id=release_id, summary=agent.routing.summary,
                           examples=list(agent.routing.examples), accepts=agent.accepts,
                           supported_locales=list(agent.supported_locales))
            for release_id, agent in members
            if agent.routing is not None and agent.accepts is not None
            and transfer_ineligibility(agent, ctx.principal, ctx.subject, locale) is None
            and self._authz.authorize_agent(ctx.principal, agent, ctx.subject).allowed
        ]
        snapshot = DirectorySnapshot(directory=name, hash=directory_hash((a.id, r) for r, a in members),
                                     entries=entries)
        result: dict[str, JsonValue] = {**snapshot.model_dump(mode="json"), "choices": snapshot.choices}
        return ToolResult(status=ToolStatus.ok, result_full=result, source="directory", call_id=call_id)
```

`locale` is an argument because `ToolCallContext` has no locale. The flow passes it as a literal per locale, or through a slot. Record this in Task 12's spec update, together with D4. Check that `ToolStatus` is importable from `agent_core.domain` (it is defined in `domain/shared.py`); otherwise import it from `agent_core.ports`.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/contracts/test_directory_contract.py tests/composition -q`, then `uv run lint-imports`
Expected: PASS. If import-linter rejects `composition` → `registry`, check `.importlinter`; `composition` may import everything.

- [ ] **Step 8: Update the registry spec and commit**

Registry spec: add §7.1b "Directorio": `aliases_named`, `RegistryDirectory`, and that it serves only active `prod` releases. Add §18 row 11: `directory/list` lives in `composition`, which answers abierto 5 of the transfer spec.

```bash
git add agent_core testing tests docs/specs/2026-09-29-registry-design.md
git commit -F msg.txt   # feat(directory): AgentDirectory port, registry directory and directory/list tool
```

---

### Task 5: M5 — elegir entre opciones de runtime

**Files:**
- Modify: `agent_core/decision/service.py` (`_passes`), `agent_core/interpreter/ports.py` (`DecisionPort.decide_choice`), `agent_core/composition/decision.py`, `testing/fakes/decision.py`
- Modify: `docs/specs/motor/m05-decision-model.md` (umbral comodín y `decide_choice`)
- Test: `tests/m05/test_choice.py` (nuevo), `tests/composition/test_decision_choice.py` (nuevo)

**Interfaces:**
- Produces:
  - `DecisionPort.decide_choice(model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str], locale: Locale) -> DecisionResult`; el valor de la decisión es `{"choice": <una de choices o "none">}`
  - `WILDCARD_LABEL = "*"` en `agent_core.decision`
  - `ScriptedDecision.decide_choice`

- [ ] **Step 1: Write the failing tests**

`tests/m05/test_choice.py`:

```python
"""Wildcard threshold for runtime choices (ADR 0021, P3)."""

from agent_core.decision import WILDCARD_LABEL
from agent_core.decision.service import DecisionService


def test_threshold_falls_back_to_the_wildcard_label() -> None:
    artifact = _artifact({("choice", WILDCARD_LABEL, "llm_structured", "es"): 0.6})
    assert DecisionService._passes(artifact, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.7)
    assert not DecisionService._passes(artifact, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.5)


def test_a_specific_label_wins_over_the_wildcard() -> None:
    artifact = _artifact({("choice", WILDCARD_LABEL, "llm_structured", "es"): 0.6,
                          ("choice", "disputas", "llm_structured", "es"): 0.9})
    assert not DecisionService._passes(artifact, "choice", {"choice": "disputas"}, "llm_structured", "es", 0.7)
```

Build `_artifact(thresholds)` with `CalibrationArtifact(run_id="cal-1", split_hash="0" * 64, method="none", thresholds=thresholds)`; check the required fields of `CalibrationArtifact` in `decision/calibration/artifact.py`.

`tests/composition/test_decision_choice.py` must check that `DecisionAdapter.decide_choice` calls `decide_with_schema` with this schema:

```python
{"type": "object", "additionalProperties": False, "required": ["choice"],
 "properties": {"choice": {"type": "string", "enum": ["disputas", "saldos", "none"]}}}
```

Use a `DecisionService` with a scripted provider. `tests/m05/helpers.py` already builds one: reuse it.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/m05/test_choice.py tests/composition/test_decision_choice.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`agent_core/decision/service.py`, in `_passes`, replace the lookup:

```python
        key = (name, value_label(item), provider, locale)
        threshold = artifact.thresholds.get(key)
        if threshold is None:
            threshold = artifact.thresholds.get((name, WILDCARD_LABEL, provider, locale))
        if threshold is None:
            return False
        return p_cal >= threshold
```

Define `WILDCARD_LABEL = "*"` in `decision/types.py` and export it from `agent_core.decision`. A real label can never be `"*"` because choices are agent ids (`EntityId` has no `*`).

`agent_core/interpreter/ports.py`, `DecisionPort`:

```python
    def decide_choice(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str],
                      locale: Locale) -> DecisionResult:
        """Picks one of `choices` or `"none"`; the decision value is `{"choice": ...}` (ADR 0021)."""
        ...
```

`agent_core/composition/decision.py`:

```python
NONE_CHOICE = "none"


def choice_schema(choices: list[str]) -> dict[str, JsonValue]:
    return {"type": "object", "additionalProperties": False, "required": ["choice"],
            "properties": {"choice": {"type": "string", "enum": [*choices, NONE_CHOICE]}}}

    # inside DecisionAdapter
    def decide_choice(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str],
                      locale: Locale) -> DecisionResult:
        output, event = self._service.decide_with_schema(model, choice_schema(choices), inputs_model_view, locale,
                                                         self._vault, scope=self._scope)
        return self._result(output, event)
```

Factor the body that `decide` already has (building `Decision` and `DecisionResult`) into `_result(output, event)`, and have both methods use it.

`testing/fakes/decision.py`:

```python
    def decide_choice(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str],
                      locale: Locale) -> DecisionResult:
        self.choice_calls.append((model, deepcopy(inputs_model_view), list(choices), locale))
        if not self._script:
            raise AssertionError("ScriptedDecision sin resultado guionado")
        return self._script.popleft()
```

with `self.choice_calls: list[tuple[EntityRef, dict[str, JsonValue], list[str], str]] = []` in `__init__`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/m05 tests/composition tests/m02 -q`
Expected: PASS.

- [ ] **Step 5: Update m05 and commit**

m05 §3.1: thresholds by value with the `"*"` wildcard. m05 §2: `decide_choice`.

```bash
git add agent_core testing tests docs/specs/motor/m05-decision-model.md
git commit -F msg.txt   # feat(m5): runtime choices with a wildcard threshold
```

---

### Task 6: M2 — `decide` con opciones y manejador de `transfer`

**Files:**
- Create: `agent_core/interpreter/handlers/transfer.py`
- Modify: `agent_core/interpreter/handlers/decide.py`, `handlers/__init__.py`, `handlers/base.py` (`NodeResult.transfer`), `context.py` (`StepOutcome.transfer`), `loop.py`, `interpreter/__init__.py`
- Modify: `docs/specs/motor/m02-interprete.md` (§3.3 filas `decide` y `transfer`)
- Test: `tests/m02/test_decide_choices.py` (nuevo), `tests/m02/test_transfer.py` (nuevo)

**Interfaces:**
- Consumes: `DecisionPort.decide_choice` (Task 5), `TransferNode` y `DirectorySnapshot` (Tasks 1-2), `CHOICE_RESULTS` (Task 3).
- Produces:
  - `TransferRequest(node_id: NodeId, target: str | None, snapshot: DirectorySnapshot | None, reason: str, slots: dict[str, JsonValue])` (dataclass congelada en `agent_core.interpreter.context`, exportada)
  - `NodeResult.transfer` y `StepOutcome.transfer: TransferRequest | None = None`
  - `handle_transfer`, que devuelve `stop=Stop.terminal` con `transfer` y sin `result_key` (el nodo queda en `transfer`, para que M4 pueda seguir por `rejected`)

- [ ] **Step 1: Write the failing tests**

`tests/m02/test_decide_choices.py`:

```python
"""`decide` with `choices_from` (ADR 0021, P3)."""

from agent_core.domain import DecisionModelDef
from testing.fakes.decision import make_decision
from tests.m02.harness import World, fact, flow, slot

ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["choice"],
    "input_view": [], "providers": [{"provider": "llm_structured"}], "calibration": {"method": "none"}})
TAIL = [{"id": "ok", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
        {"id": "low", "type": "escalate", "config": {"reason_code": "low_confidence"}}]
DECIDE = {"id": "route", "type": "decide",
          "config": {"model": "router@1.0.0", "branch_on": "choice", "save_as": "route",
                     "choices_from": "facts.directory.value.choices", "input_view": ["slots.problem"]},
          "next": {"chosen": "ok", "none": "no", "low_confidence": "low"}}


def _run(choices: list[str], *pushed: object) -> tuple[World, str]:
    w = World()
    w.add(ROUTER)
    w.decisions.push(*pushed)  # type: ignore[arg-type]
    state = w.state(flow(DECIDE, *TAIL), slots={"problem": slot("no reconozco un cargo")},
                    facts={"directory": fact({"choices": choices})})
    return w, w.step(state).state.active_flow.node_id  # type: ignore[union-attr]


def test_chosen_above_threshold() -> None:
    w, node = _run(["disputas", "saldos"], make_decision({"choice": "disputas"}, {"choice": True}))
    assert node == "ok"
    assert w.decisions.choice_calls[0][2] == ["disputas", "saldos"]


def test_none_and_low_confidence() -> None:
    assert _run(["disputas"], make_decision({"choice": "none"}, {"choice": True}))[1] == "no"
    assert _run(["disputas"], make_decision({"choice": "disputas"}, {"choice": False}))[1] == "low"


def test_empty_directory_is_none_without_calling_the_model() -> None:
    w, node = _run([])
    assert node == "no" and w.decisions.choice_calls == []


def test_a_choice_outside_the_list_is_low_confidence() -> None:
    assert _run(["disputas"], make_decision({"choice": "inventado"}, {"choice": True}))[1] == "low"
```

`tests/m02/test_transfer.py`:

```python
"""`transfer` node (ADR 0021): terminal, with the request M4 resolves."""

from typing import Any

from agent_core.interpreter import Stop
from tests.m02.harness import World, fact, flow, slot
from testing.fakes.decision import make_decision

SNAPSHOT: dict[str, Any] = {
    "directory": "customer-care", "hash": "b" * 64, "choices": ["disputas"],
    "entries": [{"agent_id": "disputas", "release_id": "rel-2", "summary": "s", "examples": [],
                 "accepts": {"slots": {"problem": {"type": "string", "required": True}}},
                 "supported_locales": ["es"]}]}
TRANSFER = {"id": "transfer", "type": "transfer",
            "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                       "packet": {"reason": "routed", "slots": ["problem", "card"]}},
            "next": {"rejected": "esc"}}
ESC = {"id": "esc", "type": "escalate", "config": {"reason_code": "transfer_rejected"}}


def _state(w: World, **over: Any) -> Any:
    decision = make_decision({"choice": "disputas"}, {"choice": True}).decision
    base: dict[str, Any] = {"slots": {"problem": slot("no reconozco un cargo")},
                            "facts": {"directory": fact(SNAPSHOT)}, "decisions": {"route": decision}}
    return w.state(flow(TRANSFER, ESC), **(base | over))


def test_transfer_stops_terminal_with_the_request() -> None:
    w = World()
    out = w.step(_state(w))
    assert out.stop is Stop.terminal and out.escalation is None
    request = out.transfer
    assert request is not None and request.target == "disputas" and request.reason == "routed"
    assert request.slots == {"problem": "no reconozco un cargo"}  # `card` was never collected: omitted
    assert request.snapshot is not None and request.snapshot.choices == ["disputas"]
    assert out.state.active_flow.node_id == "transfer"  # M4 may still follow `rejected`


def test_missing_decision_gives_a_request_without_target() -> None:
    w = World()
    out = w.step(_state(w, decisions={}))
    assert out.transfer is not None and out.transfer.target is None


def test_claimed_slots_do_not_travel() -> None:
    w = World()
    out = w.step(_state(w, slots={"problem": slot("x", "claimed")}))
    assert out.transfer is not None and out.transfer.slots == {}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/m02/test_decide_choices.py tests/m02/test_transfer.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the choice branch of `decide`** (`handlers/decide.py`)

At the top of `handle_decide`, after the budget check:

```python
    if cfg.choices_from is not None:
        return _decide_choice(node, state, ctx)
```

```python
def _choices(path: str, state: RunState, ctx: StepContext) -> list[str] | None:
    try:
        value = model_inputs([path], state, ctx)[path]
    except MissingPath:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


def _decide_choice(node: DecideNode, state: RunState, ctx: StepContext) -> NodeResult:
    cfg = node.config
    assert cfg.choices_from is not None
    choices = _choices(cfg.choices_from, state, ctx)
    if not choices:
        return NodeResult(state, result_key="none")
    try:
        inputs = model_inputs(cfg.input_view or [], state, ctx)
    except MissingPath:
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    ref = exact_ref(ctx, EntityKind.decision_model, cfg.model)
    result = ctx.decisions.decide_choice(ref, inputs, choices, ctx.locale)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    state = state.model_copy(update={"decisions": {**state.decisions, cfg.save_as: result.decision}})
    value = result.decision.value.get("choice")
    if value == "none":
        key = "none"
    elif isinstance(value, str) and value in choices and result.above_threshold.get("choice", False):
        key = "chosen"
    else:
        key = LOW_CONFIDENCE
    return NodeResult(state, result_key=key, events=list(result.events))
```

Agent ids are not PII, so the `model` view returns them unchanged. If the classifier tokenizes `choices`, list `directory` as a source whose `choices`, `agent_id` and `release_id` fields are `public` in the field catalog. The test above would catch the tokenized case.

- [ ] **Step 4: Implement `transfer`** (`handlers/transfer.py`)

```python
"""`transfer` (ADR 0021): builds the request; M4 validates it and either transfers or follows `rejected`."""

from agent_core.domain import DirectorySnapshot, JsonValue, RunState, TransferNode
from agent_core.interpreter.context import Resume, StepContext, Stop, TransferRequest
from agent_core.interpreter.handlers.base import NodeResult


def _target(node: TransferNode, state: RunState) -> str | None:
    decision = state.decisions.get(node.config.target_from.split(".")[1])
    value = decision.value.get("choice") if decision is not None else None
    return value if isinstance(value, str) and value != "none" else None


def _snapshot(node: TransferNode, state: RunState) -> DirectorySnapshot | None:
    fact = state.facts.get(node.config.directory_from)
    if fact is None or not isinstance(fact.value, dict):
        return None
    try:
        return DirectorySnapshot.model_validate({k: v for k, v in fact.value.items() if k != "choices"})
    except ValueError:
        return None


def handle_transfer(node: TransferNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    slots: dict[str, JsonValue] = {
        name: state.slots[name].value for name in node.config.packet.slots
        if name in state.slots and state.slots[name].status == "validated"}
    request = TransferRequest(node_id=node.id, target=_target(node, state), snapshot=_snapshot(node, state),
                              reason=node.config.packet.reason, slots=slots)
    return NodeResult(state, stop=Stop.terminal, transfer=request)
```

`context.py`:

```python
@dataclass(frozen=True)
class TransferRequest:
    """What a `transfer` node asks M4 to do (ADR 0021). Values are `full`: they never go to an event."""

    node_id: NodeId
    target: str | None
    snapshot: DirectorySnapshot | None
    reason: str
    slots: dict[str, JsonValue] = field(default_factory=dict)
```

Add `transfer: TransferRequest | None = None` to `StepOutcome` (last field) and to `NodeResult`. In `loop.py`, pass `transfer=result.transfer` when building the terminal `StepOutcome` (use keyword arguments for the new field). Register `"transfer": handle_transfer` in `handlers/__init__.py`. Export `TransferRequest` from `agent_core/interpreter/__init__.py`.

The loop returns before `_move`, so `active_flow.node_id` stays at `transfer`: `result.result_key` is `None`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/m02 -q`
Expected: PASS.

- [ ] **Step 6: Update m02 and commit**

m02 §3.3: add the rows for `decide` with `choices_from` and for `transfer`. §4: "`transfer` never moves the pointer; M4 decides".

```bash
git add agent_core/interpreter tests/m02 docs/specs/motor/m02-interprete.md
git commit -F msg.txt   # feat(m2): decide with runtime choices and transfer node
```

---

### Task 7: UoW — una sesión con varios runs

**Files:**
- Modify: `agent_core/ports/uow.py`, `testing/fakes/storage.py`, `agent_core/adapters/postgres_uow.py`
- Modify: `docs/specs/motor/m04-ciclo-del-turno.md` (§3.1 paso 2)
- Test: `tests/contracts/test_uow_contract.py` (nuevos `check_*`)

**Interfaces:**
- Produces:
  - `find_run_by_session(session_id)` devuelve el run **abierto** de la sesión; si no hay, el más nuevo
  - `list_runs_by_session(session_id) -> list[RunState]`, en orden de creación

- [ ] **Step 1: Write the failing contract checks**

```python
def check_session_prefers_the_open_run_and_lists_all(b: Backend) -> None:
    closed = {"status": "closed", "outcome": "transferred", "closed_at": NOW, "inactive_after": None}
    with b.factory() as uow:
        uow.save_run(run_state(run_id="run-a", **closed), 0)
        uow.save_run(run_state(run_id="run-b"), 0)  # same session, open
        found = uow.find_run_by_session("session-0001")
        assert found is not None and found.run_id == "run-b"  # reads its own writes
        uow.commit()
    with b.factory() as uow:
        found = uow.find_run_by_session("session-0001")
        assert found is not None and found.run_id == "run-b"
        assert [r.run_id for r in uow.list_runs_by_session("session-0001")] == ["run-a", "run-b"]
        assert uow.list_runs_by_session("session-nope") == []


def check_session_without_open_runs_returns_the_newest(b: Backend) -> None:
    closed = {"status": "closed", "outcome": "resolved", "closed_at": NOW, "inactive_after": None}
    _seed(b.factory, run_id="run-a", **closed)
    _seed(b.factory, run_id="run-b", **closed)
    with b.factory() as uow:
        found = uow.find_run_by_session("session-0001")
        assert found is not None and found.run_id == "run-b"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/contracts/test_uow_contract.py -q -k session`
Expected: FAIL (`list_runs_by_session` is missing; the in-memory store may return `run-a`).

- [ ] **Step 3: Implement**

`ports/uow.py`: add `def list_runs_by_session(self, session_id: str) -> list[RunState]: ...` with the docstring "In creation order; reads its own writes." Change the docstring of `find_run_by_session` to "The open run of the session or, if none is open, the newest one."

`testing/fakes/storage.py`:
- `InMemoryStore.sessions` becomes `dict[str, list[str]]` (run ids in creation order).
- In `_apply`, append the run id when it is not yet in the list.
- `find_run_by_session` and `list_runs_by_session` merge the committed list with this UoW's local runs, keeping creation order. A local run not yet committed goes at the end.

```python
    def list_runs_by_session(self, session_id: str) -> list[RunState]:
        with self._store.lock:
            ids = list(self._store.sessions.get(session_id, []))
        ids += [rid for rid, s in self._runs.items() if s.session_id == session_id and rid not in ids]
        return [state for rid in ids if (state := self.load_run(rid)) is not None]

    def find_run_by_session(self, session_id: str) -> RunState | None:
        runs = self.list_runs_by_session(session_id)
        open_runs = [r for r in runs if r.status == "open"]
        return (open_runs or runs or [None])[-1]
```

Check every other use of `store.sessions` (`grep -n "sessions" testing/fakes/storage.py`) and adapt it.

`postgres_uow.py`:

```python
    def list_runs_by_session(self, session_id: str) -> list[RunState]:
        rows = self._conn.execute("SELECT run_id FROM runs WHERE session_id = %s ORDER BY run_seq",
                                  (session_id,)).fetchall()
        ids = [row[0] for row in rows]
        ids += [rid for rid, s in self._runs.items() if s.session_id == session_id and rid not in ids]
        return [state for rid in ids if (state := self.load_run(rid)) is not None]

    def find_run_by_session(self, session_id: str) -> RunState | None:
        runs = self.list_runs_by_session(session_id)
        open_runs = [r for r in runs if r.status == "open"]
        return (open_runs or runs or [None])[-1]
```

`(open_runs or runs or [None])[-1]` returns `None` when the session has no runs; mypy needs `list[RunState | None]` there. Write it as an explicit `if`/`return` if mypy complains.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/contracts/test_uow_contract.py tests/m04 -q`. With Postgres: `docker compose up -d postgres`, then `uv run pytest tests/contracts/test_uow_contract.py -m integration -q`.
Expected: PASS.

- [ ] **Step 5: Update m04 and commit**

m04 §3.1 paso 2: "la sesión puede tener varios runs; se carga el abierto (ADR 0021)".

```bash
git add agent_core/ports agent_core/adapters testing tests/contracts docs/specs/motor/m04-ciclo-del-turno.md
git commit -F msg.txt   # feat(uow): a session can hold several runs
```

---

### Task 8: M4 — validar la transferencia y seguir por `rejected`

**Files:**
- Create: `agent_core/turn/transfer.py`
- Modify: `agent_core/turn/frame.py` (`transfer_request`, `transfer_plan`), `turn/closing.py` (`apply_outcome`), `turn/engine.py` (`_advance`), `turn/events.py` (fábricas de los tres eventos), `turn/config.py` (`max_transfers_per_session`)
- Test: `tests/m04/test_transfer_validation.py` (nuevo)

**Interfaces:**
- Consumes: `StepOutcome.transfer` (Task 6), `transfer_ineligibility` y `packet_problem` (Task 1), `RegistryPort.resolve_release`.
- Produces:
  - `TransferPlan(transfer_id, target_agent: Agent, target_ref: EntityRef, release: Release, to_run_id, packet: TransferPacket, snapshot: DirectorySnapshot, depth: int)` (dataclass en `turn/transfer.py`)
  - `Transferer.validate(frame, request) -> TransferPlan | TransferRejectReason`
  - `TurnEvents.run_transferred`, `transfer_received` y `transfer_rejected`
  - `TurnConfig.max_transfers_per_session: int = 1`

- [ ] **Step 1: Extend the M4 harness**

In `tests/m04/harness.py`:
- `World` gains the `chain` kwarg (default `PlainChain()`) and a `reception` setup method.
- `reception()` registers:
  - the agent `recepcion` with the flow `recepcion` below;
  - the specialist `disputas`, with `routing`, `accepts` and `understand="understand@1.0.0"`, whose `entry_flow` is `disputa@1.0.0`;
  - the release under alias `prod` for both;
  - a `DecisionModelDef` `understand@1.0.0` (any valid one; `ScriptedUnderstand` answers anyway).

```python
RECEPCION = flow(
    "recepcion", 10,
    node("entender", "collect", {"slot": "problema", "prompt_ref": "t-pedir@1.0.0"}, ok="transferir",
         max_attempts="esc"),
    node("transferir", "transfer", {"target_from": "decisions.ruta.choice", "directory_from": "directorio",
                                     "packet": {"reason": "routed", "slots": ["problema"]}}, rejected="esc"),
    node("esc", "escalate", {"reason_code": "transfer_rejected"}),
)
```

The test seeds `facts.directorio` (the snapshot) and `decisions.ruta` in the state, so it needs neither the tool nor `decide`: Task 9's end-to-end test covers those. `open_run(agent="recepcion@1.0.0", active_flow={"flow": "recepcion@1.0.0", "node_id": "entender"}, awaiting="slot", awaiting_node_id="entender", facts=..., decisions=...)`.

- [ ] **Step 2: Write the failing tests**

```python
"""M4 validates a transfer and follows `rejected` when it fails (ADR 0021, spec §5.2)."""

from tests.m04.harness import World
from tests.m04.helpers import cmd


def _rejected(w: World) -> str:
    rejected = [e for e in w.events() if e.type == "transfer_rejected"]
    assert len(rejected) == 1
    return rejected[0].payload.reason_code


def test_target_not_in_the_directory_read_is_rejected() -> None:
    w = World()
    w.reception(choice="saldos")  # published but absent from the snapshot the run read
    w.understand.push(cmd("continue"))
    result = w.turn("no reconozco un cargo")
    assert _rejected(w) == "not_in_directory"
    assert result.status == "escalated"  # `rejected` → `esc`


def test_revoked_specialist_is_no_active_release() -> None:
    w = World()
    w.reception()
    w.registry.revoke(w.specialist_release_id)
    w.understand.push(cmd("continue"))
    w.turn("no reconozco un cargo")
    assert _rejected(w) == "no_active_release"


def test_ineligible_principal_is_rejected() -> None:
    w = World()
    w.reception(specialist_over={"invocable_by": ["advisor"]})
    w.understand.push(cmd("continue"))
    w.turn("no reconozco un cargo")
    assert _rejected(w) == "not_eligible"


def test_packet_outside_the_contract_is_rejected() -> None:
    w = World()
    w.reception(accepts={"slots": {"tarjeta": {"type": "string", "required": True}}})
    w.understand.push(cmd("continue"))
    w.turn("no reconozco un cargo")
    assert _rejected(w) == "accepts_mismatch"


def test_transfer_limit_is_enforced_by_depth() -> None:
    w = World()
    w.reception(origin_depth=1)  # this run already came from a transfer
    w.understand.push(cmd("continue"))
    w.turn("no reconozco un cargo")
    assert _rejected(w) == "transfer_limit"


def test_rejected_events_carry_no_slot_values() -> None:
    w = World()
    w.reception(choice="saldos")
    w.understand.push(cmd("continue"))
    w.turn("no reconozco un cargo")
    dumped = "".join(e.model_dump_json() for e in w.events())
    assert "no reconozco" not in dumped
```

`reception(...)` takes these parameters and builds the state:
- `choice`: the agent id in `decisions.ruta`; defaults to `"disputas"`;
- `specialist_over`: overrides for the specialist agent;
- `accepts`: the specialist's contract; defaults to `{"slots": {"problema": {"type": "string", "required": True}}}`;
- `origin_depth`: when given, the reception run carries a `RunOrigin` with that `depth`.

Expose `specialist_release_id` on the world.

- [ ] **Step 3: Run them and confirm they fail**

Run: `uv run pytest tests/m04/test_transfer_validation.py -q`
Expected: FAIL (M4 ignores `outcome.transfer`; the run stays open or errors).

- [ ] **Step 4: Implement the events** (`turn/events.py`)

```python
    def run_transferred(self, state: RunState, turn_id: str, plan: "TransferPlanView") -> RunTransferred:
        payload = RunTransferredPayload(
            transfer_id=plan.transfer_id, to_agent=plan.target_ref, to_release_id=plan.release_id,
            to_run_id=plan.to_run_id, reason=plan.reason, packet_fp=plan.packet_fp,
            directory=plan.directory, directory_hash=plan.directory_hash, candidates=list(plan.candidates))
        return RunTransferred(**self._base(state, turn_id), payload=payload)

    def transfer_received(self, state: RunState, turn_id: str, transfer_id: str, accepted: list[str],
                          packet_fp: Fingerprint) -> TransferReceived:
        payload = TransferReceivedPayload(transfer_id=transfer_id, accepted_slots=sorted(accepted),
                                          packet_fp=packet_fp)
        return TransferReceived(**self._base(state, turn_id), payload=payload)

    def transfer_rejected(self, state: RunState, turn_id: str, transfer_id: str, target: str | None,
                          reason: TransferRejectReason, snapshot: DirectorySnapshot | None) -> TransferRejected:
        payload = TransferRejectedPayload(
            transfer_id=transfer_id, to_agent=target, reason_code=reason,
            directory=snapshot.directory if snapshot else None, directory_hash=snapshot.hash if snapshot else None)
        return TransferRejected(**self._base(state, turn_id), payload=payload)
```

To keep `turn/events.py` free of `turn/transfer.py` (and avoid a cycle), make `run_transferred` take the fields directly instead of `TransferPlanView`: `transfer_id`, `target_ref`, `release_id`, `to_run_id`, `reason`, `packet_fp` and `snapshot`. Add `run_started(..., origin: RunOrigin | None = None)`.

- [ ] **Step 5: Implement validation** (`turn/transfer.py`)

```python
"""Transfer between agents in M4 (ADR 0021, spec §5.2): validation, packet and the plan the engine runs."""

from dataclasses import dataclass

from agent_core.domain import (
    Agent,
    AgentSelector,
    DirectorySnapshot,
    EntityKind,
    EntityRef,
    Fingerprint,
    Release,
    TransferPacket,
    TransferRejectReason,
    packet_problem,
    transfer_ineligibility,
)
from agent_core.interpreter import TransferRequest
from agent_core.ports import AuthzPort, IdKind, IdSource, RegistryPort
from agent_core.turn.frame import TurnFrame


@dataclass(frozen=True)
class TransferPlan:
    transfer_id: str
    target: Agent
    target_ref: EntityRef
    release: Release
    to_run_id: str
    packet: TransferPacket
    packet_fp: Fingerprint
    snapshot: DirectorySnapshot
    depth: int


class Transferer:
    def __init__(self, registry: RegistryPort, ids: IdSource, authz: AuthzPort | None,
                 max_per_session: int) -> None:
        self._registry, self._ids, self._authz, self._max = registry, ids, authz, max_per_session

    def new_transfer_id(self) -> str:
        return self._ids.new_id(IdKind.transfer)

    def validate(self, frame: TurnFrame, request: TransferRequest,
                 transfer_id: str) -> TransferPlan | TransferRejectReason:
        state = frame.state
        if frame.entry == "start_run" or frame.turn is None:
            return "no_turn"
        depth = (state.origin.depth if state.origin is not None else 0) + 1
        if depth > self._max:
            return "transfer_limit"
        snapshot = request.snapshot
        if snapshot is None or request.target is None or request.target not in snapshot.choices:
            return "not_in_directory"
        try:
            release = self._registry.resolve_release(AgentSelector(id=request.target, alias="prod"), state.principal)
        except KeyError:
            return "no_active_release"
        if self._registry.release_status(release.id) != "active":
            return "no_active_release"
        version = release.entities.get(EntityKind.agent, {}).get(request.target)
        if version is None:
            return "no_active_release"
        target_ref = EntityRef(id=request.target, version=version)
        target = self._registry.get(target_ref, Agent)
        if transfer_ineligibility(target, state.principal, state.subject, state.locale) is not None:
            return "not_eligible"
        if self._authz is not None and not self._authz.authorize_agent(state.principal, target, state.subject).allowed:
            return "not_eligible"
        assert target.accepts is not None  # `transfer_ineligibility` returned None
        if packet_problem(target.accepts, request.slots) is not None:
            return "accepts_mismatch"
        packet = TransferPacket(reason=request.reason, trigger=frame.text_model, slots=dict(request.slots))
        fingerprint = frame.runtime.step.views.project(
            packet.model_dump(mode="json"), "transfer", [], frame.runtime.step.vault).fingerprint
        return TransferPlan(transfer_id=transfer_id, target=target, target_ref=target_ref, release=release,
                            to_run_id=self._ids.new_id(IdKind.run), packet=packet, packet_fp=fingerprint,
                            snapshot=snapshot, depth=depth)
```

Notes:
- `resolve_release` raises `KeyError` for a missing or revoked alias in `PostgresRegistry`. `InMemoryRegistry` raises `KeyError` for a missing alias, but returns a revoked release (with `status="revoked"`); the `release_status` check covers that case.
- The fingerprint uses the same M7 `project(...).fingerprint` that `agent_step` uses. Check its exact signature in `agent_core/views` and keep the packet out of every event.
- `AuthzPort | None`: the engine's `authz` is optional today. Keep it optional and pass `self._authz` only when it is an `AuthzPort`.

- [ ] **Step 6: Wire it into the engine**

`turn/frame.py`: add `transfer_plan: "TransferPlan | None" = None` (use `TYPE_CHECKING` to avoid the cycle).

`turn/config.py`: `max_transfers_per_session: int = 1`.

`turn/closing.py`, `apply_outcome`: before `if outcome.escalation is not None`:

```python
        if outcome.transfer is not None:
            frame.pending_transfer = outcome.transfer
            return
```

(add `pending_transfer: TransferRequest | None = None` to `TurnFrame`).

`turn/engine.py`: build `self._transferer = Transferer(registry, ids, authz if authz is not None else None, self._config.max_transfers_per_session)` in `__init__`, and change `_advance`:

```python
    def _advance(self, frame: TurnFrame) -> None:
        """Step 11: `advance` and its translation (step 12). A transfer is resolved here (ADR 0021)."""
        with frame.meter.stage("flow"):
            outcome = advance(frame.state, self._step_ctx(frame), frame.resume)
            self._closer.apply_outcome(frame, outcome)
        request = frame.pending_transfer
        if request is None:
            return
        frame.pending_transfer = None
        transfer_id = self._transferer.new_transfer_id()
        plan = self._transferer.validate(frame, request, transfer_id)
        if isinstance(plan, str):
            frame.buffer.add(self._events.transfer_rejected(
                frame.state, frame.turn_id, transfer_id, request.target, plan, request.snapshot))
            frame.state = self._follow(frame.state, request.node_id, "rejected")
            frame.resume = NO_RESUME
            self._advance(frame)
            return
        frame.buffer.add(self._events.run_transferred(
            frame.state, frame.turn_id, transfer_id=plan.transfer_id, target_ref=plan.target_ref,
            release_id=plan.release.id, to_run_id=plan.to_run_id, reason=plan.packet.reason,
            packet_fp=plan.packet_fp, snapshot=plan.snapshot))
        self._closer.close_run(frame, Outcome.transferred, "transfer")
        frame.transfer_plan = plan

    def _follow(self, state: RunState, node_id: str, key: str) -> RunState:
        assert state.active_flow is not None
        flow = self._registry.get(state.active_flow.flow, Flow)
        target = next(n for n in flow.nodes if n.id == node_id).next[key]
        return state.model_copy(update={"active_flow": state.active_flow.model_copy(update={"node_id": target})})
```

`close_run` with `closed_by="transfer"` keeps `active_flow` (only `"flow"` clears it), so the lineage shows where the transfer happened. `RunState._coherence` accepts `status="closed"` with `outcome=transferred`.

The rejection path re-enters `_advance` and runs `rejected` (`esc` in the harness). The recursion is bounded because `rejected` cannot lead back to the same `transfer` without a waiting node (G0-04).

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/m04 -q`
Expected: the new validation tests PASS. A successful transfer leaves the run closed and **no target exists yet** (Task 9); any test that asserts a successful transfer belongs in Task 9.

- [ ] **Step 8: Commit**

```bash
git add agent_core/turn tests/m04
git commit -F msg.txt   # feat(m4): validate transfers and follow the rejected branch
```

---

### Task 9: M4 — abrir el run destino en el mismo turno

**Files:**
- Modify: `agent_core/turn/engine.py` (`_finish`, `_process`, `_continue_in_target`, `_new_state`, `start_run`), `turn/results.py` (`agent`)
- Modify: `docs/specs/motor/m04-ciclo-del-turno.md` (§3.1 paso 12b, transferencia), `docs/specs/2026-09-30-transferencia-entre-agentes-design.md` §5.2 (se reconcilia en Task 12)
- Test: `tests/m04/test_transfer.py` (nuevo)

**Interfaces:**
- Consumes: `TransferPlan` (Task 8), `list_runs_by_session` (Task 7), `RunOrigin` (Task 1).
- Produces:
  - `TurnResult.run_id` es el run destino y `TurnResult.agent` el especialista
  - los mensajes del turno son los del origen (por ejemplo, "te comunico con…") seguidos de los del destino
  - el resultado se guarda bajo `(run destino, client_turn_id)`

- [ ] **Step 1: Write the failing tests**

```python
"""Transfer end to end in M4 (ADR 0021, T-TR-01, T-TR-02, T-TR-07, T-TR-08)."""

import pytest

from agent_core.audit import AuditLog
from testing.fakes.storage import SimulatedCrash
from tests.m04.harness import World
from tests.m04.helpers import cmd


def _transfer(w: World):  # noqa: ANN202
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    return w.turn("no reconozco un cargo")


def test_specialist_answers_in_the_same_turn() -> None:
    w = World(chain_factory=AuditLog)
    result = _transfer(w)
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion", "disputas"]
    source, target = runs
    assert source.status == "closed" and source.outcome is not None and source.outcome.value == "transferred"
    assert target.status == "open" and result.run_id == target.run_id
    assert result.agent is not None and result.agent.id == "disputas"
    assert result.messages[-1].text == w.text("t-pedir")  # disputa's first `collect` asks for details
    assert target.slots["problema"].status == "validated"


def test_chains_are_linked_by_hash() -> None:
    w = World(chain_factory=AuditLog)
    _transfer(w)
    source, target = w.session_runs()
    source_events = w.audit.read(source.run_id)
    assert [e.type for e in source_events][-3:] == ["run_transferred", "run_closed", "turn_completed"]
    first = w.audit.read(target.run_id)
    assert [e.type for e in first][:3] == ["run_started", "transfer_received", "turn_started"]
    origin = first[0].payload.origin
    assert origin is not None and origin.from_event_hash == source_events[-1].hash
    assert origin.transfer_id == source_events[-3].payload.transfer_id == first[1].payload.transfer_id
    assert target.origin == origin


def test_retry_after_transfer_returns_the_same_result() -> None:
    w = World(chain_factory=AuditLog)
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    first = w.turn("no reconozco un cargo", client_turn_id="c-1")
    again = w.turn("no reconozco un cargo", client_turn_id="c-1")
    assert again == first and len(w.session_runs()) == 2


def test_crash_on_commit_leaves_nothing() -> None:
    w = World(chain_factory=AuditLog)
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.turn("no reconozco un cargo")
    runs = w.session_runs()
    assert [r.agent.id for r in runs] == ["recepcion"] and runs[0].status == "open"


def test_the_session_keeps_its_principal() -> None:
    w = World(chain_factory=AuditLog)
    _transfer(w)
    source, target = w.session_runs()
    assert target.principal == source.principal and target.subject == source.subject
```

Harness additions:
- `World(chain_factory=...)`: builds the chain as `chain_factory(self.audit)` (`AuditLog(sink)`).
- `session_runs()`: returns `uow.list_runs_by_session(SESSION_ID)` from a fresh UoW.
- `store.inject`: check `InMemoryStore`'s fault API (`take_fault`); it is probably `store.faults.add("on_commit")` or similar.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m04/test_transfer.py -q`
Expected: FAIL (no target run).

- [ ] **Step 3: Extract the run constructor** (`turn/engine.py`)

Move the `RunState(...)` construction out of `start_run` into:

```python
    def _new_state(self, *, run_id: str, session_id: str | None, release: Release, agent_ref: EntityRef,
                   agent: Agent, principal: Principal, on_behalf_of: OnBehalfOf | None,
                   subject: SubjectRef | None, locale: Locale, slots: dict[str, Slot],
                   origin: RunOrigin | None = None, turn_count: int = 1) -> RunState:
        now = self._clock.now()
        conversational = agent.mode == "conversational"
        return RunState(
            run_id=run_id, session_id=session_id, release=release.id, agent=agent_ref, principal=principal,
            on_behalf_of=on_behalf_of, subject=subject, mode=agent.mode, locale=locale, created_at=now,
            last_activity_at=now, inactive_after=now + agent.inactivity_ttl if conversational else None,
            turn_count=turn_count, slots=slots, origin=origin)
```

`start_run` calls it with `run_id=self._ids.new_id(IdKind.run)` and the current values. Run `uv run pytest tests/m04 -q` and check it stays green **before** going on: this is a pure refactor.

- [ ] **Step 4: Continue in the target run**

Change `_finish`:
- do not store the source result when a transfer happened;
- after persisting, continue in the target run.

```python
        result = build_turn_result(frame, saved, self._trace.current(frame.turn_id))
        plan = frame.transfer_plan
        if store_result and frame.client_turn_id is not None and plan is None:
            frame.uow.put_turn_result(saved.run_id, frame.client_turn_id, result)
        if frame.entry == "turn":
            frame.uow.release_turn(saved.run_id, frame.turn_id)
        frame.state = saved
        if plan is not None:
            return self._continue_in_target(frame, result, plan)
        return result
```

```python
    def _continue_in_target(self, frame: TurnFrame, source: TurnResult, plan: TransferPlan) -> TurnResult:
        """ADR 0021: same turn, same session and principal, a new run pinned to the specialist's release."""
        head = frame.uow.last_event(frame.state.run_id)
        assert head is not None and head.hash is not None  # the source turn was just chained
        origin = RunOrigin(kind="transfer", transfer_id=plan.transfer_id, from_run_id=frame.state.run_id,
                           from_agent=frame.state.agent, from_release_id=frame.state.release,
                           from_event_hash=head.hash, depth=plan.depth)
        slots = {name: Slot(value=value, status="validated", source_turn=1)
                 for name, value in plan.packet.slots.items()}
        source_state = frame.state
        target = self._new_state(
            run_id=plan.to_run_id, session_id=source_state.session_id, release=plan.release,
            agent_ref=plan.target_ref, agent=plan.target, principal=source_state.principal,
            on_behalf_of=source_state.on_behalf_of, subject=source_state.subject, locale=source_state.locale,
            slots=slots, origin=origin, turn_count=0)
        prelude = [
            self._events.run_started(target, plan.target_ref, {}, origin=origin),
            self._events.transfer_received(target, frame.turn_id, plan.transfer_id, list(slots), plan.packet_fp),
        ]
        turn = frame.turn
        assert turn is not None
        result = self._process(frame.uow, target, source_state.principal, source_state.on_behalf_of, turn,
                               frame.turn_id, StageMeter(self._clock), prelude=prelude, store_result=False)
        if isinstance(result, EngineError):
            raise result
        merged = result.model_copy(update={"messages": [*source.messages, *result.messages]})
        if turn.client_turn_id is not None:
            frame.uow.put_turn_result(target.run_id, turn.client_turn_id, merged)
        return merged
```

Then change `_process`:
- `_process(..., prelude: Sequence[EngineEvent] = (), store_result: bool = True)`;
- right after `_new_frame`, do `frame.buffer.add(*prelude)` **before** `frame.buffer.reserve_turn_started()`;
- pass `store_result` to every `self._finish(...)` call in `_process`.

Check the event order with the buffer:
- `peek()` puts the filled `turn_started` first (`head = [self._turn_started]`), so `run_started` would come after it.
- Fix it in `EventBuffer`: add a `prelude` list that `peek()` places before `turn_started`. Add `def add_prelude(self, *events)` and use it here.
- Write a unit test in `tests/m04/test_buffer.py`: `test_prelude_goes_before_turn_started`.

`start_run` is not affected: there, `frame.entry == "start_run"`, so `validate` returns `no_turn` (P6).

The reportable attributes of `run_started` for the target: use the same `reportable_attrs` logic as `start_run`. Factor it into `_reportable(principal)` and call it in both places.

`turn/results.py`, `build_turn_result`: add `agent=state.agent`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/m04 tests/m09 -q`
Expected: PASS. `tests/m09` covers `principal_mismatch` against the open run of the session (T-TR-08). If `tests/m09` uses the session in a way that assumes one run, adapt only the assertion, never the gate.

- [ ] **Step 6: Update m04 and commit**

m04 §3.1: new step 12b "transferencia", the atomicity, and that the result is stored under the target run. §4 Invariantes: "solo un run abierto por sesión".

```bash
git add agent_core/turn tests/m04 docs/specs/motor/m04-ciclo-del-turno.md
git commit -F msg.txt   # feat(m4): open the specialist run in the same turn
```

---

### Task 10: M11 — verificar el enlace entre cadenas

**Files:**
- Create: `agent_core/audit/links.py`
- Modify: `agent_core/audit/__init__.py`
- Modify: `docs/specs/motor/m11-auditoria-transcript-replay.md` (§ enlace entre cadenas)
- Test: `tests/m11/test_transfer_links.py` (nuevo)

**Interfaces:**
- Consumes: `RunOrigin` and the events of Task 2; `check_chain` from `agent_core.audit.chain`.
- Produces: `verify_transfer_link(target: RunState, sink: AuditSink) -> list[str]`, que devuelve problemas legibles sin datos (lista vacía si el enlace es válido)

- [ ] **Step 1: Write the failing tests**

```python
"""Hash link between the origin and target chains of a transfer (ADR 0021 D9, Review Focus 4)."""

from agent_core.audit import AuditLog, verify_transfer_link
from tests.m04.harness import World
from tests.m04.helpers import cmd


def _world() -> World:
    w = World(chain_factory=AuditLog)
    w.reception()
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn("no reconozco un cargo")
    return w


def test_a_fresh_transfer_verifies() -> None:
    w = _world()
    assert verify_transfer_link(w.session_runs()[1], w.audit) == []


def test_later_events_on_the_origin_chain_do_not_break_the_link() -> None:
    w = _world()
    source = w.session_runs()[0]
    w.append_standalone(source.run_id, "access_denied")  # a later denied read on the closed run
    assert verify_transfer_link(w.session_runs()[1], w.audit) == []


def test_a_tampered_origin_breaks_the_link() -> None:
    w = _world()
    source = w.session_runs()[0]
    w.tamper(source.run_id, index=-3)  # rewrites `run_transferred` without fixing the hashes
    assert verify_transfer_link(w.session_runs()[1], w.audit) != []


def test_a_run_without_origin_has_nothing_to_verify() -> None:
    w = _world()
    assert verify_transfer_link(w.session_runs()[0], w.audit) == []
```

Harness:
- `append_standalone(run_id, kind)`: `AuditLog(w.audit, w.store.uow).append_standalone(run_id, [make_event(kind)...])` with a valid `access_denied` payload. Copy one from `tests/m09`.
- `tamper(run_id, index)`: rewrites the stored event's payload directly in `w.store.events[run_id]`. It is only for the test.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m11/test_transfer_links.py -q`
Expected: FAIL (`verify_transfer_link` does not exist).

- [ ] **Step 3: Implement** (`agent_core/audit/links.py`)

```python
"""Verifies the hash link a transfer leaves between two chains (ADR 0021 D9, P2)."""

from agent_core.audit.chain import check_chain
from agent_core.domain import RunState, RunTransferred
from agent_core.ports import AuditSink


def verify_transfer_link(target: RunState, sink: AuditSink) -> list[str]:
    origin = target.origin
    if origin is None:
        return []
    problems: list[str] = []
    source_events = sink.read(origin.from_run_id)
    if not check_chain(origin.from_run_id, source_events).ok:
        problems.append("la cadena de origen no verifica")
    at = next((i for i, e in enumerate(source_events) if e.hash == origin.from_event_hash), None)
    if at is None:
        return [*problems, "el hash de origen no está en la cadena de origen"]
    transferred = [e for e in source_events[: at + 1] if isinstance(e, RunTransferred)
                   and e.payload.transfer_id == origin.transfer_id]
    if len(transferred) != 1 or transferred[0].payload.to_run_id != target.run_id:
        problems.append("no hay un run_transferred que apunte a este run")
    target_events = sink.read(target.run_id)
    if not target_events or target_events[0].type != "run_started" or target_events[0].payload.origin != origin:
        problems.append("el run_started del destino no lleva este origen")
    return problems
```

`ChainCheck` (`audit/chain.py`) exposes `ok`, `broken_at` and `reason`. Export `verify_transfer_link` from `agent_core/audit/__init__.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/m11 -q`
Expected: PASS.

- [ ] **Step 5: Update m11 and commit**

```bash
git add agent_core/audit tests/m11 tests/m04/harness.py docs/specs/motor/m11-auditoria-transcript-replay.md
git commit -F msg.txt   # feat(m11): verify the hash link between transfer chains
```

---

### Task 11: M9 — linaje de la sesión

**Files:**
- Modify: `agent_core/api/app.py` (ruta nueva), `agent_core/api/schemas.py` (`session_lineage`)
- Modify: `docs/specs/motor/m09-acceso-y-api.md` (tabla de rutas y §3.6 lectura)
- Test: `tests/m09/test_session_lineage.py` (nuevo); regenerar `contracts/` (OpenAPI)

**Interfaces:**
- Consumes: `list_runs_by_session` (Task 7), `RunState.origin` (Task 2), `authorizer.authorize_read`.
- Produces: `GET /v1/sessions/{session_id}/lineage` devuelve `{"session_id", "runs": [{"run_id", "agent", "release", "status", "outcome", "origin": {"transfer_id", "from_run_id", "from_agent", "from_release_id"} | null}], "trace_id"}`

- [ ] **Step 1: Write the failing tests**

Follow the pattern of the existing session tests in `tests/m09` (look for the one testing `post_turn`; it builds the app with `TestClient` and signed demo credentials).

```python
def test_owner_reads_the_session_lineage(client_with_transfer) -> None:  # fixture: session with 2 runs
    client, session_id, credential = client_with_transfer
    response = client.get(f"/v1/sessions/{session_id}/lineage", headers={"Authorization": credential})
    assert response.status_code == 200
    body = response.json()
    assert [r["agent"] for r in body["runs"]] == ["recepcion@1.0.0", "disputas@1.0.0"]
    assert body["runs"][0]["origin"] is None
    assert body["runs"][1]["origin"]["from_run_id"] == body["runs"][0]["run_id"]
    assert "from_event_hash" not in body["runs"][1]["origin"]


def test_another_customer_cannot_read_it(client_with_transfer, other_customer_credential) -> None:
    client, session_id, _ = client_with_transfer
    response = client.get(f"/v1/sessions/{session_id}/lineage",
                          headers={"Authorization": other_customer_credential})
    assert response.status_code == 403
```

Build the `client_with_transfer` fixture by saving two runs of one session directly in the store: the first closed as `transferred`, the second open with `origin`. This test is about the API, not M4.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m09/test_session_lineage.py -q`
Expected: FAIL with 404 (no route).

- [ ] **Step 3: Implement**

`api/schemas.py`:

```python
def session_lineage(session_id: str, runs: list[RunState], trace_id: str) -> dict[str, Any]:
    """`GET /v1/sessions/{id}/lineage`: runs in order with their release and how each one started (ADR 0021)."""
    def origin(run: RunState) -> dict[str, Any] | None:
        if run.origin is None:
            return None
        return {"transfer_id": run.origin.transfer_id, "from_run_id": run.origin.from_run_id,
                "from_agent": str(run.origin.from_agent), "from_release_id": run.origin.from_release_id}
    return {
        "session_id": session_id,
        "runs": [{"run_id": r.run_id, "agent": str(r.agent), "release": r.release, "status": r.status,
                  "outcome": to_jsonable(r.outcome), "origin": origin(r)} for r in runs],
        "trace_id": trace_id,
    }
```

`api/app.py`, next to `get_run`:

```python
    @router.get("/sessions/{session_id}/lineage", summary="Linaje de la sesión", operation_id="get_session_lineage")
    def get_session_lineage(request: Request, session_id: str, authorization: Authorization = None,
                            on_behalf_of: OnBehalfOfHeader = None) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of, session_id)  # owner of the open run
        if admitted.run is None:
            raise EngineError(ProblemCode.not_found, "sesión")
        with deps.uow_factory() as uow:
            runs = uow.list_runs_by_session(session_id)
        for run in runs:
            authorizer.authorize_read(admitted, run, trace_id=request_trace_id(request))
        return JsonResponse(session_lineage(session_id, runs, request_trace_id(request)))
```

`from_event_hash` is not published: it is an integrity datum that `verify_transfer_link` uses internally, not something the app needs.

- [ ] **Step 4: Regenerate contracts and run the tests**

Run: `uv run agentcore contracts`, then `uv run pytest tests/m09 tests/m00 -q`
Expected: PASS.

- [ ] **Step 5: Update m09 and commit**

```bash
git add agent_core/api contracts tests/m09 docs/specs/motor/m09-acceso-y-api.md
git commit -F msg.txt   # feat(m9): session lineage endpoint
```

---

### Task 12: Prueba de punta a punta y reconciliación de documentos

**Files:**
- Test: `tests/m04/test_transfer_e2e.py` (nuevo)
- Modify: `docs/specs/2026-09-30-transferencia-entre-agentes-design.md`, `docs/adr/0021-transferencia-entre-agentes.md` (estado), `docs/specs/motor/00-indice.md` (eventos y emisores; tabla de temas), `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` (tema nuevo #19 con los abiertos que quedan)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the end-to-end test**

A reception flow **without seeded facts**:
- `collect` → `tool directory/list` (through `DirectoryToolExecutor` over `InMemoryDirectory`) → `decide` with `choices_from` (`ScriptedDecision.decide_choice` answers `disputas` above threshold) → `transfer`;
- `disputa` continues in the target.

Assert:
- two runs;
- `verify_transfer_link == []`;
- `run_transferred.payload.candidates == ["disputas"]` and its `directory_hash` equals the tool result's `hash`;
- `decision_made` was not emitted by the scripted decision (it emits none), so check `choice_calls` instead.

For this test, `FakeRuntimeFactory` wraps `w.step_tools` with `DirectoryToolExecutor(w.tools, InMemoryDirectory(w.registry), AllowAllAuthz(), w.ids)`. Add a `World(directory=True)` switch.

- [ ] **Step 2: Run the whole suite**

Run: `uv run pytest -q`, `uv run lint-imports`, `uv run mypy`, `uv run ruff check .` and `uv run agentcore contracts --check`.
Expected: all green.

- [ ] **Step 3: Reconcile the spec with P1–P7**

In the transfer spec:
- §3.4: remove `directory_read` and move its fields into `run_transferred` and `transfer_rejected`; add `no_turn` to the reasons (P1, P6).
- §3.3 and §8: `from_event_hash` is the hash of the last event of the origin chain in the transfer turn (P2).
- §3.2: `branch_on: choice`, `choices_from` points to a list of strings, `"none"` is added by the engine, and the `"*"` threshold (P3).
- §4: the tool takes `locale` as an argument; `directory/list` lives in `composition` (abierto 5, closed).
- §5.2: the target needs `understand` and reuses the `turn_id` (P4); `max_transfers_per_session` (P5).
- §7: `engine.directory_read` leaves the DSL catalog; the other three stay.
- §12: mark abiertos 1 and 5 as closed by P3 and Task 4; keep 2, 3, 4 and 6.
- §11: phases 1–6 done; 7 and 8 pending, each with its own plan.

ADR 0021: the state becomes "aceptado (2026-09-30); implementado: fases 1–6 sin spans OTel".

Index:
- §6: add the three events with emitter M4.
- §10: one row "Transferencia entre agentes (ADR 0021)" with its state.

TEMAS: #19 "Transferencia entre agentes: pendientes". It lists REL-T1, evaluation §7, the demo agents, OTel spans, session replay, the return to reception and the step-up specialists.

- [ ] **Step 4: Commit**

```bash
git add tests/m04/test_transfer_e2e.py tests/m04/harness.py docs
git commit -F msg.txt   # test(transfer): end to end; docs reconciled with the implementation decisions
```

---

## Self-review

- **Cobertura de la spec:**

| Spec | Tarea |
|---|---|
| §3.1 | 1 |
| §3.2–§3.4 | 2 (y 3 para `choices_from`) |
| §4 | 4 |
| §5.1–§5.2 | 6, 8 y 9 |
| §5.3 sesión y linaje | 7 y 11 |
| §6 | 3, salvo REL-T1 (P7) |
| §7 | fuera (P7) |
| §8 | 9 y 10, sin spans (P7) |
| §9 fallas | 8 (rechazos), 9 (atomicidad), 4 (directorio caído: `error` de la tool sigue la rama del flow) |
| §10 T-TR-01..15 | 01, 02, 07 y 08 → 9; 03–05 → 8; 06 → 4; 09 → 10 (enlace; el replay completo queda fuera); 10 → 11; 11 → 3; 12–14 → fuera; 15 → 8 |

- **Tipos consistentes:**
  - `TransferRequest` (6), `TransferPlan` (8) y `RunOrigin` (1) tienen los mismos campos en todas las tareas.
  - `decide_choice` tiene la misma firma en las tareas 5 y 6.
  - `DirectorySnapshot.choices` lo producen las tareas 1 y 4 y lo consume la 6.
- **Review Focus:** cada punto tiene su prueba en la tarea indicada.
