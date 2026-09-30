"""`EngineRunner` del replay `fixture` (M11 §3.4): el motor real con los puertos grabados.

M2, M3, M4, M6, M7, M8 y M10 son los reales (`build_turn_engine`); las tools, el LLM, el reloj y los IDs
salen de `RecordedPorts`, y las decisiones de M5 de los `decision_made` grabados. Herramienta de desarrollo:
usa el almacén en memoria de `testing`."""

from pathlib import Path

from agent_core.audit import RecordedPorts, ReplayCase
from agent_core.composition import EngineDeps, build_turn_engine
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import AgentSelector, EngineEvent, EntityRef, Release, ToolDef
from agent_core.ports import IdKind, IdSource
from agent_core.views import FieldClassifier
from testing.engine_world import CATALOG, RELEASE_ID, Driver, SyntheticAuthz, principal_at
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.registry_dir import registry_from_directory
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript
from testing.replay.recorded import decisions_of, providers_from, synthesized_thresholds

# Los IDs que aparecen en la cadena de eventos se reparten como se grabaron. Los demás (`fact`, `message`) no
# viajan en ningún evento: un borrador grabado cita `fact-0007` y `RecordedIds` inventaría otro nombre.
_RECORDED_KINDS = frozenset({IdKind.event, IdKind.run, IdKind.session, IdKind.turn, IdKind.call,
                             IdKind.decision, IdKind.action, IdKind.handoff})


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


class RecordedEngineRunner:
    def __init__(self, registry_root: Path) -> None:
        self._registry: InMemoryRegistry = registry_from_directory(registry_root, RELEASE_ID)
        self._release: Release = self._registry.resolve_release(
            AgentSelector(id="atencion", alias="prod"), principal_at("step_up"))

    def definitions(self, tool: EntityRef) -> ToolDef:
        """Definiciones de tools para `RecordedToolExecutor` (las lee del registro de autoría)."""
        return self._registry.get(tool, ToolDef)

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        if case.release != self._release.id:
            raise ValueError(f"el registro solo tiene la release {self._release.id}, no {case.release}")
        decisions = decisions_of(list(case.recorded))
        store = InMemoryStore()
        audit = InMemoryAuditSink(store)
        thresholds = synthesized_thresholds(decisions, "cal-demo")
        deps = EngineDeps(
            clock=ports.clock, ids=_ReplayIds(ports.ids), keys=FakeKeyProvider.default(),
            uow_factory=store.uow, audit=audit, registry=self._registry, releases=lambda _: self._release,
            tools=ports.tools, gateway=ports.llm, providers=dict(providers_from(decisions)),
            calibrations=InMemoryCalibrationSource({"cal-demo": thresholds}),
            transcript=InMemoryTranscript(), authz=SyntheticAuthz(), classifier=FieldClassifier(CATALOG))
        driver = Driver(build_turn_engine(deps))
        turn_ids = [e.turn_id for e in ports.readings.events_of("turn_started")]
        for index, op in enumerate(case.inputs):
            # El alta del run lleva el instante de `run_started`; cada turno, el de su `turn_started`.
            ports.clock.enter_turn(None if op["op"] == "start" else turn_ids[index])
            driver.apply(op)
        assert driver.run_id is not None
        return audit.read(driver.run_id)
