"""`TurnTelemetry` over `agent_telemetry` (ADR 0003 #4, M11 §3.2, m04 §3.9).

One live `invoke_agent` per turn, under the current context (the request's `agentcore.api.request` when M9
serves the turn). It takes no Clock and no IdSource: span ids and times come from the OTel SDK; the derived
children (T4) take theirs from the events. Without a provider every span is a no-op, but `bind` still
correlates the turn's logs and the gateway's `chat` with `run_id`, `turn_id` and the release."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from opentelemetry.trace import Span

import agent_telemetry as tel
from agent_core.domain import EngineError, EngineEvent
from agent_core.turn import NO_SPAN, TransferSpan, TurnScope, TurnSpan


class _OtelTurnSpan:
    def __init__(self, active: Span, scope: TurnScope) -> None:
        self._active, self._scope = active, scope

    def record(self, events: Sequence[EngineEvent]) -> None:
        return None  # the derived children `decide`, `rule` and `execute_tool` come in T4

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        with NO_SPAN.transfer(transfer_id) as span:  # the `agentcore.transfer` span comes in T5
            yield span


class OtelTurnTelemetry:
    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        with (
            tel.bind(run_id=scope.run_id, turn_id=scope.turn_id, session_id=scope.session_id,
                     release=scope.release, agent=str(scope.agent)),
            tel.span(tel.INVOKE_AGENT, attributes={
                "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": scope.agent.id,
                "agentcore.entry": scope.entry, "agentcore.principal_type": scope.principal_type,
                "agentcore.locale": scope.locale}) as active,
        ):
            try:
                yield _OtelTurnSpan(active, scope)
            except EngineError as exc:  # the code only: the detail may carry run data (rule 6)
                tel.set_attributes(active, {"agentcore.problem_code": exc.code.value})
                raise
