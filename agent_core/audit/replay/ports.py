"""Puertos "grabados" del replay (M11 §3.4): responden desde los registros, nunca llaman nada real."""

import base64
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from agent_core.audit.replay.fixture import FullToolResult
from agent_core.audit.replay.report import ReplayMode
from agent_core.domain import (
    EngineEvent,
    EntityRef,
    JsonValue,
    Locale,
    ToolCalledPayload,
    ToolDef,
    UtcDatetime,
    to_jsonable,
)
from agent_core.ports import GenerationResult, IdKind, ToolCallContext, ToolResult


class ReplayDesync(Exception):
    """El motor pidió algo que el registro no tiene (o no en ese orden): es una divergencia."""

    def __init__(self, event_seq: int | None, expected: JsonValue, actual: JsonValue) -> None:
        super().__init__("el motor se desvió de lo grabado")
        self.event_seq, self.expected, self.actual = event_seq, expected, actual


class FullViewAccessError(RuntimeError):
    """El replay `audit` intentó leer la vista `full` (invariante de M11 §4)."""


class FullSource(Protocol):
    def tool_result(self, call_id: str) -> FullToolResult: ...


class FixtureFullSource:
    def __init__(self, results: Mapping[str, FullToolResult]) -> None:
        self._results = dict(results)

    def tool_result(self, call_id: str) -> FullToolResult:
        try:
            return self._results[call_id]
        except KeyError:
            raise ReplayDesync(None, f"fixture.full[{call_id}]", None) from None


class ForbiddenFullSource:
    def tool_result(self, call_id: str) -> FullToolResult:
        raise FullViewAccessError("el replay `audit` no accede a la vista full")


class RecordedClock:
    """`now()` constante dentro de un turno (decisión 10). `monotonic_ns()` constante: solo mide."""

    def __init__(self, instants: Mapping[str | None, UtcDatetime]) -> None:
        self._instants = dict(instants)
        self._turn: str | None = None
        self._override: UtcDatetime | None = None

    @classmethod
    def from_events(cls, events: list[EngineEvent]) -> "RecordedClock":
        instants: dict[str | None, UtcDatetime] = {}
        for e in events:
            kind = getattr(e, "type", None)
            if kind == "run_started":
                instants.setdefault(None, e.ts)
            elif kind == "turn_started" and e.turn_id is not None:
                instants.setdefault(e.turn_id, e.ts)
        return cls(instants)

    def enter_turn(self, turn_id: str | None) -> None:
        self._turn, self._override = turn_id, None

    def set(self, instant: UtcDatetime) -> None:
        """Instante de un `expiry_evaluated` o de un barrido."""
        self._override = instant

    def now(self) -> UtcDatetime:
        if self._override is not None:
            return self._override
        try:
            return self._instants[self._turn]
        except KeyError:
            raise ReplayDesync(None, f"instante grabado del turno {self._turn}", None) from None

    def monotonic_ns(self) -> int:
        return 0


