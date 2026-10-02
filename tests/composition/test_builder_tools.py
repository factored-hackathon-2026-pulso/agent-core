"""El adaptador del constructor: permisos por su propia credencial, idempotencia y auditoría del run."""

from typing import Any

import pytest

from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
from agent_core.domain import EntityRef, Principal
from agent_core.domain.schema import unsupported_keyword
from agent_core.ports import ToolCallContext, ToolResult, ToolStatus
from agent_core.registry import Quotas, RegistryService
from testing.builders import principal
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _ref(name: str) -> EntityRef:
    return EntityRef(id=f"registry/{name}", version="1.0.0")


def _ctx(run_principal: Principal | None = None) -> ToolCallContext:
    who = run_principal or principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                                     attrs={"actor": "human"})
    return ToolCallContext(run_id="run-1", release="rel-x", principal=who)


def _executor(w: World, actor: Principal | None = None, service: RegistryService | None = None
              ) -> BuilderToolExecutor:
    return BuilderToolExecutor(service or w.service, actor or bot(), FakeIds())


def _value(result: ToolResult) -> dict[str, Any]:
    assert isinstance(result.result_full, dict), result
    return result.result_full


def _create(ex: BuilderToolExecutor, key: str = "k1", origin: str = "builder_chat") -> ToolResult:
    return ex.execute(_ref("create_proposal"), {"agent_id": AGENT, "origin": origin, "title": "t"}, {},
                      _ctx(), key)


def test_create_proposal_is_idempotent_and_audited_with_the_run_principal() -> None:
    w = World()
    ex = _executor(w)
    first, again = _create(ex), _create(ex)
    assert first.status is ToolStatus.ok and again.status is ToolStatus.ok
    assert _value(first)["proposal_id"] == _value(again)["proposal_id"]
    record = _value(ex.execute(_ref("get_write"), {"idempotency_key": "k1"}, {}, _ctx()))
    assert (record["op"], record["run_id"], record["on_behalf_of"]) == ("create_proposal", "run-1",
                                                                       "builder:ana")


def test_a_write_without_a_key_is_refused() -> None:
    w = World()
    with pytest.raises(ValueError):
        _executor(w).execute(_ref("freeze"), {"proposal_id": "x"}, {}, _ctx())


def test_permissions_come_from_the_executor_credential_not_from_the_run() -> None:  # Review Focus 7
    w = World()
    no_role = principal(type="builder", id="sin-rol", roles=[], attrs={})
    result = _create(_executor(w, actor=no_role))  # el run lo hace un supervisor con constructor y aprobador
    assert (result.status, result.error) == (ToolStatus.denied, "forbidden_role")


def test_the_quota_is_a_denial_without_effect() -> None:  # Review Focus 6
    w = World()
    limited = RegistryService(w.store, w.evaluator, w.clock, w.ids, quotas=Quotas(proposals_per_day=1))
    ex = _executor(w, service=limited)
    assert _create(ex, "a", "auto_detect").status is ToolStatus.ok
    second = _create(ex, "b", "auto_detect")
    assert (second.status, second.error) == (ToolStatus.denied, "quota_exceeded")


def test_a_registry_rejection_of_a_write_is_uncertain_and_leaves_no_record() -> None:  # Review Focus 6
    w = World()
    ex = _executor(w)
    proposal_id = _value(_create(ex))["proposal_id"]
    bad = prompt_draft(version="0.1.0").model_dump(mode="json")  # versión inválida: freeze la rechaza
    put = ex.execute(_ref("put_draft"), {"proposal_id": proposal_id, "expected_rev": 0, "changes": [bad]}, {},
                     _ctx(), "k2")
    assert put.status is ToolStatus.ok
    frozen = ex.execute(_ref("freeze"), {"proposal_id": proposal_id}, {}, _ctx(), "k3")
    assert (frozen.status, frozen.error) == (ToolStatus.uncertain, "validation_failed")
    readback = ex.execute(_ref("get_write"), {"idempotency_key": "k3"}, {}, _ctx())
    assert readback.status is ToolStatus.ok and readback.result_full is None  # el verify lo verá como failed


def test_malformed_arguments_do_not_raise() -> None:  # Review Focus 6
    w = World()
    result = _executor(w).execute(_ref("put_draft"), {"proposal_id": 7}, {}, _ctx(), "k")
    assert (result.status, result.error) == (ToolStatus.denied, "invalid_args")


def test_the_whole_constructor_cycle_through_the_tools() -> None:
    w = World()
    ex = _executor(w)
    pid = _value(_create(ex))["proposal_id"]
    changes = [prompt_draft().model_dump(mode="json"), SUITE.model_dump(mode="json")]
    put = ex.execute(_ref("put_draft"), {"proposal_id": pid, "expected_rev": 0, "changes": changes}, {},
                     _ctx(), "k2")
    assert put.status is ToolStatus.ok
    assert _value(ex.execute(_ref("validate"), {"proposal_id": pid}, {}, _ctx()))["valid"] is True
    assert ex.execute(_ref("freeze"), {"proposal_id": pid}, {}, _ctx(), "k3").status is ToolStatus.ok
    evaluated = ex.execute(_ref("evaluate"), {"proposal_id": pid, "suite_id": "disputas-suite"}, {}, _ctx(),
                           "k4")
    assert _value(evaluated)["verdict"] == "pass"
    assert _value(ex.execute(_ref("get_write"), {"idempotency_key": "k4"}, {}, _ctx()))["verdict"] == "pass"
    assert _value(ex.execute(_ref("get_proposal"), {"proposal_id": pid}, {}, _ctx()))["state"] == "evaluated"


def test_the_builder_has_no_tool_to_approve_publish_promote_or_revoke() -> None:
    names = {tool_id.split("/")[1] for tool_id in BUILDER_TOOL_DEFS}
    assert names == {"create_proposal", "put_draft", "freeze", "reopen", "evaluate", "validate",
                     "get_proposal", "get_entity", "list_versions", "get_write"}
    assert not names & {"approve", "reject", "publish", "promote", "revoke", "import_seed"}


def test_every_tool_is_documented_and_every_write_declares_its_readback() -> None:
    for definition in BUILDER_TOOL_DEFS.values():
        assert definition.description and definition.args_schema is not None
        assert unsupported_keyword(definition.args_schema) is None
        if definition.risk_class.value == "write_draft":
            assert definition.readback_by == "idempotency_key"


def test_an_unknown_tool_or_version_is_a_key_error() -> None:
    ex = _executor(World())
    with pytest.raises(KeyError):
        ex.definition(EntityRef(id="registry/publish", version="1.0.0"))
    with pytest.raises(KeyError):
        ex.definition(EntityRef(id="registry/freeze", version="2.0.0"))


def test_the_builder_cannot_choose_an_origin_that_escapes_the_autonomous_quotas() -> None:
    w = World()
    ex = _executor(w)
    for origin in ("manual", "import"):
        result = _create(ex, f"k-{origin}", origin)
        assert (result.status, result.error) == (ToolStatus.denied, "invalid_args"), origin
    enum = BUILDER_TOOL_DEFS["registry/create_proposal"].args_schema
    assert isinstance(enum, dict)
    properties = enum["properties"]
    assert isinstance(properties, dict) and properties["origin"] == {
        "type": "string", "enum": ["builder_chat", "auto_detect"]}
