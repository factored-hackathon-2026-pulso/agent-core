"""Tools que sirve el propio motor (composición), no un tool provider: `seleccionar`, `seleccionar_caso`,
`convertir_moneda`, `obtener_handoff` y `leer_transcript`.

Envuelve el `ToolExecutor` real (el `HttpToolExecutor` del tool-service), como `DirectoryToolExecutor`:
lo que no es una de estas cuatro tools pasa tal cual. Se activa solo en `serve`
(`EngineDeps.engine_tools`); el replay y la evaluación siguen con sus dobles guionados.

- `seleccionar` (compute): el elemento de `lista` cuyo `transaction_id` es `id`.
- `seleccionar_caso` (compute): el elemento de `lista` cuyo `case_id` es `id`. Es una tool aparte porque la
  tabla de origen (`source`) sale de la definición de la tool, y de ella depende cómo se clasifican los campos
  del elemento (`customer_cases` y no `customer_transactions`).
- `convertir_moneda` (compute): monto × tasa con una tabla FIJA de un archivo de despliegue
  (`AGENTCORE_FX_RATES_FILE`, `{"USD": "1", "MXN": "0.055", ...}`, USD por unidad de cada moneda). No es
  una fuente de mercado: el resultado es aproximado. Sin tabla, la tool falla cerrada (`fx_unconfigured`).
- `obtener_handoff` / `leer_transcript` (read): leen la conversación del asistente que el copiloto
  acompaña. La sesión NO la escribe el modelo: la pone quien abre el run del copiloto en su `input`
  (`assistant_session_id`, slot `claimed`). Cada run de esa sesión debe ser del mismo sujeto que la
  llamada (el cliente de la delegación); si no, `denied`. El paquete y el texto salen de
  `HandoffService.get` y `TranscriptReader.read_rendered`, que autorizan y enmascaran para quien lee
  (M10, M11).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from agent_core.audit import RunNotFound
from agent_core.domain import (
    EngineError,
    EntityRef,
    JsonValue,
    OnBehalfOf,
    Principal,
    SchemaError,
    SubjectRef,
    ToolDef,
    loads,
)
from agent_core.ports import (
    IdKind,
    IdSource,
    ToolCallContext,
    ToolExecutor,
    ToolResult,
    ToolStatus,
    UnitOfWorkFactory,
)

FX_RATES_FILE_ENV = "AGENTCORE_FX_RATES_FILE"
SESSION_INPUT = "assistant_session_id"
DEFAULT_TURNS = 20
MAX_TURNS = 50
_CENT = Decimal("0.01")

ENGINE_TOOL_IDS = frozenset({"seleccionar", "seleccionar_caso", "convertir_moneda", "obtener_handoff",
                             "leer_transcript"})

Handler = Callable[[dict[str, JsonValue], ToolCallContext, str, str], ToolResult]


@dataclass(frozen=True)
class SessionRun:
    """Lo que las tools de lectura necesitan de un run de la sesión del asistente."""

    run_id: str
    subject: SubjectRef | None
    handoff_ref: str | None


class RunLookup(Protocol):
    def input_of(self, run_id: str, name: str) -> JsonValue: ...

    def session_runs(self, session_id: str) -> list[SessionRun]: ...


class HandoffReader(Protocol):
    def get(self, handoff_ref: str, reader: Principal,
            on_behalf_of: OnBehalfOf | None = None) -> dict[str, JsonValue]: ...


class RenderedLine(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def text(self) -> str: ...


class TranscriptLines(Protocol):
    def read_rendered(self, run_id: str, reader: Principal,
                      on_behalf_of: OnBehalfOf | None) -> Sequence[RenderedLine]: ...


class UowRunLookup:
    """`RunLookup` sobre la base del motor. El run del copiloto ya está guardado cuando el modelo llama a una
    tool en un turno (el primer avance de `start_run` no llama tools del agente)."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def input_of(self, run_id: str, name: str) -> JsonValue:
        with self._uow_factory() as uow:
            state = uow.load_run(run_id)
        if state is None or name not in state.slots:
            return None
        return state.slots[name].value

    def session_runs(self, session_id: str) -> list[SessionRun]:
        with self._uow_factory() as uow:
            runs = uow.list_runs_by_session(session_id)
        return [SessionRun(run_id=r.run_id, subject=r.subject, handoff_ref=r.handoff_ref) for r in runs]


def load_fx_rates(path: Path) -> dict[str, Decimal]:
    """`{"USD": "1", "MXN": "0.055"}`: USD por unidad de cada moneda; códigos ISO de 3 letras en mayúscula."""
    try:
        raw = loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SchemaError(f"no se pudo leer la tabla de tasas {path.name}") from None
    if not isinstance(raw, dict) or not raw:
        raise SchemaError(f"{path.name}: la tabla de tasas debe ser un objeto no vacío")
    rates: dict[str, Decimal] = {}
    for code, value in raw.items():
        try:
            rate = Decimal(str(value))
        except InvalidOperation:
            raise SchemaError(f"{path.name}: tasa inválida para {code!r}") from None
        valid_code = len(code) == 3 and code.isalpha() and code.isupper()
        if not valid_code or isinstance(value, bool) or not rate.is_finite() or rate <= 0:
            raise SchemaError(f"{path.name}: tasa inválida para {code!r}")
        rates[code] = rate
    return rates


def fx_rates_from_env(env: Mapping[str, str]) -> dict[str, Decimal] | None:
    path = (env.get(FX_RATES_FILE_ENV) or "").strip()
    return load_fx_rates(Path(path)) if path else None


