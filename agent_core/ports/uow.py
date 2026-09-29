from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from types import TracebackType
from typing import Protocol, Self

from agent_core.domain.base import UtcDatetime
from agent_core.domain.events import EngineEvent
from agent_core.domain.identity import PrincipalKey
from agent_core.domain.json import JsonValue
from agent_core.domain.shared import OutboxMessage
from agent_core.domain.state import RunState
from agent_core.domain.turn import RunResult, TurnResult


class UnitOfWork(Protocol):
    """Una instancia = una transacción. Sin `commit()` todo se descarta, salvo el lease de `acquire_turn`."""

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None: ...

    def acquire_turn(self, run_id: str, turn_id: str, now: UtcDatetime, ttl: timedelta) -> None:
        """Visible de inmediato. Otro lease vigente → `TurnInProgress`."""
        ...

    def release_turn(self, run_id: str, turn_id: str) -> None:
        """Se aplica con `commit()`."""
        ...

    def load_run(self, run_id: str) -> RunState | None: ...

    def find_run_by_session(self, session_id: str) -> RunState | None: ...

    def save_run(self, state: RunState, expected_version: int) -> RunState:
        """Versión distinta → `VersionConflict`. Devuelve el estado con `state_version` = esperada + 1."""
        ...

    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None: ...

    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None: ...

    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None:
        """`(hash del body, resultado)`. Solo el hash del body viaja: nunca el body crudo."""
        ...

    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str,
                            result: RunResult) -> None: ...

    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None: ...

    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None: ...

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        """Lote en orden; M11 ya los encadenó."""
        ...

    def last_event(self, run_id: str) -> EngineEvent | None:
        """Para encadenar."""
        ...

    def enqueue_outbox(self, message: OutboxMessage) -> None: ...

    def add_usage(self, principal: PrincipalKey, cost_usd: Decimal, now: UtcDatetime) -> None:
        """Suma `cost_usd` y 1 hit al principal (lo lee `CostCounters`). Lo llama M4."""
        ...

    def list_inactive(self, now: UtcDatetime, limit: int) -> list[str]:
        """run_ids `open` con `inactive_after < now`, ordenados por `inactive_after`."""
        ...

    def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
