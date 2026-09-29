import shutil
from pathlib import Path

import yaml

from agent_core.cli import main
from agent_core.domain import EntityKind
from agent_core.flows.agent import release_flows, validate_agent, validate_flow_for_agent
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl, load_registry
from agent_core.flows.validate import validate_registry
from tests.m01.cases import agent, base, flow, node, registry, task_base

FIXTURE = Path(__file__).parent / "fixtures" / "registry"


def _reg() -> AuthoringRegistry:
    return registry(flow(base()))  # agent().entry_flow = base@1


def _decl(**over: object) -> ReleaseDecl:
    data: dict[str, object] = {"id": "rel", "agents": [{"agent": "atencion@1"}],
                               "language_detection": "lang@1"}
    return ReleaseDecl.model_validate(data | over)


def test_agent_and_base_are_valid() -> None:
    assert validate_agent(agent(), _reg()) == []
    assert validate_flow_for_agent(flow(base()), agent(), _reg()) == []


# T-M1-23
def test_mode_mismatch_is_ag_01() -> None:
    found = validate_flow_for_agent(flow(base()), agent(mode="task"), registry())
    assert [(v.rule, v.flow) for v in found] == [("AG-01", "base@1.0.0")]
    assert validate_flow_for_agent(flow(task_base()), agent(mode="task"), registry()) == []


# T-M1-12
def test_missing_locale_in_flow_template() -> None:
    d = base()
    node(d, "pedir")["config"]["prompt_ref"] = "t/solo_es"
    found = validate_flow_for_agent(flow(d), agent(), registry())
    assert [(v.rule, v.node_id) for v in found] == [("G0-12", "pedir")]


def test_agent_engine_templates() -> None:
    templates = dict(agent().templates.model_dump(mode="json"))
    missing = validate_agent(agent(templates={**templates, "clarify": "t/noexiste"}), _reg())
    assert [v.rule for v in missing] == ["G0-02"]
    partial = validate_agent(agent(templates={**templates, "clarify": "t/solo_es"}), _reg())
    assert [v.rule for v in partial] == ["G0-12"]


def test_cli_runs_agent_checks(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    path = root / "agents" / "atencion@1.0.0.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["supported_locales"] = ["es", "pt", "en"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert main(["validate", str(root)]) == 1


def test_release_flows_are_total_and_deduplicated() -> None:
    reg = _reg()
    decl = _decl(flows=["base@1", "base@^1", "fantasma@1"])
    flows, missing = release_flows(reg, decl, agent())
    assert [(f.id, f.version) for f in flows] == [("base", "1.0.0")]
    assert missing == ["fantasma@1"]


def test_registry_reports_missing_agent_and_flow_once_and_in_order() -> None:
    reg = AuthoringRegistry.from_entities(
        [*registry(flow(base())).all(EntityKind.template), agent(), flow(base())],
        [_decl(agents=[{"agent": "atencion@1"}, {"agent": "otro@1"}, {"agent": "otro@1"}],
               flows=["fantasma@1", "fantasma@1"])],
    )
    found = validate_registry(reg)
    messages = [v.message for v in found if v.path == "releases/rel.yaml"]
    assert sorted(set(messages)) == messages
    assert any("otro@1" in m for m in messages) and any("fantasma@1" in m for m in messages)
    assert validate_registry(reg) == found


def test_registry_flags_flow_used_by_release_agent_in_wrong_mode() -> None:
    reg = AuthoringRegistry.from_entities(
        [*registry().all(EntityKind.template), agent(mode="task"), flow(base())], [_decl()]
    )
    assert "AG-01" in {v.rule for v in validate_registry(reg)}


def test_missing_entry_flow_is_reported_once_not_once_per_release() -> None:
    entities = [*registry().all(EntityKind.template), agent(entry_flow="fantasma@1")]
    releases = [_decl(id="rel-a"), _decl(id="rel-b"), _decl(id="rel-c", flows=["fantasma@1"])]
    found = validate_registry(AuthoringRegistry.from_entities(entities, releases))
    mentions = [v for v in found if "fantasma@1" in v.message]
    entry = [v for v in mentions if not (v.path or "").startswith("releases/")]
    assert [(v.rule, v.path) for v in entry] == [("G0-02", "agents/atencion@1.0.0.yaml#/entry_flow")]
    # una release que además lista el flow en `flows` sí lo reporta, en su propio sitio
    assert [v.path for v in mentions if (v.path or "").startswith("releases/")] == ["releases/rel-c.yaml"]
    lonely = AuthoringRegistry.from_entities(entities)
    flows, missing = release_flows(lonely, _decl(), agent(entry_flow="fantasma@1"))
    assert flows == [] and missing == []


def test_agent_violation_path_uses_the_loaded_file(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    path = root / "agents" / "atencion@1.0.0.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["entry_flow"] = "fantasma@1"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    reg, loaded = load_registry(root)
    assert loaded == []
    found = [v for v in validate_registry(reg) if "fantasma@1" in v.message]
    assert [(v.rule, v.path) for v in found] == [("G0-02", "agents/atencion@1.0.0.yaml#/entry_flow")]
