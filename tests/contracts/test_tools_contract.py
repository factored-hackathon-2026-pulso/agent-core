"""Contrato de `ToolExecutor` (spec M0 T-M0-C-*) contra FakeToolExecutor, más las pruebas propias del doble:
no ejecuta nada real, registra llamadas solo como datos, exige idempotency_key y nunca filtra secretos."""

from dataclasses import dataclass
from typing import Any

import pytest

from agent_core.domain import EntityRef, ToolDef
from agent_core.ports import ToolCallContext, ToolExecutor, ToolStatus
from testing.builders import principal
from testing.fakes.ids import FakeIds
from testing.fakes.tools import FakeToolExecutor, Scripted

WRITE = EntityRef.parse("radicar@1.0.0")
READBACK = EntityRef.parse("obtener@1.0.0")
READ = EntityRef.parse("buscar@1.0.0")
WRITE_STATUSES = {ToolStatus.ok, ToolStatus.denied, ToolStatus.uncertain, ToolStatus.step_up_required}


@dataclass
class Setup:
    executor: ToolExecutor
    ctx: ToolCallContext
    low_ctx: ToolCallContext


def _defs() -> tuple[ToolDef, ToolDef, ToolDef]:
    write = ToolDef(id="radicar", version="1.0.0", risk_class="write_reversible", min_auth_level="session",
                    idempotent=False, readback_by="idempotency_key")
    readback = ToolDef(id="obtener", version="1.0.0", risk_class="read", min_auth_level="session",
                       idempotent=True)
    read = ToolDef(id="buscar", version="1.0.0", risk_class="read", min_auth_level="session", idempotent=True)
    return write, readback, read


def _contexts() -> tuple[ToolCallContext, ToolCallContext]:
    ctx = ToolCallContext(run_id="run-0001", release="rel-1", principal=principal())
    low = ToolCallContext(run_id="run-0001", release="rel-1",
                          principal=principal(id=None, auth={"level": "anonymous",
                                                              "at": principal().auth.at}))
    return ctx, low


def _fake(script: list[Scripted] | None = None) -> FakeToolExecutor:
    fake = FakeToolExecutor(FakeIds())
    write, readback, read = _defs()
    default = [Scripted(ToolStatus.uncertain, effect=True)]
    fake.register(write, script=script if script is not None else default)
    fake.register_readback(readback, of=WRITE)
    fake.register(read, handler=lambda args: {"eco": args.get("q")})
    return fake


@pytest.fixture(params=["fake"])
def setup(request: pytest.FixtureRequest) -> Setup:
    ctx, low = _contexts()
    return Setup(_fake(), ctx, low)


def _readback(executor: ToolExecutor, ctx: ToolCallContext, key: str) -> object:
    return executor.execute(READBACK, {"idempotency_key": key}, {}, ctx).result_full


def check_write_returns_only_write_statuses(s: Setup) -> None:
    result = s.executor.execute(WRITE, {"a": 1}, {}, s.ctx, idempotency_key="action-1")
    assert result.status in WRITE_STATUSES


def check_same_idempotency_key_returns_same_resource(s: Setup) -> None:
    s.executor.execute(WRITE, {"a": 1}, {}, s.ctx, idempotency_key="action-1")
    second = s.executor.execute(WRITE, {"a": 1}, {}, s.ctx, idempotency_key="action-1")
    assert second.status is ToolStatus.ok
    assert second.result_full == _readback(s.executor, s.ctx, "action-1")


def check_step_up_required_has_no_effect(s: Setup) -> None:
    result = s.executor.execute(WRITE, {"a": 1}, {}, s.low_ctx, idempotency_key="action-2")
    assert result.status is ToolStatus.step_up_required
    assert result.required_level == "session"
    assert _readback(s.executor, s.ctx, "action-2") is None


def test_write_returns_only_write_statuses(setup: Setup) -> None:
    check_write_returns_only_write_statuses(setup)


def test_same_idempotency_key_returns_same_resource(setup: Setup) -> None:
    check_same_idempotency_key_returns_same_resource(setup)


def test_step_up_required_has_no_effect(setup: Setup) -> None:
    check_step_up_required_has_no_effect(setup)


def test_read_tool(setup: Setup) -> None:
    result = setup.executor.execute(READ, {"q": "x"}, {}, setup.ctx)
    assert result.status is ToolStatus.ok
    assert result.result_full == {"eco": "x"}
    assert result.call_id.startswith("call-")


