"""Tools del motor servidas por composición: seleccionar, convertir_moneda, handoff y transcript."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from agent_core.composition.engine_tools import (
    EngineToolExecutor,
    SessionRun,
    fx_rates_from_env,
    load_fx_rates,
)
from agent_core.domain import (
    EngineError,
    EntityRef,
    JsonValue,
    OnBehalfOf,
    Principal,
    ProblemCode,
    SchemaError,
    SubjectRef,
    ToolDef,
)
from agent_core.ports import ToolCallContext, ToolStatus
from testing.builders import principal
from testing.fakes.ids import FakeIds
from testing.fakes.tools import FakeToolExecutor

CUSTOMER = SubjectRef(kind="customer", ref="cust-001")
OTHER = SubjectRef(kind="customer", ref="cust-002")


def _ref(tool_id: str) -> EntityRef:
    return EntityRef(id=tool_id, version="1.0.0")


def _def(tool_id: str, risk: str, source: str | None = None) -> ToolDef:
    data: dict[str, JsonValue] = {
        "id": tool_id, "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
        "idempotent": True, "args_schema": {"type": "object"}}
    if source is not None:
        data["source"] = source
    return ToolDef.model_validate(data)


class _Runs:
    def __init__(self, inputs: dict[str, dict[str, JsonValue]],
                 sessions: dict[str, list[SessionRun]]) -> None:
        self.inputs, self.sessions = inputs, sessions

    def input_of(self, run_id: str, name: str) -> JsonValue:
        return self.inputs.get(run_id, {}).get(name)

    def session_runs(self, session_id: str) -> list[SessionRun]:
        return self.sessions.get(session_id, [])


class _Handoffs:
    def __init__(self, deny: bool = False) -> None:
        self.deny = deny
        self.asked: list[tuple[str, str]] = []

    def get(self, handoff_ref: str, reader: Principal,
            on_behalf_of: OnBehalfOf | None = None) -> dict[str, JsonValue]:
        self.asked.append((handoff_ref, reader.id))
        if self.deny:
            raise EngineError(ProblemCode.subject_forbidden, "")
        return {"reason": "monto", "summary": "resumen visible"}


@dataclass
class _Line:
    role: str
    text: str


class _Transcripts:
    def __init__(self, by_run: dict[str, list[_Line]]) -> None:
        self.by_run = by_run

    def read_rendered(self, run_id: str, reader: Principal,
                      on_behalf_of: OnBehalfOf | None) -> list[_Line]:
        return self.by_run[run_id]


def _executor(fx: dict[str, Decimal] | None = None, *, deny_handoff: bool = False,
              runs: _Runs | None = None) -> tuple[EngineToolExecutor, FakeToolExecutor, _Handoffs]:
    ids = FakeIds()
    inner = FakeToolExecutor(ids)
    inner.register(_def("seleccionar", "compute", "customer_transactions"), handler=lambda a: None)
    inner.register(_def("convertir_moneda", "compute"), handler=lambda a: None)
    inner.register(_def("obtener_handoff", "read", "handoff"), handler=lambda a: None)
    inner.register(_def("leer_transcript", "read", "transcript"), handler=lambda a: None)
    inner.register(_def("leer_productos", "read", "customer_products"), handler=lambda a: [{"p": 1}])
    handoffs = _Handoffs(deny_handoff)
    default_runs = _Runs(
        {"run-cop": {"assistant_session_id": "ses-1"}},
        {"ses-1": [SessionRun("run-rec", CUSTOMER, None), SessionRun("run-dis", CUSTOMER, "ho-1")]})
    transcripts = _Transcripts({
        "run-rec": [_Line("user", "hola"), _Line("assistant", "¿en qué te ayudo?")],
        "run-dis": [_Line("user", "no reconozco un cargo"), _Line("rejected_draft", "borrador"),
                    _Line("assistant", "lo escalo")]})
    executor = EngineToolExecutor(inner, ids, runs or default_runs, handoffs, transcripts, fx)
    return executor, inner, handoffs


def _ctx(subject: SubjectRef | None = CUSTOMER, run_id: str = "run-cop") -> ToolCallContext:
    return ToolCallContext(run_id=run_id, release="rel-1", principal=principal(), subject=subject)


# --- seleccionar ------------------------------------------------------------------------------------------

def test_seleccionar_returns_the_item_with_that_transaction_id_keeping_the_source_table() -> None:
    executor, _, _ = _executor()
    lista: list[JsonValue] = [{"transaction_id": "tx-1", "amount": "1"},
                              {"transaction_id": "tx-2", "amount": "2"}]
    result = executor.execute(_ref("seleccionar"), {"lista": lista, "id": "tx-2"}, {}, _ctx())
    assert result.status is ToolStatus.ok
    assert result.result_full == {"transaction_id": "tx-2", "amount": "2"}
    assert result.source == "customer_transactions"


@pytest.mark.parametrize("args", [{"lista": [], "id": "tx-9"}, {"lista": "x", "id": "tx-1"}, {"lista": []}])
def test_seleccionar_without_a_match_or_with_bad_args_is_an_error(args: dict[str, JsonValue]) -> None:
    executor, _, _ = _executor()
    result = executor.execute(_ref("seleccionar"), args, {}, _ctx())
    assert result.status is ToolStatus.error


# --- convertir_moneda -------------------------------------------------------------------------------------

def test_convertir_moneda_uses_the_fixed_table_and_rounds_to_cents() -> None:
    executor, _, _ = _executor({"USD": Decimal("1"), "MXN": Decimal("0.055")})
    result = executor.execute(_ref("convertir_moneda"),
                              {"monto": Decimal("1850.00"), "moneda": "MXN", "destino": "USD"}, {}, _ctx())
    assert result.status is ToolStatus.ok
    assert result.result_full == Decimal("101.75")


def test_convertir_moneda_without_a_table_fails_closed() -> None:
    executor, _, _ = _executor(None)
    result = executor.execute(_ref("convertir_moneda"), {"monto": "1", "moneda": "USD", "destino": "USD"}, {},
                              _ctx())
    assert (result.status, result.error) == (ToolStatus.error, "fx_unconfigured")


def test_convertir_moneda_refuses_an_unknown_currency_or_a_bad_amount() -> None:
    executor, _, _ = _executor({"USD": Decimal("1")})
    unknown = executor.execute(_ref("convertir_moneda"),
                               {"monto": "1", "moneda": "COP", "destino": "USD"}, {}, _ctx())
    bad = executor.execute(_ref("convertir_moneda"), {"monto": "uno", "moneda": "USD", "destino": "USD"}, {},
                           _ctx())
    assert unknown.error == "unknown_currency" and bad.error == "bad_args"


def test_fx_table_file_is_validated(tmp_path: Path) -> None:
    good = tmp_path / "fx.json"
    good.write_text('{"USD": "1", "MXN": 0.055}', encoding="utf-8")
    assert load_fx_rates(good) == {"USD": Decimal("1"), "MXN": Decimal("0.055")}
    assert fx_rates_from_env({}) is None
    for bad in ('{"usd": "1"}', '{"USD": "-1"}', "[]", "{}", "no json"):
        broken = tmp_path / "broken.json"
        broken.write_text(bad, encoding="utf-8")
        with pytest.raises(SchemaError):
            load_fx_rates(broken)


# --- obtener_handoff / leer_transcript --------------------------------------------------------------------

def test_handoff_reads_the_latest_handoff_of_the_assistant_session_for_the_reader() -> None:
    executor, _, handoffs = _executor()
    result = executor.execute(_ref("obtener_handoff"), {}, {}, _ctx())
    assert result.status is ToolStatus.ok
    assert result.result_full == {"reason": "monto", "summary": "resumen visible"}
    assert result.source == "handoff"
    assert handoffs.asked == [("ho-1", principal().id)]


def test_transcript_joins_the_session_runs_in_order_without_rejected_drafts_and_keeps_the_last_n() -> None:
    executor, _, _ = _executor()
    result = executor.execute(_ref("leer_transcript"), {"limite": 3}, {}, _ctx())
    assert result.status is ToolStatus.ok
    assert result.result_full == [{"role": "assistant", "text": "¿en qué te ayudo?"},
                                  {"role": "user", "text": "no reconozco un cargo"},
                                  {"role": "assistant", "text": "lo escalo"}]


def test_a_session_of_another_customer_is_denied() -> None:
    executor, _, handoffs = _executor()
    for tool in ("obtener_handoff", "leer_transcript"):
        result = executor.execute(_ref(tool), {}, {}, _ctx(subject=OTHER))
        assert (result.status, result.error) == (ToolStatus.denied, "subject_mismatch")
    assert handoffs.asked == []


def test_without_a_subject_nothing_is_read() -> None:
    executor, _, _ = _executor()
    result = executor.execute(_ref("leer_transcript"), {}, {}, _ctx(subject=None))
    assert result.status is ToolStatus.denied


def test_without_an_assistant_session_there_is_nothing_to_read() -> None:
    executor, _, _ = _executor(runs=_Runs({}, {}))
    handoff = executor.execute(_ref("obtener_handoff"), {}, {}, _ctx())
    transcript = executor.execute(_ref("leer_transcript"), {}, {}, _ctx())
    assert (handoff.status, handoff.result_full) == (ToolStatus.ok, None)
    assert (transcript.status, transcript.result_full) == (ToolStatus.ok, [])


def test_the_handoff_policy_refusal_is_a_denial() -> None:
    executor, _, _ = _executor(deny_handoff=True)
    result = executor.execute(_ref("obtener_handoff"), {}, {}, _ctx())
    assert (result.status, result.error) == (ToolStatus.denied, "subject_forbidden")


@pytest.mark.parametrize("limite", [0, 51, "5", True])
def test_transcript_limit_is_bounded(limite: JsonValue) -> None:
    executor, _, _ = _executor()
    assert executor.execute(_ref("leer_transcript"), {"limite": limite}, {}, _ctx()).error == "bad_args"


def test_other_tools_go_to_the_provider_untouched() -> None:
    executor, _, _ = _executor()
    result = executor.execute(_ref("leer_productos"), {}, {}, _ctx())
    assert result.status is ToolStatus.ok and result.result_full == [{"p": 1}]
    assert executor.definition(_ref("leer_productos")).source == "customer_products"
