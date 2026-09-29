"""UoW, log de auditoría, outbox y contadores de costo en memoria, con fallas inyectables (M0 §10).

Semántica que replica la del adaptador Postgres futuro: aislamiento por copia profunda (quien llama no puede
mutar lo persistido), versión optimista revalidada bajo lock al commitear (sin lost update), lease
inmediato con vencimiento según el `now` que llega por parámetro (este módulo nunca lee la hora),
auditoría solo-append y outbox at-least-once con entrega idempotente. Toda lectura y escritura del estado
compartido (incluidas las fallas inyectables) se hace bajo `store.lock`. M3 usa las fallas inyectables.

Escalabilidad del doble: `load_run`/`find_run_by_session` son O(1); `list_inactive` usa un índice ordenado
(`bisect`) en vez de recorrer todos los runs; `Outbox.pending` recorre solo `limit` mensajes. `read` de
auditoría y `hits` de costos son lineales en lo que devuelven/recorren (suficiente para un doble)."""

import threading
from bisect import bisect_left, insort
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import islice
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Self

from agent_core.domain import (
    EngineEvent,
    JsonValue,
    OutboxMessage,
    PrincipalKey,
    RunResult,
    RunState,
    TurnInProgress,
    TurnResult,
    VersionConflict,
)

FaultPoint = Literal["on_commit", "after_commit"]


class SimulatedCrash(Exception):
    """Caída simulada del proceso en un punto de commit."""


@dataclass
class _Lease:
    turn_id: str
    expires_at: datetime


@dataclass
class InMemoryStore:
    """Estado compartido entre UoWs. `lock` serializa lease, commit y lecturas de auditoría/outbox."""

    runs: dict[str, RunState] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    leases: dict[str, _Lease] = field(default_factory=dict)
    turn_results: dict[tuple[str, str], TurnResult] = field(default_factory=dict)
    idempotency: dict[tuple[PrincipalKey, str], tuple[str, RunResult]] = field(default_factory=dict)
    handoffs: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    events: dict[str, list[EngineEvent]] = field(default_factory=dict)
    outbox_pending: dict[str, OutboxMessage] = field(default_factory=dict)  # orden de inserción = de entrega
    outbox_seen: set[str] = field(default_factory=set)  # ids ya encolados (entregados o no): evita duplicados
    usage: dict[PrincipalKey, list[tuple[datetime, Decimal]]] = field(default_factory=dict)
    inactive_index: list[tuple[datetime, str]] = field(default_factory=list)  # runs open con inactive_after
    faults: deque[FaultPoint] = field(default_factory=deque)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def inject(self, point: FaultPoint, times: int = 1) -> None:
        self.faults.extend([point] * times)

    def take_fault(self, point: FaultPoint) -> bool:
        with self.lock:
            if self.faults and self.faults[0] == point:
                self.faults.popleft()
                return True
            return False

    def uow(self) -> "InMemoryUoW":
        return InMemoryUoW(self)

    def version_of(self, run_id: str) -> int:
        state = self.runs.get(run_id)
        return state.state_version if state else 0

    def reindex_inactive(self, old: RunState | None, new: RunState) -> None:
        if old is not None and (key := _inactive_key(old)) is not None:
            del self.inactive_index[bisect_left(self.inactive_index, key)]
        if (new_key := _inactive_key(new)) is not None:
            insort(self.inactive_index, new_key)


def _inactive_key(state: RunState) -> tuple[datetime, str] | None:
    if state.status == "open" and state.inactive_after is not None:
        return (state.inactive_after, state.run_id)
    return None


