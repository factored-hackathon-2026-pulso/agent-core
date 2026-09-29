"""Replay (M11 §3.4): verify_chain → puertos grabados → motor inyectado → comparación."""

from dataclasses import dataclass
from typing import Literal, Protocol

from agent_core.audit.chain import check_chain
from agent_core.audit.replay.compare import first_divergence
from agent_core.audit.replay.fixture import Fixture
from agent_core.audit.replay.ports import (
    FixtureFullSource,
    ForbiddenFullSource,
    FullSource,
    RecordedPorts,
    ReplayDesync,
    ToolDefinitions,
    build_ports,
)
from agent_core.audit.replay.report import Divergence, ReplayMode, ReplayReport
from agent_core.domain import EngineEvent, EntityRef, JsonValue, ToolDef
from agent_core.ports import AuditSink, Clock, TranscriptStore


@dataclass(frozen=True)
class ReplayCase:
    run_id: str
    release: str
    mode: ReplayMode
    inputs: list[dict[str, JsonValue]]
    recorded: list[EngineEvent]


class EngineRunner(Protocol):
    """Corre M4 + M2 con los puertos grabados y devuelve la secuencia de eventos producida.
    La implementa el cableado (`agent_core.cli`): `audit` no puede importar `turn` ni `interpreter`.
    Debe lanzar `ReplayDesync` (lo hacen los puertos grabados) y no usar más puertos que `RecordedPorts`."""

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]: ...


def _no_definitions(_: EntityRef) -> ToolDef:
    raise KeyError("sin definiciones de tools")


class Replayer:
    def __init__(self, engine: EngineRunner, clock: Clock, *, audit: AuditSink | None = None,
                 transcript: TranscriptStore | None = None,
                 definitions: ToolDefinitions | None = None) -> None:
        self._engine, self._clock, self._audit, self._transcript = engine, clock, audit, transcript
        self._definitions: ToolDefinitions = definitions or _no_definitions

    def replay(self, source: Fixture | str, mode: ReplayMode) -> ReplayReport:
        start = self._clock.monotonic_ns()
        events, inputs, full, drafts, run_id, release = self._resolve(source, mode)
        chain = check_chain(run_id, events)
        if not chain.ok:
            return self._report(mode, run_id, release, start, "chain_broken", chain_broken_at=chain.broken_at)
        ports = build_ports(events, mode, full=full, drafts=drafts, definitions=self._definitions)
        case = ReplayCase(run_id, release, mode, inputs, events)
        try:
            produced = self._engine.run(case, ports)
        except ReplayDesync as desync:
            div = Divergence(event_seq=desync.event_seq, expected=desync.expected, actual=desync.actual)
            return self._report(mode, run_id, release, start, "diverged", first_divergence=div)
        found = first_divergence(events, produced)
        return self._report(mode, run_id, release, start, "match" if found is None else "diverged",
                            first_divergence=found)

    def _resolve(
        self, source: Fixture | str, mode: ReplayMode,
    ) -> tuple[list[EngineEvent], list[dict[str, JsonValue]], FullSource, list[JsonValue], str, str]:
        if isinstance(source, Fixture):
            full: FullSource = FixtureFullSource(source.full) if mode == "fixture" else ForbiddenFullSource()
            fixture_drafts = source.drafts if mode == "fixture" else []
            recorded: list[EngineEvent] = list(source.events)
            return recorded, source.inputs, full, fixture_drafts, source.run_id, source.release
        if mode == "fixture":
            raise ValueError("el modo fixture necesita un Fixture, no un run_id")
        if self._audit is None:
            raise ValueError("replay por run_id necesita un AuditSink")
        events = self._audit.read(source)
        release = events[0].release if events else ""
        entries = self._transcript.read(source) if self._transcript is not None else []
        inputs: list[dict[str, JsonValue]] = [
            {"turn_id": e.turn_id, "text_model": e.text_model} for e in entries if e.role == "user"]
        drafts: list[JsonValue] = [e.text_model for e in entries if e.role != "user"]
        return events, inputs, ForbiddenFullSource(), drafts, source, release

    def _report(self, mode: ReplayMode, run_id: str, release: str, start: int,
                verdict: Literal["match", "diverged", "chain_broken"], *,
                first_divergence: Divergence | None = None,
                chain_broken_at: int | None = None) -> ReplayReport:
        elapsed = (self._clock.monotonic_ns() - start) // 1_000_000
        return ReplayReport(mode=mode, run_id=run_id, release=release, verdict=verdict,
                            first_divergence=first_divergence, chain_broken_at=chain_broken_at,
                            duration_ms=elapsed)