def _error(call_id: str, kind: str, status: ToolStatus = ToolStatus.error) -> ToolResult:
    return ToolResult(status=status, call_id=call_id, error=kind)


def _ok(call_id: str, value: JsonValue, source: str) -> ToolResult:
    return ToolResult(status=ToolStatus.ok, result_full=value, source=source, call_id=call_id)


class EngineToolExecutor:
    """Sirve las cuatro tools del motor y delega todo lo demás en `inner`."""

    def __init__(self, inner: ToolExecutor, ids: IdSource, runs: RunLookup, handoffs: HandoffReader,
                 transcripts: TranscriptLines, fx_rates: Mapping[str, Decimal] | None) -> None:
        self._inner = inner
        self._ids = ids
        self._runs = runs
        self._handoffs = handoffs
        self._transcripts = transcripts
        self._fx = fx_rates
        self._handlers: dict[str, Handler] = {
            "seleccionar": self._seleccionar, "seleccionar_caso": self._seleccionar_caso,
            "convertir_moneda": self._convertir,
            "obtener_handoff": self._handoff, "leer_transcript": self._transcript}

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._inner.definition(tool)

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        handler = self._handlers.get(tool.id)
        if handler is None:
            return self._inner.execute(tool, args, bound_params, ctx, idempotency_key)
        call_id = self._ids.new_id(IdKind.call)
        source = self._inner.definition(tool).source or tool.id
        return handler(args, ctx, call_id, source)

    # --- compute -----------------------------------------------------------------------------------------

    @staticmethod
    def _pick(args: dict[str, JsonValue], call_id: str, source: str, key: str) -> ToolResult:
        lista, wanted = args.get("lista"), args.get("id")
        if not isinstance(lista, list) or not isinstance(wanted, str) or not wanted:
            return _error(call_id, "bad_args")
        for item in lista:
            if isinstance(item, dict) and item.get(key) == wanted:
                return _ok(call_id, item, source)
        return _error(call_id, "not_found")

    @staticmethod
    def _seleccionar(args: dict[str, JsonValue], ctx: ToolCallContext, call_id: str,
                     source: str) -> ToolResult:
        return EngineToolExecutor._pick(args, call_id, source, "transaction_id")

    @staticmethod
    def _seleccionar_caso(args: dict[str, JsonValue], ctx: ToolCallContext, call_id: str,
                          source: str) -> ToolResult:
        return EngineToolExecutor._pick(args, call_id, source, "case_id")

    def _convertir(self, args: dict[str, JsonValue], ctx: ToolCallContext, call_id: str,
                   source: str) -> ToolResult:
        if self._fx is None:
            return _error(call_id, "fx_unconfigured")
        moneda, destino, raw = args.get("moneda"), args.get("destino"), args.get("monto")
        try:
            monto = Decimal(str(raw))
        except InvalidOperation:
            return _error(call_id, "bad_args")
        if isinstance(raw, bool) or not monto.is_finite() or not isinstance(moneda, str) \
                or not isinstance(destino, str):
            return _error(call_id, "bad_args")
        if moneda not in self._fx or destino not in self._fx:
            return _error(call_id, "unknown_currency")
        value = (monto * self._fx[moneda] / self._fx[destino]).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        return _ok(call_id, value, source)

    # --- lecturas de la sesión del asistente -------------------------------------------------------------

    def _session(self, ctx: ToolCallContext, call_id: str) -> list[SessionRun] | ToolResult:
        """Los runs de la sesión del asistente, todos del sujeto de la llamada; o el resultado a devolver."""
        if ctx.subject is None:
            return _error(call_id, "no_subject", ToolStatus.denied)
        session_id = self._runs.input_of(ctx.run_id, SESSION_INPUT)
        if session_id is None:
            return []  # el copiloto no acompaña ninguna conversación: no hay nada que leer
        if not isinstance(session_id, str) or not session_id:
            return _error(call_id, "bad_session")
        runs = self._runs.session_runs(session_id)
        if any(r.subject != ctx.subject for r in runs):
            return _error(call_id, "subject_mismatch", ToolStatus.denied)
        return runs

    def _handoff(self, args: dict[str, JsonValue], ctx: ToolCallContext, call_id: str,
                 source: str) -> ToolResult:
        runs = self._session(ctx, call_id)
        if isinstance(runs, ToolResult):
            return runs
        refs = [r.handoff_ref for r in runs if r.handoff_ref is not None]
        if not refs:
            return _ok(call_id, None, source)
        try:
            packet = self._handoffs.get(refs[-1], ctx.principal, ctx.on_behalf_of)
        except EngineError as exc:
            return _error(call_id, exc.code.value, ToolStatus.denied)
        return _ok(call_id, packet, source)

    def _transcript(self, args: dict[str, JsonValue], ctx: ToolCallContext, call_id: str,
                    source: str) -> ToolResult:
        limite = args.get("limite", DEFAULT_TURNS)
        if not isinstance(limite, int) or isinstance(limite, bool) or not 1 <= limite <= MAX_TURNS:
            return _error(call_id, "bad_args")
        runs = self._session(ctx, call_id)
        if isinstance(runs, ToolResult):
            return runs
        lines: list[JsonValue] = []
        try:
            for run in runs:  # en orden de la sesión (transferencias incluidas)
                rendered = self._transcripts.read_rendered(run.run_id, ctx.principal, ctx.on_behalf_of)
                lines.extend({"role": line.role, "text": line.text}
                             for line in rendered if line.role in ("user", "assistant"))
        except EngineError as exc:
            return _error(call_id, exc.code.value, ToolStatus.denied)
        except RunNotFound:
            return _error(call_id, "not_found")
        return _ok(call_id, lines[-limite:], source)
