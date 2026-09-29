"""Dobles de M4 (solo datos sintéticos). `FakeRuntime.model_text` marca el texto (fugas de PII)."""

from collections import deque
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from types import TracebackType
from typing import Any

from agent_core.domain import (
    Command,
    EncryptedBlob,
    EngineEvent,
    Fingerprint,
    JsonValue,
    Message,
    RejectedDraft,
    TranscriptRef,
)
from agent_core.guards import GuardResult, InjectionResult, LangDecision
from agent_core.interpreter import StepContext
from agent_core.ports import UnitOfWork, UnitOfWorkFactory
from agent_core.turn import UnderstandOutcome, UnderstandRequest
from testing.fakes.clock import FakeClock

MODEL_MARK = "[model]"


def cmd(
    command: str,
    *,
    flow: str | None = None,
    interrupt: str | None = None,
    additional: tuple[str, ...] = (),
    slots: dict[str, JsonValue] | None = None,
    above: dict[str, bool] | None = None,
    cost: str = "0",
) -> UnderstandOutcome:
    """Comando sintético; por defecto todos los campos calibrados presentes superan el umbral."""
    fields = {
        "command": True,
        **({"flow": True} if flow else {}),
        **({"interrupt": True} if interrupt else {}),
    }
    return UnderstandOutcome(
        command=Command(command),
        flow=flow,
        interrupt=interrupt,
        additional_flows=list(additional),
        slots=slots or {},
        above_threshold={**fields, **(above or {})},
        decision_id="decision-x",
        cost_usd=Decimal(cost),
    )


class ScriptedUnderstand:
    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self._script: deque[UnderstandOutcome] = deque()
        self.calls: list[UnderstandRequest] = []
        self.clock, self.advance = clock, advance
        self.hook: Callable[[], None] | None = None  # se ejecuta al entrar (p. ej. bloquear un hilo)

    def push(self, *outcomes: UnderstandOutcome) -> None:
        self._script.extend(outcomes)

    def run(self, request: UnderstandRequest) -> UnderstandOutcome:
        self.calls.append(request)
        if self.hook is not None:
            self.hook()
        self.clock.advance(self.advance)
        if not self._script:
            raise AssertionError("ScriptedUnderstand sin resultado guionado")
        return self._script.popleft()


def kept(locale: str = "es") -> GuardResult:
    return GuardResult(
        lang=LangDecision(
            decision="kept",
            locale=locale,
            locale_prior=locale,
            letters=20,
            top2=[(locale, 0.99)],
            detector="lingua@2.1.1",
        ),
        size_ok=True,
        injection=InjectionResult(flagged=False, ruleset="none"),
    )


def unsupported(locale: str = "es") -> GuardResult:
    base = kept(locale)
    return base.model_copy(update={"lang": base.lang.model_copy(update={"decision": "unsupported"})})


def too_large(locale: str = "es") -> GuardResult:
    return kept(locale).model_copy(update={"size_ok": False})


def injected(locale: str = "es") -> GuardResult:
    base = kept(locale)
    flagged = InjectionResult(flagged=True, signals=["ignore-previous"], ruleset="rules@1.0.0")
    return base.model_copy(update={"injection": flagged})


class ScriptedGuards:
    """Devuelve `result` (mutable por el test) y registra el texto recibido; `advance` mueve el reloj."""

    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self.result = kept()
        self.events: list[EngineEvent] = []
        self.calls: list[str] = []
        self.contexts: list[dict[str, Any]] = []
        self.clock, self.advance = clock, advance

    def run(
        self,
        text_model_view: str,
        state: Any,
        agent: Any,
        release: Any,
        request_lang: str | None,
        first_turn: bool,
        *,
        turn_id: str | None = None,
    ) -> tuple[GuardResult, list[EngineEvent]]:
        self.calls.append(text_model_view)
        self.contexts.append({"lang": request_lang, "first_turn": first_turn, "turn_id": turn_id})
        self.clock.advance(self.advance)
        return self.result, list(self.events)


class InMemoryTurnRecorder:
    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self.calls: list[tuple[str, str, str, str, list[RejectedDraft]]] = []
        self.clock, self.advance = clock, advance
        self.fail: Exception | None = None

    def record_turn(
        self,
        run_id: str,
        turn_id: str,
        user_msg_model: str,
        final_model: str,
        rejected: list[RejectedDraft],
    ) -> list[TranscriptRef]:
        self.clock.advance(self.advance)
        if self.fail is not None:
            raise self.fail
        self.calls.append((run_id, turn_id, user_msg_model, final_model, rejected))
        fp = Fingerprint(alg="HMAC-SHA256", kid="fp-1", value=f"fp{len(self.calls)}")
        n = len(self.calls)
        return [TranscriptRef(entry_id=f"entry-{n}-{i}", fingerprint=fp) for i in range(2 + len(rejected))]


class PlainChain:
    """Sin hash: solo persiste por la UoW (fase A, sin M11)."""

    def append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> None:
        uow.append_events(run_id, events)


class FakeRuntime:
    """C1: `step` lo arma el `World` con M2/M3 reales; `model_text`/`render` son marcas visibles."""

    def __init__(self, step: StepContext) -> None:
        self.step = step

    def model_text(self, text: str) -> str:
        return f"{MODEL_MARK}{text}"

    def render(self, message: Message) -> Message:
        return message.model_copy(update={"text": message.text.replace(MODEL_MARK, "")})

    def sealed_token_map(self) -> EncryptedBlob | None:
        return None


class FixedTrace:
    def current(self, turn_id: str) -> str:
        return f"trace-{turn_id}"


class CommitCounter:
    """Envuelve una `UnitOfWorkFactory`: cuenta commits por UoW (invariante "una transacción por turno")."""

    def __init__(self, base: UnitOfWorkFactory) -> None:
        self.base, self.commits = base, 0

    def __call__(self) -> Any:
        inner = self.base()
        outer = self

        class _U:
            def __enter__(self) -> "_U":
                inner.__enter__()
                return self

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> None:
                return inner.__exit__(exc_type, exc, tb)

            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

            def commit(self) -> None:
                outer.commits += 1
                inner.commit()

        return _U()
