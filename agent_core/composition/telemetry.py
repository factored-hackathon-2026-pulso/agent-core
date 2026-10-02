"""`TurnTelemetry` over `agent_telemetry` (ADR 0003 #4, M11 §3.2, m04 §3.9).

One live `invoke_agent` per turn, under the current context (the request's `agentcore.api.request` when M9
serves the turn). It takes no Clock and no IdSource: the live span's ids and times come from the OTel SDK;
the derived children `agentcore.decide`, `agentcore.rule` and `execute_tool` take theirs from the events
(`derived_spans`). Without a provider every span is a no-op, but `bind` still correlates the turn's logs and
the gateway's `chat` with `run_id`, `turn_id` and the release."""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from opentelemetry import context as otel_context
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, SpanContext

import agent_telemetry as tel
from agent_core.domain import DecisionMade, EngineError, EngineEvent, RuleEvaluated, ToolCalled
from agent_core.turn import TransferOutcome, TransferSpan, TurnScope, TurnSpan

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class DerivedSpan:
    """A finished child of the turn's `invoke_agent`, built only from an audit event's scalar fields."""

    name: str
    start_ns: int
    end_ns: int
    attributes: Mapping[str, str | int | bool]


def _ns(ts: datetime) -> int:
    """Epoch nanoseconds of an event's `ts` (microsecond precision, exact integer arithmetic)."""
    return (ts - _EPOCH) // timedelta(microseconds=1) * 1_000


def derived_spans(events: Sequence[EngineEvent]) -> list[DerivedSpan]:
    """M11 §3.2 children from the turn's audit events (U4, F13). Pure and read-only: it reads ids, refs, enums
    and counters, never a payload dict (`args`, `result`, `error`, `inputs`, `value`; rule 6), and it never
    writes to an event, whose payload dicts the engine's chain shares (m04 §3.9).

    Times: every emitter takes `ts` after the call (`decision/service.py`, `actions/execution.py`,
    `interpreter/handlers/tool.py` and `agent.py`), so `decide` and `execute_tool` end at `ts` and start
    `latency_ms` earlier; a rule is instantaneous at `ts`. `agent_step`, `knowledge_read` and the rest have no
    child: the gateway's live `chat` already covers the LLM."""
    out: list[DerivedSpan] = []
    for event in events:
        end = _ns(event.ts)
        if isinstance(event, DecisionMade):
            d = event.payload
            out.append(DerivedSpan(tel.DECIDE, end - d.latency_ms * _NS_PER_MS, end, {
                "agentcore.decision.id": d.decision_id, "agentcore.decision.model": str(d.model),
                "agentcore.decision.provider": d.provider_used,
                "agentcore.decision.fallback_depth": d.fallback_depth,
                "agentcore.decision.tokens": d.tokens}))
        elif isinstance(event, RuleEvaluated):
            r = event.payload
            attrs: dict[str, str | int | bool] = {"agentcore.node": r.node_id,
                                                  "agentcore.rule.result": r.result}
            if r.policy is not None:
                attrs["agentcore.rule.policy"] = str(r.policy)
            out.append(DerivedSpan(tel.RULE, end, end, attrs))
        elif isinstance(event, ToolCalled):
            t = event.payload
            out.append(DerivedSpan(tel.EXECUTE_TOOL, end - t.latency_ms * _NS_PER_MS, end, {
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": t.tool.id,
                "gen_ai.tool.call.id": t.call_id, "agentcore.tool": str(t.tool), "agentcore.node": t.node_id,
                "agentcore.tool.status": t.status.value, "agentcore.tool.attempt": t.attempt}))
    return out


@dataclass(frozen=True)
class TransferLink:
    """Opaque to M4: the transfer span's context and the context the origin's `invoke_agent` was opened
    in, so the target's `invoke_agent` is its sibling (F10) and links back to the transfer. Nothing is
    persisted."""

    span_context: SpanContext
    parent: Context


class _OtelTransferSpan:
    def __init__(self, active: Span, parent: Context) -> None:
        self._active = active
        self.link: object | None = TransferLink(active.get_span_context(), parent)

    def finish(self, outcome: TransferOutcome) -> None:
        """Ids and enums only. `to_agent` is whatever M4 echoes (a rejection echoes it only when
        `transfer_rejected` does) and `to_release_id` exists only for a transfer."""
        attrs: dict[str, str] = {"agentcore.transfer.outcome": outcome.outcome}
        if outcome.to_agent is not None:
            attrs["agentcore.transfer.to_agent"] = outcome.to_agent
        if outcome.outcome == "transferred" and outcome.to_release_id is not None:
            attrs["agentcore.transfer.to_release_id"] = outcome.to_release_id
        if outcome.outcome == "rejected" and outcome.reason_code is not None:
            attrs["agentcore.transfer.reason_code"] = outcome.reason_code
        tel.set_attributes(self._active, attrs)


class _OtelTurnSpan:
    def __init__(self, active: Span, scope: TurnScope, parent: Context) -> None:
        self._active, self._scope, self._parent = active, scope, parent

    def record(self, events: Sequence[EngineEvent]) -> None:
        """One finished child per derived span, under this turn's `invoke_agent` and its `bind` context.
        No `try`: `record_span` does not raise outside strict mode, and M4's `observed_turn` contains
        failures."""
        for child in derived_spans(tuple(events)):  # a snapshot: the caller's list is never touched
            tel.record_span(child.name, parent=self._active, start_ns=child.start_ns, end_ns=child.end_ns,
                            attributes=child.attributes)

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        """`agentcore.transfer`, a child of this turn's `invoke_agent` that covers the validation."""
        with tel.span(tel.TRANSFER, attributes={
                "agentcore.transfer.id": transfer_id,
                "agentcore.transfer.from_agent": self._scope.agent.id}) as active:
            yield _OtelTransferSpan(active, self._parent)


class OtelTurnTelemetry:
    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        # The target of a transfer is a sibling of the origin's `invoke_agent` (F10): it opens under the
        # context the origin was opened in, with a span link to the transfer span.
        transfer_links = [link for link in links if isinstance(link, TransferLink)]
        parent = transfer_links[0].parent if transfer_links else otel_context.get_current()
        otel_links = [Link(link.span_context) for link in transfer_links if link.span_context.is_valid]
        with (
            tel.bind(run_id=scope.run_id, turn_id=scope.turn_id, session_id=scope.session_id,
                     release=scope.release, agent=str(scope.agent)),
            tel.span(tel.INVOKE_AGENT, context=parent if transfer_links else None, links=otel_links,
                     attributes={
                         "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": scope.agent.id,
                         "agentcore.entry": scope.entry, "agentcore.principal_type": scope.principal_type,
                         "agentcore.locale": scope.locale}) as active,
        ):
            try:
                yield _OtelTurnSpan(active, scope, parent)
            except EngineError as exc:  # the code only: the detail may carry run data (rule 6)
                tel.set_attributes(active, {"agentcore.problem_code": exc.code.value})
                raise
