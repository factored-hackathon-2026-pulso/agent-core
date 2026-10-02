"""`EngineRunner` del replay `fixture` (M11 §3.4): el motor real con los puertos grabados.

M2, M3, M4, M6, M7, M8 y M10 son los reales (`build_turn_engine`); las tools, el LLM, el reloj y los IDs
salen de `RecordedPorts`, y las decisiones de M5 de los `decision_made` grabados. Herramienta de desarrollo:
usa el almacén en memoria de `testing`."""

from pathlib import Path

from agent_core.audit import RecordedPorts, ReplayCase
from agent_core.composition import EngineDeps, build_turn_engine
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import (
    AgentSelector,
    DecisionModelDef,
    EngineEvent,
    EntityKind,
    EntityRef,
    JsonValue,
    RunStarted,
    ToolDef,
)
from agent_core.flows import load_registry
from agent_core.ports import IdKind, IdSource, KeyProvider
from agent_core.views import FieldClassifier
from testing.engine_world import CATALOG, Driver, SyntheticAuthz, principal_at
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.registry_dir import registry_from_releases
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript
from testing.replay.recorded import decisions_of, providers_from, synthesized_thresholds

# Los IDs que aparecen en la cadena de eventos se reparten como se grabaron. Los demás (`fact`, `message`) no
# viajan en ningún evento: un borrador grabado cita `fact-0007` y `RecordedIds` inventaría otro nombre.
# `transfer` sí viaja (`run_transferred`, `transfer_rejected`, `origin` del destino): ADR 0021.
_RECORDED_KINDS = frozenset({IdKind.event, IdKind.run, IdKind.session, IdKind.turn, IdKind.call,
                             IdKind.decision, IdKind.action, IdKind.handoff, IdKind.transfer})


class _ReplayIds:
    """`IdSource` del replay `fixture`: grabados donde el registro los tiene, y la secuencia determinista
    de `FakeIds` (con que se graban los fixtures) para el resto y para los secretos."""

    def __init__(self, recorded: IdSource) -> None:
        self._recorded = recorded
        self._fake = FakeIds()

    def new_id(self, kind: IdKind) -> str:
        return (self._recorded if kind in _RECORDED_KINDS else self._fake).new_id(kind)

    def secret_token(self) -> str:
        return self._fake.secret_token()


def _client_turn_ids(turn_started: list[EngineEvent]) -> dict[str, str]:
    """`turn_id -> client_turn_id` de los turnos grabados que lo traen (el alta del run no)."""
    found: dict[str, str] = {}
    for event in turn_started:
        client_turn_id = event.payload.client_turn_id  # type: ignore[attr-defined]
        if client_turn_id is not None and event.turn_id is not None:
            found.setdefault(event.turn_id, client_turn_id)
    return found


def _with_recorded(op: dict[str, JsonValue], started: RunStarted, turn_id: str | None,
                   client_ids: dict[str, str]) -> dict[str, JsonValue]:
    """Completa una operación reconstruida de un almacén de auditoría con lo que la cadena sí registra: el
    tipo de sujeto (`ref` no viaja en ningún evento y ningún evento depende de él) y el `client_turn_id`."""
    completed = dict(op)
    if op["op"] == "start":
        if "subject" not in op and started.payload.subject_kind is not None:
            completed["subject"] = {"kind": started.payload.subject_kind, "ref": "replay-subject"}
    elif "client_turn_id" not in op and turn_id is not None and turn_id in client_ids:
        completed["client_turn_id"] = client_ids[turn_id]
    return completed


class RecordedEngineRunner:
    """Every release of the authoring directory, pinned: a session that transfers crosses releases (one per
    agent, ADR 0021 D3). The replayed release is `case.release`; the entry agent comes from the recorded
    `run_started`."""

    def __init__(self, registry_root: Path, *, keys: KeyProvider | None = None,
                 field_classifier: FieldClassifier | None = None) -> None:
        """`keys` y `field_classifier` deben ser los del despliegue que grabó el run: las huellas llevan el
        `kid` de la clave y la vista `audit` depende del catálogo de campos."""
        self._keys = keys
        self._field_classifier = field_classifier
        self._registry: InMemoryRegistry = registry_from_releases(registry_root)
        authoring, _ = load_registry(registry_root)
        # The synthesized thresholds answer under every artifact name a decision model of the registry cites.
        self._threshold_ids = sorted({m.thresholds_from for m in authoring.all(EntityKind.decision_model)
                                      if isinstance(m, DecisionModelDef) and m.thresholds_from})

    def definitions(self, tool: EntityRef) -> ToolDef:
        """Definiciones de tools para `RecordedToolExecutor` (las lee del registro de autoría)."""
        return self._registry.get(tool, ToolDef)

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        """The events of every run of the session, in creation order (the `EngineRunner` contract)."""
        try:
            self._registry.resolve_release_by_id(case.release)
        except KeyError:
            raise ValueError(f"el registro no tiene la release {case.release}") from None
        started = case.recorded[0]
        if not isinstance(started, RunStarted):
            raise ValueError("el fixture no empieza con run_started")
        agent = started.payload.agent.id
        entry = self._registry.resolve_release(AgentSelector(id=agent, alias="prod"), principal_at("step_up"))
        if entry.id != case.release:
            raise ValueError(f"el alias prod de {agent} es la release {entry.id}, no {case.release}")
        decisions = decisions_of(list(case.recorded))
        synthesized = synthesized_thresholds(decisions, "replay")
        calibrations = InMemoryCalibrationSource(
            {name: synthesized.model_copy(update={"run_id": name}) for name in self._threshold_ids})
        store = InMemoryStore()
        audit = InMemoryAuditSink(store)
        deps = EngineDeps(
            clock=ports.clock, ids=_ReplayIds(ports.ids), keys=self._keys or FakeKeyProvider.default(),
            uow_factory=store.uow, audit=audit, registry=self._registry,
            releases=self._registry.resolve_release_by_id, tools=ports.tools, gateway=ports.llm,
            providers=dict(providers_from(decisions)), calibrations=calibrations,
            transcript=InMemoryTranscript(), authz=SyntheticAuthz(),
            classifier=self._field_classifier or FieldClassifier(CATALOG))
        # No `directory`: `directory/list` is served from the record like any other tool (F5).
        driver = Driver(build_turn_engine(deps), agent=agent)
        # One instant per turn: the target of a transfer runs in the origin's turn and repeats its turn id,
        # so the turn ids are deduplicated in order before indexing them by op.
        turn_ids = list(dict.fromkeys(e.turn_id for e in ports.readings.events_of("turn_started")))
        client_ids = _client_turn_ids(ports.readings.events_of("turn_started"))
        for index, op in enumerate(case.inputs):
            # El alta del run lleva el instante de `run_started`; cada turno, el de su `turn_started`.
            ports.clock.enter_turn(None if op["op"] == "start" else turn_ids[index])
            driver.apply(_with_recorded(op, started, turn_ids[index], client_ids))
        assert driver.run_id is not None
        if driver.session_id is None:
            return audit.read(driver.run_id)
        with store.uow() as uow:
            run_ids = [r.run_id for r in uow.list_runs_by_session(driver.session_id)]
        return [event for run_id in run_ids for event in audit.read(run_id)]