class InMemoryUoW:
    """Una instancia = una transacción. Lee sus propias escrituras; `acquire_turn` es inmediato."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self._runs: dict[str, RunState] = {}
        self._base_versions: dict[str, int] = {}  # versión vigente al primer save_run, revalidada en commit
        self._turn_results: dict[tuple[str, str], TurnResult] = {}
        self._idempotency: dict[tuple[PrincipalKey, str], tuple[str, RunResult]] = {}
        self._handoffs: dict[str, dict[str, JsonValue]] = {}
        self._events: dict[str, list[EngineEvent]] = {}
        self._outbox: list[OutboxMessage] = []
        self._usage: list[tuple[PrincipalKey, datetime, Decimal]] = []
        self._released: list[tuple[str, str]] = []
        self._done = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._done = True  # lo no commiteado se descarta

    def _check_open(self) -> None:
        if self._done:
            raise RuntimeError("la UoW ya terminó; abre una nueva")

    def acquire_turn(self, run_id: str, turn_id: str, now: datetime, ttl: timedelta) -> None:
        self._check_open()
        if ttl <= timedelta(0):
            raise ValueError("el TTL del lease debe ser positivo")
        with self._store.lock:
            lease = self._store.leases.get(run_id)
            if lease is not None and lease.turn_id != turn_id and lease.expires_at > now:
                raise TurnInProgress(f"turno {lease.turn_id} en curso en {run_id}")
            self._store.leases[run_id] = _Lease(turn_id, now + ttl)

    def release_turn(self, run_id: str, turn_id: str) -> None:
        self._check_open()
        self._released.append((run_id, turn_id))

    def load_run(self, run_id: str) -> RunState | None:
        with self._store.lock:
            state = self._runs.get(run_id) or self._store.runs.get(run_id)
            return state.model_copy(deep=True) if state is not None else None

    def find_run_by_session(self, session_id: str) -> RunState | None:
        for state in self._runs.values():
            if state.session_id == session_id:
                return state.model_copy(deep=True)
        with self._store.lock:
            run_id = self._store.sessions.get(session_id)
        return self.load_run(run_id) if run_id is not None else None

    def save_run(self, state: RunState, expected_version: int) -> RunState:
        self._check_open()
        local = self._runs.get(state.run_id)
        with self._store.lock:
            current_version = local.state_version if local else self._store.version_of(state.run_id)
        if expected_version != current_version:
            raise VersionConflict(f"{state.run_id}: esperada {expected_version}, vigente {current_version}")
        # Revalida los invariantes y corta todo alias con el llamador.
        validated = RunState.model_validate(state.model_dump())
        saved = validated.model_copy(update={"state_version": expected_version + 1})
        if state.run_id not in self._base_versions:
            self._base_versions[state.run_id] = current_version
        self._runs[state.run_id] = saved
        return saved.model_copy(deep=True)

    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None:
        key = (run_id, client_turn_id)
        with self._store.lock:
            local = self._turn_results.get(key)
            result = local if local is not None else self._store.turn_results.get(key)
            return deepcopy(result)

    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None:
        self._check_open()
        self._turn_results[(run_id, client_turn_id)] = deepcopy(result)

    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None:
        scoped = (principal, key)  # el principal es parte de la clave: nadie repite ni lee runs ajenos
        with self._store.lock:
            found = self._idempotency.get(scoped) or self._store.idempotency.get(scoped)
            return deepcopy(found)

    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str,
                            result: RunResult) -> None:
        self._check_open()
        self._idempotency[(principal, key)] = (body_hash, deepcopy(result))

    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None:
        self._check_open()
        self._handoffs[handoff_ref] = deepcopy(packet)

    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None:
        with self._store.lock:
            found = self._handoffs.get(handoff_ref)
            if found is None:
                found = self._store.handoffs.get(handoff_ref)
            return deepcopy(found)

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        self._check_open()
        self._events.setdefault(run_id, []).extend(deepcopy(events))

    def last_event(self, run_id: str) -> EngineEvent | None:
        pending = self._events.get(run_id)
        if pending:
            return deepcopy(pending[-1])
        with self._store.lock:
            committed = self._store.events.get(run_id)
            return deepcopy(committed[-1]) if committed else None

    def enqueue_outbox(self, message: OutboxMessage) -> None:
        self._check_open()
        self._outbox.append(deepcopy(message))

    def add_usage(self, principal: PrincipalKey, cost_usd: Decimal, now: datetime) -> None:
        self._check_open()
        if not cost_usd.is_finite() or cost_usd < 0:
            raise ValueError("el costo debe ser un Decimal finito y no negativo")
        self._usage.append((principal, now, cost_usd))

    def list_inactive(self, now: datetime, limit: int) -> list[str]:
        if limit <= 0:
            return []
        store = self._store
        # Del índice ordenado basta el prefijo vencido, más margen por los runs que esta UoW reescribió.
        with store.lock:
            end = bisect_left(store.inactive_index, (now, ""))
            window = list(islice(store.inactive_index, min(end, limit + len(self._runs))))
        committed = [(ts, rid) for ts, rid in window if rid not in self._runs]
        local = [key for s in self._runs.values() if (key := _inactive_key(s)) is not None and key[0] < now]
        return [rid for _, rid in sorted(committed + local)[:limit]]

    def commit(self) -> None:
        self._check_open()
        store = self._store
        try:
            if store.take_fault("on_commit"):
                raise SimulatedCrash("caída antes de aplicar el commit")
            with store.lock:
                self._apply(store)
        finally:
            self._done = True
        if store.take_fault("after_commit"):
            raise SimulatedCrash("caída después de aplicar el commit")

    def _apply(self, store: InMemoryStore) -> None:
        # Todo se valida antes de tocar nada: un commit se aplica completo o no se aplica.
        for run_id, base in self._base_versions.items():
            if store.version_of(run_id) != base:
                raise VersionConflict(f"{run_id}: otra transacción commiteó primero (base {base})")
        for run_id, state in self._runs.items():
            store.reindex_inactive(store.runs.get(run_id), state)
            store.runs[run_id] = state
            if state.session_id is not None:
                store.sessions[state.session_id] = run_id
        store.turn_results.update(self._turn_results)
        for scoped, record in self._idempotency.items():
            store.idempotency.setdefault(scoped, record)  # el primer registro gana: un replay no lo pisa
        store.handoffs.update(self._handoffs)
        for run_id, events in self._events.items():
            store.events.setdefault(run_id, []).extend(events)
        for message in self._outbox:
            if message.message_id not in store.outbox_seen:
                store.outbox_seen.add(message.message_id)
                store.outbox_pending[message.message_id] = message
        for principal, at, cost in self._usage:
            store.usage.setdefault(principal, []).append((at, cost))
        for run_id, turn_id in self._released:
            lease = store.leases.get(run_id)
            if lease is not None and lease.turn_id == turn_id:
                del store.leases[run_id]


class InMemoryAuditSink:
    """Solo `read` y `append_outside_turn`: no hay API para modificar ni borrar lo ya escrito."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def read(self, run_id: str) -> list[EngineEvent]:
        with self._store.lock:
            return deepcopy(self._store.events.get(run_id, []))

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None:
        with self._store.lock:
            self._store.events.setdefault(run_id, []).extend(deepcopy(events))