def test_fake_records_calls_and_rejects_write_without_key(setup: Setup) -> None:
    fake = setup.executor
    assert isinstance(fake, FakeToolExecutor)
    with pytest.raises(ValueError):
        fake.execute(WRITE, {"a": 1}, {}, setup.ctx)
    fake.execute(READ, {"q": "y"}, {"customer_id": "cust-001"}, setup.ctx)
    assert fake.calls[-1].bound_params == {"customer_id": "cust-001"}


# --- propias del doble ----------------------------------------------------------------------------------

def test_scripted_step_up_on_write_has_no_effect_and_sets_required_level() -> None:
    fake = _fake([Scripted(ToolStatus.step_up_required)])
    ctx, _ = _contexts()
    result = fake.execute(WRITE, {"a": 1}, {}, ctx, idempotency_key="k-1")
    assert result.status is ToolStatus.step_up_required
    assert result.required_level == "session"
    assert fake.effects.get(WRITE, {}) == {}


def test_write_status_outside_contract_is_rejected_without_effect() -> None:
    fake = _fake([Scripted(ToolStatus.timeout)])
    ctx, _ = _contexts()
    with pytest.raises(ValueError):
        fake.execute(WRITE, {"a": 1}, {}, ctx, idempotency_key="k-1")
    assert fake.effects.get(WRITE, {}) == {}


def test_uncertain_effect_true_is_visible_by_readback_and_effect_false_is_not() -> None:
    fake = _fake([Scripted(ToolStatus.uncertain, effect=True), Scripted(ToolStatus.uncertain, effect=False)])
    ctx, _ = _contexts()
    first = fake.execute(WRITE, {"a": 1}, {}, ctx, idempotency_key="k-1")
    assert first.status is ToolStatus.uncertain and first.result_full is None
    assert _readback(fake, ctx, "k-1") == {"a": 1}
    fake.execute(WRITE, {"a": 2}, {}, ctx, idempotency_key="k-2")
    assert _readback(fake, ctx, "k-2") is None


def test_step_up_does_not_consume_the_script() -> None:
    fake = _fake([Scripted(ToolStatus.denied)])
    ctx, low = _contexts()
    assert fake.execute(WRITE, {"a": 1}, {}, low, idempotency_key="k-1").status is ToolStatus.step_up_required
    assert fake.execute(WRITE, {"a": 1}, {}, ctx, idempotency_key="k-1").status is ToolStatus.denied


def test_results_and_records_do_not_alias_caller_data() -> None:
    fake = _fake([Scripted(ToolStatus.ok, result={"n": {"v": 1}})])
    ctx, _ = _contexts()
    args: dict[str, Any] = {"nested": {"v": 1}}
    result = fake.execute(WRITE, args, {}, ctx, idempotency_key="k-1")
    assert isinstance(result.result_full, dict)
    result.result_full["n"]["v"] = 999  # type: ignore[index]
    args["nested"]["v"] = 999
    assert fake.calls[0].args == {"nested": {"v": 1}}
    assert _readback(fake, ctx, "k-1") == {"n": {"v": 1}}


def test_handler_result_is_not_shared_between_calls() -> None:
    shared: Any = {"items": [1]}
    fake = FakeToolExecutor(FakeIds())
    fake.register(_defs()[2], handler=lambda args: shared)
    ctx, _ = _contexts()
    first = fake.execute(READ, {}, {}, ctx)
    first.result_full["items"].append(2)  # type: ignore[index,call-overload]
    assert fake.execute(READ, {}, {}, ctx).result_full == {"items": [1]}


def test_unknown_tool_fails_closed_and_records_nothing() -> None:
    fake = _fake()
    ctx, _ = _contexts()
    with pytest.raises(KeyError):
        fake.execute(EntityRef.parse("nada@1.0.0"), {}, {}, ctx)
    with pytest.raises(KeyError):
        fake.definition(EntityRef.parse("nada@1.0.0"))
    assert fake.calls == []


def test_errors_never_echo_args() -> None:
    fake = _fake()
    ctx, _ = _contexts()
    with pytest.raises(ValueError) as info:
        fake.execute(WRITE, {"password": "s3cr3t-value"}, {"token": "tok-abc"}, ctx)
    assert "s3cr3t-value" not in str(info.value) and "tok-abc" not in str(info.value)
    fake2 = _fake([Scripted(ToolStatus.timeout)])
    with pytest.raises(ValueError) as info2:
        fake2.execute(WRITE, {"password": "s3cr3t-value"}, {}, ctx, idempotency_key="k-1")
    assert "s3cr3t-value" not in str(info2.value)


def test_executor_repr_does_not_leak_call_data() -> None:
    fake = _fake()
    ctx, _ = _contexts()
    fake.execute(READ, {"q": "s3cr3t-value"}, {"customer_id": "cust-001"}, ctx)
    assert "s3cr3t-value" not in repr(fake) + str(fake)