class RecordedIds:
    """Reparte los IDs grabados, por `IdKind`, en el orden de la cadena (decisión 9)."""

    def __init__(self, by_kind: Mapping[IdKind, list[str]]) -> None:
        self._queues: dict[IdKind, deque[str]] = {k: deque(v) for k, v in by_kind.items()}
        self._fallback: defaultdict[IdKind, int] = defaultdict(int)
        self._tokens = 0

    @classmethod
    def from_events(cls, events: list[EngineEvent]) -> "RecordedIds":
        out: dict[IdKind, list[str]] = defaultdict(list)
        seen: dict[IdKind, set[str]] = defaultdict(set)

        def add(kind: IdKind, value: str | None) -> None:
            if value is not None and value not in seen[kind]:
                seen[kind].add(value)
                out[kind].append(value)

        for e in events:
            add(IdKind.event, e.event_id)
            add(IdKind.run, e.run_id)
            add(IdKind.session, e.session_id)
            add(IdKind.turn, e.turn_id)
            payload = getattr(e, "payload", None)
            for name, kind in (("call_id", IdKind.call), ("readback_call_id", IdKind.call),
                               ("decision_id", IdKind.decision), ("action_id", IdKind.action),
                               ("handoff_ref", IdKind.handoff),
                               ("to_run_id", IdKind.run),          # ADR 0021: the target the engine opens
                               ("transfer_id", IdKind.transfer)):  # transferred / rejected / received
                add(kind, getattr(payload, name, None))
        return cls(out)

    def new_id(self, kind: IdKind) -> str:
        queue = self._queues.get(kind)
        if queue:
            return queue.popleft()
        self._fallback[kind] += 1
        return f"{kind.value}-replay-{self._fallback[kind]:04d}"

    def secret_token(self) -> str:
        self._tokens += 1
        return base64.urlsafe_b64encode(self._tokens.to_bytes(16, "big")).rstrip(b"=").decode()


type ToolDefinitions = Callable[[EntityRef], ToolDef]


class RecordedToolExecutor:
    """Sirve los `tool_called` grabados en orden. Otra tool, o de más, es un `ReplayDesync`."""

    def __init__(self, events: list[EngineEvent], mode: ReplayMode, full: FullSource,
                 definitions: ToolDefinitions) -> None:
        self._calls = deque((e.seq, e.payload) for e in events  # type: ignore[attr-defined]
                            if getattr(e, "type", None) == "tool_called")
        self._mode, self._full, self._definitions = mode, full, definitions

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if not self._calls:
            raise ReplayDesync(None, None, {"tool": to_jsonable(tool)})
        seq, rec = self._calls[0]
        assert isinstance(rec, ToolCalledPayload)
        if rec.tool != tool:
            raise ReplayDesync(seq, {"tool": to_jsonable(rec.tool)}, {"tool": to_jsonable(tool)})
        self._calls.popleft()
        if self._mode == "fixture":
            full = self._full.tool_result(rec.call_id)
            return ToolResult(status=full.status, result_full=full.result_full, source=full.source,
                              call_id=rec.call_id, error=full.error, required_level=full.required_level)
        return ToolResult(status=rec.status, result_full=rec.result, call_id=rec.call_id, error=rec.error)

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._definitions(tool)


class RecordedGateway:
    def __init__(self, outputs: list[JsonValue]) -> None:
        self._outputs = deque(outputs)

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        if not self._outputs:
            raise ReplayDesync(None, None, {"prompt": to_jsonable(prompt)})
        return GenerationResult(output=self._outputs.popleft(), tokens_in=0, tokens_out=0,
                                cost_usd=Decimal(0), model="replay-recorded")


class RecordedReadings:
    """Salidas no deterministas que el motor lee de los registros (guardas, decisiones, comandos,
    resultados de `rule`/`verify`/validador en modo `audit`)."""

    def __init__(self, events: list[EngineEvent]) -> None:
        self._events = list(events)

    def events_of(self, event_type: str, turn_id: str | None = None) -> list[EngineEvent]:
        return [e for e in self._events if getattr(e, "type", None) == event_type
                and (turn_id is None or e.turn_id == turn_id)]


@dataclass(frozen=True)
class RecordedPorts:
    mode: ReplayMode
    clock: RecordedClock
    ids: RecordedIds
    tools: RecordedToolExecutor
    llm: RecordedGateway
    readings: RecordedReadings
    full: FullSource


def build_ports(events: list[EngineEvent], mode: ReplayMode, *, full: FullSource, drafts: list[JsonValue],
                definitions: ToolDefinitions) -> RecordedPorts:
    return RecordedPorts(mode, RecordedClock.from_events(events), RecordedIds.from_events(events),
                         RecordedToolExecutor(events, mode, full, definitions), RecordedGateway(drafts),
                         RecordedReadings(events), full)