class InMemoryOutbox:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def pending(self, limit: int) -> list[OutboxMessage]:
        with self._store.lock:
            return deepcopy(list(islice(self._store.outbox_pending.values(), max(limit, 0))))

    def mark_delivered(self, message_id: str) -> None:
        with self._store.lock:
            self._store.outbox_pending.pop(message_id, None)  # idempotente; id desconocido: no-op


class InMemoryCostCounters:
    """Lee lo que escribe `UnitOfWork.add_usage`. "Hoy" = día calendario UTC de `now`."""

    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def spent_today(self, principal: PrincipalKey, now: datetime) -> Decimal:
        today = now.astimezone(UTC).date()
        with self._store.lock:
            entries = list(self._store.usage.get(principal, []))
        return sum((c for at, c in entries if at.astimezone(UTC).date() == today), Decimal(0))

    def hits(self, principal: PrincipalKey, window: timedelta, now: datetime) -> int:
        start = now - window
        with self._store.lock:
            entries = list(self._store.usage.get(principal, []))
        return sum(1 for at, _ in entries if start < at <= now)


if TYPE_CHECKING:
    from agent_core.ports import AuditSink, CostCounters, Outbox, UnitOfWork

    def _conforms_uow(x: InMemoryUoW) -> UnitOfWork:
        return x

    def _conforms_audit(x: InMemoryAuditSink) -> AuditSink:
        return x

    def _conforms_outbox(x: InMemoryOutbox) -> Outbox:
        return x

    def _conforms_costs(x: InMemoryCostCounters) -> CostCounters:
        return x
