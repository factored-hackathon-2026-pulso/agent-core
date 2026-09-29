"""Clausura de la release en `validate_registry` y la CLI (M1 §3.11, §3.12): lo que `pin_release` rechaza,
`validate` lo reporta y sale con 1."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from agent_core.domain import LanguageDetection, RegistryEntity, SchemaError
from agent_core.flows import closure as closure_module
from agent_core.flows.cli_validate import run_validate
from agent_core.flows.closure import closure_problems, resolve_closure
from agent_core.flows.pin import pin_release
from agent_core.flows.registry import AuthoringRegistry, ReleaseAgent, ReleaseDecl, load_registry
from agent_core.flows.validate import validate_registry
from agent_core.flows.violations import Violation
from tests.m01.cases import ENTITIES, agent, base, flow

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
LANG = LanguageDetection.model_validate(
    {"id": "lang", "version": "1.0.0", "detector": "lingua@1.4.0", "candidates": ["es", "pt"],
     "min_letters": 12, "min_letters_unsupported": 20}
)


def _decl(**over: object) -> ReleaseDecl:
    data: dict[str, object] = {"id": "r", "agents": [{"agent": "atencion@1"}], "flows": ["base@1.0.0"],
                               "language_detection": "lang@1"}
    return ReleaseDecl.model_validate(data | over)


def _reg(decl: ReleaseDecl, *extra: RegistryEntity, lang: bool = True) -> AuthoringRegistry:
    entities = [*ENTITIES, flow(base()), agent(), *extra, *([LANG] if lang else [])]
    return AuthoringRegistry.from_entities(entities, [decl])


def _release_violations(reg: AuthoringRegistry) -> list[Violation]:
    found = validate_registry(reg)
    assert all(isinstance(v, Violation) for v in found)
    return [v for v in found if (v.path or "").startswith("releases/r.yaml#/")]


def test_a_valid_release_has_no_problems() -> None:
    reg = _reg(_decl())
    assert closure_problems(reg, _decl()) == []
    assert validate_registry(reg) == []
    pin_release(reg, "r")


def test_unresolved_release_level_refs_are_g0_02_and_pin_still_rejects() -> None:
    decl = _decl(language_detection="nolang@1", injection_ruleset="noinj@1")
    reg = _reg(decl, lang=False)
    found = _release_violations(reg)
    assert [(v.rule, v.path, v.flow, v.node_id) for v in found] == [
        ("G0-02", "releases/r.yaml#/injection_ruleset", None, None),
        ("G0-02", "releases/r.yaml#/language_detection", None, None),
    ]
    assert "noinj@1" in found[0].message and "nolang@1" in found[1].message
    with pytest.raises(SchemaError, match=r"language_detection.*no resuelve"):
        pin_release(reg, "r")


def test_unresolved_signal_policy_is_reported_and_pin_still_rejects() -> None:
    interrupt: dict[str, Any] = {"id": "salir", "priority": 1,
                                 "action": {"type": "start_flow", "flow": "base@^1"},
                                 "signal_policy": "nopol@1"}
    decl = _decl(interrupts=[interrupt])
    reg = _reg(decl)
    found = _release_violations(reg)
    assert [(v.rule, v.path) for v in found] == [("G0-02", "releases/r.yaml#/interrupts/0/signal_policy")]
    with pytest.raises(SchemaError, match=r"signal_policy.*no resuelve"):
        pin_release(reg, "r")


def test_invalid_alias_is_reported_and_pin_still_rejects() -> None:
    entry = ReleaseAgent.model_construct(agent=_decl().agents[0].agent, aliases=["PROD X"])
    decl = _decl().model_copy(update={"agents": [entry]})
    reg = _reg(decl)
    found = _release_violations(reg)
    assert [(v.rule, v.path) for v in found] == [("G0-02", "releases/r.yaml#/agents/0/aliases")]
    assert "alias inválido" in found[0].message
    with pytest.raises(SchemaError, match=r"alias"):
        pin_release(reg, "r")


def test_version_conflict_is_g0_02_and_pin_still_rejects() -> None:
    decl = _decl(flows=["base@1.0.0", "base@1"])
    reg = _reg(decl, flow({**base(), "version": "1.1.0"}))
    found = _release_violations(reg)
    # `base@1` (release y entry_flow del agente) resuelve a 1.1.0 aunque la release fija 1.0.0
    assert {(v.rule, v.path) for v in found} == {
        ("G0-02", "releases/r.yaml#/flows/1"),
        ("G0-02", "releases/r.yaml#/agent atencion@1.0.0 /entry_flow"),
    }
    assert all("resuelve a 1.0.0 y a 1.1.0" in v.message for v in found)
    with pytest.raises(SchemaError, match=r"resuelve a 1\.0\.0 y a 1\.1\.0"):
        pin_release(reg, "r")


def test_closure_problems_are_sorted_and_deterministic() -> None:
    decl = _decl(language_detection="nolang@1", injection_ruleset="noinj@1", flows=["base@1.0.0", "base@1"])
    extra = flow({**base(), "version": "1.1.0"})
    first = closure_problems(_reg(decl, extra, lang=False), decl)
    second = closure_problems(_reg(decl, extra, lang=False), decl)
    assert first == second == sorted(first)
    assert len(first) == 4


def test_skip_covered_omits_unresolved_refs_reported_elsewhere() -> None:
    decl = _decl(flows=["fantasma@1"], agents=[{"agent": "otro@1"}])
    reg = _reg(decl)
    assert {where for where, _ in closure_problems(reg, decl)} == {"agents/0", "flows/0"}
    assert closure_problems(reg, decl, skip_covered=True) == []
    chosen, _ = resolve_closure(reg, decl)
    assert sorted(i for _, i in chosen) == ["lang"]  # solo lo que sí resolvió


def test_problems_are_capped() -> None:
    decl = _decl(flows=[f"f{i}@1" for i in range(300)])
    problems = closure_problems(_reg(decl), decl)
    assert len(problems) == closure_module.MAX_PROBLEMS + 1
    assert problems[-1][1] == "se omitieron 100 problemas más"


# --- CLI sobre una copia del registro de la fixture ---

def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    return root


def _release_yaml(root: Path, text: str) -> None:
    (root / "releases" / "demo.yaml").write_text(text, encoding="utf-8")


def test_cli_baseline_is_clean(tmp_path: Path) -> None:
    assert run_validate(_copy(tmp_path), as_json=False)[0] == 0


def test_cli_exits_1_on_missing_language_detection_and_injection_ruleset(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _release_yaml(root, "id: demo\nagents:\n  - {agent: 'atencion@^1', aliases: [prod]}\n"
                        "flows: ['disputa-cargo@^1']\nlanguage_detection: 'nolang@1'\n"
                        "injection_ruleset: 'noinj@1'\n")
    code, out = run_validate(root, as_json=False)
    assert code == 1 and "nolang@1" in out and "noinj@1" in out and "G0-02" in out
    reg, loaded = load_registry(root)
    assert loaded == []
    with pytest.raises(SchemaError):
        pin_release(reg, "demo")


def test_cli_exits_1_on_invalid_alias(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _release_yaml(root, "id: demo\nagents:\n  - {agent: 'atencion@^1', aliases: ['PROD X']}\n"
                        "flows: ['disputa-cargo@^1']\nlanguage_detection: 'lang-es-pt@1'\n")
    code, out = run_validate(root, as_json=False)
    assert code == 1 and "alias" in out


def test_cli_exits_1_on_version_conflict(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    source = (root / "flows" / "disputa-cargo@1.0.0.yaml").read_text(encoding="utf-8")
    assert "version: 1.0.0" in source
    (root / "flows" / "disputa-cargo@1.1.0.yaml").write_text(
        source.replace("version: 1.0.0", "version: 1.1.0"), encoding="utf-8")
    _release_yaml(root, "id: demo\nagents:\n  - {agent: 'atencion@^1', aliases: [prod]}\n"
                        "flows: ['disputa-cargo@1.0.0', 'disputa-cargo@1']\n"
                        "language_detection: 'lang-es-pt@1'\n")
    code, out = run_validate(root, as_json=False)
    assert code == 1 and "resuelve a 1.0.0 y a 1.1.0" in out
    reg, loaded = load_registry(root)
    assert loaded == []
    with pytest.raises(SchemaError, match=r"resuelve a 1\.0\.0 y a 1\.1\.0"):
        pin_release(reg, "demo")
