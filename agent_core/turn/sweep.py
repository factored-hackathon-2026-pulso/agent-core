"""Barrido de inactividad (m04 §3.6, decisión 12).

Solo necesita `uow_factory`, `registry`, `clock`, `ids`, `actions` y `chain`: así `agentcore sweep` no
arrastra M5, M6 ni M10. Cada run se procesa en su propia UoW y con el lease del turno: un turno vivo
gana al barrido."""

from dataclasses import dataclass
from datetime import datetime

from agent_core.actions import ActionManager
from agent_core.domain import (
    Agent,
    EngineEvent,
    InvalidationReason,
    Outcome,
    TurnInProgress,
)
from agent_core.ports import Clock, IdKind, IdSource, RegistryPort, UnitOfWorkFactory
from agent_core.turn.closing import closed_state
from agent_core.turn.config import TurnConfig
from agent_core.turn.events import TurnEvents
from agent_core.turn.ports import EventChain


@dataclass(frozen=True)
class SweepReport:
    evaluated: int
    abandoned: int
    skipped: int


class Sweeper:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        registry: RegistryPort,
        clock: Clock,
        ids: IdSource,
        actions: ActionManager,
        chain: EventChain,
        config: TurnConfig | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._clock = clock
        self._ids = ids
        self._actions = actions
        self._chain = chain
        self._config = config or TurnConfig()
        self._events = TurnEvents(ids, clock)

    def sweep(self, now: datetime) -> SweepReport:
        """Evalúa en lotes los runs `open` con `inactive_after < now` y abandona los vencidos.

        Los runs saltados (turno vivo) siguen figurando en `list_inactive`: cada pasada pide `limit` más los
        ya vistos para llegar al resto sin volver a procesarlos."""
        seen: list[str] = []
        evaluated = abandoned = skipped = 0
        while True:
            with self._uow_factory() as uow:
                listed = uow.list_inactive(now, self._config.sweep_batch + len(seen))
            fresh = [run_id for run_id in listed if run_id not in seen]
            if not fresh:
                break
            for run_id in fresh[: self._config.sweep_batch]:
                seen.append(run_id)
                outcome = self._sweep_one(run_id, now)
                if outcome == "skipped":
                    skipped += 1
                elif outcome == "abandoned":
                    evaluated += 1
                    abandoned += 1
                elif outcome == "kept":
                    evaluated += 1
        return SweepReport(evaluated=evaluated, abandoned=abandoned, skipped=skipped)

    def _sweep_one(self, run_id: str, now: datetime) -> str:
        """`abandoned`, `kept` (reevaluado y sigue vivo), `skipped` (turno en curso) o `gone`."""
        lease_id = self._ids.new_id(IdKind.turn)
        with self._uow_factory() as uow:
            try:
                uow.acquire_turn(run_id, lease_id, now, self._config.lease_ttl)
            except TurnInProgress:
                return "skipped"
            state = uow.load_run(run_id)
            due = (
                state is not None
                and state.status == "open"
                and state.inactive_after is not None
                and state.inactive_after < now
            )
            if state is None or state.status != "open":  # cerrado mientras tanto: nada que evaluar
                uow.release_turn(run_id, lease_id)
                uow.commit()
                return "gone"
            agent = self._registry.get(state.agent, Agent)
            events: list[EngineEvent] = [
                self._events.expiry_evaluated(state, None, now, agent.inactivity_ttl, expired=due)
            ]
            if due:
                state, invalidated = self._actions.invalidate(
                    state, InvalidationReason.abandoned, turn_id=None
                )
                events.extend(invalidated)
                state = closed_state(state, Outcome.abandoned, "abandonment", now)
                events.append(self._events.run_closed(state, None, Outcome.abandoned, "abandonment"))
                saved = uow.save_run(state, expected_version=state.state_version)
            else:
                saved = state
            self._chain.append(uow, saved.run_id, events)
            uow.release_turn(run_id, lease_id)
            uow.commit()
            return "abandoned" if due else "kept"
