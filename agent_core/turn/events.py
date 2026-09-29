"""Fábrica de los eventos que emite M4 (m04 §6). Sin texto del usuario ni secretos en ningún payload.

Todo instante sale del `Clock` y todo `event_id` del `IdSource` (regla dura 2)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from agent_core.domain import (
    Awaiting,
    Command,
    CommandEmitted,
    CommandEmittedPayload,
    EntityRef,
    ExpiryEvaluated,
    ExpiryEvaluatedPayload,
    GuardsOutput,
    Outcome,
    RunClosed,
    RunClosedPayload,
    RunStarted,
    RunStartedPayload,
    RunState,
    TurnCompleted,
    TurnCompletedPayload,
    TurnStages,
    TurnStarted,
    TurnStartedPayload,
)
from agent_core.ports import Clock, IdKind, IdSource

ClosedBy = Literal["flow", "abandonment", "escalation", "revocation"]


@dataclass(frozen=True)
class CommandInfo:
    """Lo que `command_emitted` necesita de una decisión de comando; lo construye quien llama."""

    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = field(default_factory=list)
    above_threshold: dict[str, bool] = field(default_factory=dict)
    decision_id: str | None = None


class TurnEvents:
    def __init__(self, ids: IdSource, clock: Clock) -> None:
        self._ids = ids
        self._clock = clock

    def _base(self, state: RunState, turn_id: str | None) -> dict[str, Any]:
        return {
            "event_id": self._ids.new_id(IdKind.event),
            "run_id": state.run_id,
            "turn_id": turn_id,
            "session_id": state.session_id,
            "release": state.release,
            "ts": self._clock.now(),
        }

    def run_started(self, state: RunState, agent: EntityRef, reportable_attrs: dict[str, str]) -> RunStarted:
        payload = RunStartedPayload(
            agent=agent,
            mode=state.mode,
            subject_kind=state.subject.kind if state.subject is not None else None,
            principal_type=state.principal.type,
            locale=state.locale,
            reportable_attrs=dict(reportable_attrs),
        )
        return RunStarted(**self._base(state, None), payload=payload)

    def turn_started(
        self, state: RunState, turn_id: str, client_turn_id: str | None, *, guards: GuardsOutput | None
    ) -> TurnStarted:
        payload = TurnStartedPayload(client_turn_id=client_turn_id, guards=guards)
        return TurnStarted(**self._base(state, turn_id), payload=payload)

    def command_emitted(
        self, state: RunState, turn_id: str, info: CommandInfo, *, source: Literal["understand", "button"]
    ) -> CommandEmitted:
        """Con `source="button"` el botón no pasa por Understand: sin umbrales ni `decision_id`."""
        button = source == "button"
        payload = CommandEmittedPayload(
            command=info.command,
            flow=info.flow,
            interrupt=info.interrupt,
            additional_flows=list(info.additional_flows),
            above_threshold={} if button else dict(info.above_threshold),
            decision_id=None if button else info.decision_id,
            source=source,
        )
        return CommandEmitted(**self._base(state, turn_id), payload=payload)

    def expiry_evaluated(
        self, state: RunState, turn_id: str | None, now: datetime, ttl: timedelta, *, expired: bool
    ) -> ExpiryEvaluated:
        payload = ExpiryEvaluatedPayload(
            now=now, last_activity_at=state.last_activity_at, ttl=ttl, expired=expired
        )
        return ExpiryEvaluated(**self._base(state, turn_id), payload=payload)

    def turn_completed(
        self,
        state: RunState,
        turn_id: str,
        entry: Literal["start_run", "turn"],
        client_turn_id: str | None,
        *,
        duration_ms: int,
        stages: TurnStages,
        degraded: bool,
        awaiting: Awaiting,
    ) -> TurnCompleted:
        payload = TurnCompletedPayload(
            client_turn_id=client_turn_id,
            entry=entry,
            duration_ms=duration_ms,
            stages=stages,
            degraded=degraded,
            awaiting=awaiting,
        )
        return TurnCompleted(**self._base(state, turn_id), payload=payload)

    def run_closed(
        self, state: RunState, turn_id: str | None, outcome: Outcome, closed_by: ClosedBy
    ) -> RunClosed:
        payload = RunClosedPayload(outcome=outcome, closed_by=closed_by)
        return RunClosed(**self._base(state, turn_id), payload=payload)
