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
from agent_core.audit.transcript import RunNotFound
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
    La implementa el cableado (`testing.replay`): `audit` no puede importar `turn` ni `interpreter`.
    Debe lanzar `ReplayDesync` (lo hacen los puertos grabados) y no usar más puertos que `RecordedPorts`.

    Returns the events of every run of the session, run by run in creation order: the replayed run first,
    then each run it transferred to (ADR 0021). `case.recorded` has the same shape (`events + linked`)."""

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]: ...


def _by_run(events: list[EngineEvent]) -> list[tuple[str, list[EngineEvent]]]:
    """`[(run_id, events)]` in order of first appearance (the linked runs, in creation order)."""
    runs: dict[str, list[EngineEvent]] = {}
    for event in events:
        runs.setdefault(event.run_id, []).append(event)
    return list(runs.items())


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
        events, linked, inputs, full, drafts, run_id, release = self._resolve(source, mode)
        chain = check_chain(run_id, events)
        if not chain.ok:
            return self._report(mode, run_id, release, start, "chain_broken", chain_broken_at=chain.broken_at)
        for linked_run, chain_events in _by_run(linked):
            check = check_chain(linked_run, chain_events)
            if not check.ok:
                return self._report(mode, run_id, release, start, "chain_broken",
                                    chain_broken_at=check.broken_at)
        recorded = [*events, *linked]
        ports = build_ports(recorded, mode, full=full, drafts=drafts, definitions=self._definitions)
        case = ReplayCase(run_id, release, mode, inputs, recorded)
        try:
            produced = self._engine.run(case, ports)
        except ReplayDesync as desync:
            div = Divergence(event_seq=desync.event_seq, expected=desync.expected, actual=desync.actual)
            return self._report(mode, run_id, release, start, "diverged", first_divergence=div)
        found = first_divergence(recorded, produced)
        return self._report(mode, run_id, release, start, "match" if found is None else "diverged",
                            first_divergence=found)

    def _resolve(
        self, source: Fixture | str, mode: ReplayMode,
    ) -> tuple[list[EngineEvent], list[EngineEvent], list[dict[str, JsonValue]], FullSource, list[JsonValue],
               str, str]:
        """`(events, linked, inputs, full, drafts, run_id, release)`. A `run_id` replay does not follow the
        session yet (`linked` is empty): M11 decision 24, pending."""
        if isinstance(source, Fixture):
            full: FullSource = FixtureFullSource(source.full) if mode == "fixture" else ForbiddenFullSource()
            fixture_drafts = source.drafts if mode == "fixture" else []
            recorded: list[EngineEvent] = list(source.events)
            linked: list[EngineEvent] = list(source.linked)
            return recorded, linked, source.inputs, full, fixture_drafts, source.run_id, source.release
        if mode == "fixture":
            raise ValueError("el modo fixture necesita un Fixture, no un run_id")
        if self._audit is None:
            raise ValueError("replay por run_id necesita un AuditSink")
        events = self._audit.read(source)
        if not events:
            raise RunNotFound(source)  # ni eventos ni release: no hay nada que reproducir
        release = events[0].release
        entries = self._transcript.read(source) if self._transcript is not None else []
        inputs: list[dict[str, JsonValue]] = [
            {"turn_id": e.turn_id, "text_model": e.text_model} for e in entries if e.role == "user"]
        # Solo el texto final de un turno con `response_emitted` generado y sin respaldo sale del LLM;
        # las plantillas y los textos de respaldo no. Los borradores rechazados siempre salieron del LLM.
        emitted: dict[str | None, bool] = {}
        for e in events:
            if getattr(e, "type", None) == "response_emitted":  # gana el último de cada turno
                payload = e.payload  # type: ignore[attr-defined]
                emitted[e.turn_id] = payload.kind == "generated" and not payload.fallback_used
        llm_final = {turn for turn, from_llm in emitted.items() if from_llm}
        drafts: list[JsonValue] = [
            e.text_model for e in entries
            if e.role == "rejected_draft" or (e.role == "assistant" and e.turn_id in llm_final)]
        return events, [], inputs, ForbiddenFullSource(), drafts, source, release

    def _report(self, mode: ReplayMode, run_id: str, release: str, start: int,
                verdict: Literal["match", "diverged", "chain_broken"], *,
                first_divergence: Divergence | None = None,
                chain_broken_at: int | None = None) -> ReplayReport:
        elapsed = (self._clock.monotonic_ns() - start) // 1_000_000
        return ReplayReport(mode=mode, run_id=run_id, release=release, verdict=verdict,
                            first_divergence=first_divergence, chain_broken_at=chain_broken_at,
                            duration_ms=elapsed)
