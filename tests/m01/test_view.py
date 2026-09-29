import pytest

from agent_core.domain import EntityKind, RefSpec, Template, ToolDef
from agent_core.flows.refs import RefSite, agent_ref_sites, entity_ref_sites, flow_ref_sites, pointer_str
from agent_core.flows.view import best_version, parse_version, satisfies
from tests.m01.cases import AGENT, agent, base, flow, registry


# T-M1-42
@pytest.mark.parametrize(
    ("spec", "version", "ok"),
    [
        (None, "3.1.0", True),
        ("1.2.0", "1.2.0", True), ("1.2.0", "1.2.1", False),
        ("^1", "1.9.9", True), ("^1", "2.0.0", False), ("^1.2", "1.1.0", False), ("^1.2", "1.3.0", True),
        ("~1.2", "1.2.9", True), ("~1.2", "1.3.0", False), ("~1", "1.9.0", True),
        ("1", "1.4.0", True), ("1", "2.0.0", False),
        ("1.2", "1.2.5", True), ("1.2", "1.3.0", False),
    ],
)
def test_satisfies(spec: str | None, version: str, ok: bool) -> None:
    assert satisfies(spec, version) is ok


def test_best_version_uses_semver_precedence() -> None:
    assert best_version("^1", ["1.0.0", "1.10.0", "1.9.0", "2.0.0"]) == "1.10.0"
    assert best_version(None, ["1.0.0", "2.0.0"]) == "2.0.0"
    assert best_version("^3", ["1.0.0"]) is None


def test_registry_resolves_by_kind_and_range() -> None:
    reg = registry()
    assert isinstance(reg.resolve(EntityKind.tool, RefSpec.parse("leer@^1")), ToolDef)
    assert reg.resolve(EntityKind.tool, RefSpec.parse("leer@2")) is None
    assert reg.resolve(EntityKind.template, RefSpec.parse("leer")) is None


def test_template_reads_are_derived() -> None:
    tpl = registry().resolve(EntityKind.template, RefSpec.parse("t/hecho"))
    assert isinstance(tpl, Template)
    assert tpl.reads == frozenset({"facts.verif.value.id"})


def test_flow_ref_sites_cover_the_table() -> None:
    sites = {(s.node_id, s.kind, str(s.ref)) for s in flow_ref_sites(flow(base()))}
    assert sites == {
        ("pedir", EntityKind.template, "t/pedir"),
        ("buscar", EntityKind.tool, "leer@1"),
        ("confirmar", EntityKind.tool, "escribir@1"),
        ("confirmar", EntityKind.template, "t/resumen"),
        ("verificar", EntityKind.tool, "leer_escritura@1"),
        ("ok_msg", EntityKind.template, "t/hecho"),
    }


# Robustez ante texto no confiable del registro
_BAD_SPECS = ["", "^", "~", "1.x", "-1", "^-1", "1.2.3.4", " 1", "1 ", "1\n", "^^1", "v1", "01", "1.02",
              "١.٢.٣", "^١", "1.", ".1", "1..2", "9" * 5000, "^" + "9" * 5000, "1e3", "1_0"]
_BAD_VERSIONS = ["", "1", "1.2", "1.2.3.4", "-1.0.0", "1.x.0", "١.٢.٣", "1.0.0\n",
                 "9" * 5000 + ".0.0", "01.0.0"]


@pytest.mark.parametrize("spec", _BAD_SPECS, ids=lambda v: repr(v)[:20])
def test_satisfies_is_total_on_bad_specs(spec: str) -> None:
    assert satisfies(spec, "1.2.3") is False


@pytest.mark.parametrize("version", _BAD_VERSIONS, ids=lambda v: repr(v)[:20])
def test_satisfies_is_total_on_bad_versions(version: str) -> None:
    assert satisfies(None, version) is False
    assert satisfies("^1", version) is False


@pytest.mark.parametrize("version", _BAD_VERSIONS, ids=lambda v: repr(v)[:20])
def test_parse_version_raises_only_value_error(version: str) -> None:
    with pytest.raises(ValueError):
        parse_version(version)


def test_parse_version_ok() -> None:
    assert parse_version("1.10.0") == (1, 10, 0)


def test_best_version_ignores_bad_versions_and_specs() -> None:
    assert best_version("^1", ["basura", "1.2.0", "١.٢.٣"]) == "1.2.0"
    assert best_version("^", ["1.0.0"]) is None
    assert best_version(None, []) is None


def test_authoring_resolve_never_raises() -> None:
    reg = registry()
    for spec in _BAD_SPECS:
        assert reg.resolve(EntityKind.tool, RefSpec.model_construct(id="leer", spec=spec)) is None


def test_versions_sorted_incrementally() -> None:
    reg = registry(
        ToolDef.model_validate({"id": "leer", "version": "1.10.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True}),
        ToolDef.model_validate({"id": "leer", "version": "1.2.0", "risk_class": "read",
                                "min_auth_level": "session", "idempotent": True}),
    )
    assert reg.versions(EntityKind.tool, "leer") == ["1.0.0", "1.2.0", "1.10.0"]
    reg.versions(EntityKind.tool, "leer").append("9.9.9")  # copia: no altera el registro
    assert reg.versions(EntityKind.tool, "leer") == ["1.0.0", "1.2.0", "1.10.0"]


class _Port:
    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc

    def get(self, ref: object, kind: object) -> object:
        raise self._exc  # type: ignore[misc]


def test_release_view_narrow_except() -> None:
    from agent_core.domain import Release
    from agent_core.flows.view import release_view

    rel = Release.model_validate({
        "id": "r", "status": "active", "entities": {"tool": {"leer": "1.0.0"}},
        "language_detection": "ld@1.0.0"})
    ref = RefSpec.parse("leer@1")
    assert release_view(_Port(KeyError("x")), rel).resolve(EntityKind.tool, ref) is None  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        release_view(_Port(RuntimeError("boom")), rel).resolve(EntityKind.tool, ref)  # type: ignore[arg-type]
    assert release_view(_Port(None), rel).resolve(EntityKind.tool, RefSpec.parse("leer@2")) is None  # type: ignore[arg-type]


# Los punteros de RefSite deben navegar `model_dump(by_alias=True, mode="json")` hasta la referencia.
def _navigate(dumped: object, pointer: tuple[str | int, ...]) -> object:
    cur = dumped
    for part in pointer:
        cur = cur[part]  # type: ignore[index]
    return cur


def _check_sites(entity: object, sites: list[RefSite]) -> None:
    dumped = entity.model_dump(by_alias=True, mode="json")  # type: ignore[attr-defined]
    for site in sites:
        # RefSpec se vuelca como {"id", "spec"}; el `value` crudo de un validador `decide` es el texto.
        target = _navigate(dumped, site.pointer)
        text = f"{target['id']}@{target['spec']}" if isinstance(target, dict) and target.get("spec") else (
            target["id"] if isinstance(target, dict) else target)
        assert text == str(site.ref), site


def _all_sites_flow() -> dict[str, object]:
    return {
        "id": "sitios", "version": "1.0.0", "priority": 10,
        "nodes": [
            {"id": "dec", "type": "decide",
             "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d"},
             "next": {"a": "pol", "b": "pol", "low_confidence": "pol"}},
            {"id": "pol", "type": "rule", "config": {"policy": "pol@1"},
             "next": {"true": "col", "false": "col"}},
            {"id": "col", "type": "collect",
             "config": {"slot": "s", "prompt_ref": "t/pedir",
                        "validator": {"kind": "decide", "value": "modelo@1"}},
             "next": {"ok": "conf", "max_attempts": "conf"}},
            {"id": "conf", "type": "confirm",
             "config": {"action": {"tool": "escribir@1", "args": {}}, "summary_template": "t/resumen",
                        "reprompt_template": "t/aclarar"},
             "next": {"yes": "gen", "no": "gen", "unclear": "gen", "max_attempts": "gen"}},
            {"id": "gen", "type": "respond",
             "config": {"generate": {"prompt_ref": "p/gen", "fallback_template_ref": "t/seguro"}},
             "next": {"next": "fin"}},
            {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
        ],
    }


def test_flow_ref_site_pointers_navigate_the_dump() -> None:
    fl = flow(_all_sites_flow())  # type: ignore[arg-type]
    sites = flow_ref_sites(fl)
    _check_sites(fl, sites)
    got = {(s.node_id, s.kind, pointer_str(s.pointer), str(s.ref)) for s in sites}
    assert got == {
        ("dec", EntityKind.decision_model, "/nodes/0/config/model", "modelo@1"),
        ("pol", EntityKind.policy, "/nodes/1/config/policy", "pol@1"),
        ("col", EntityKind.template, "/nodes/2/config/prompt_ref", "t/pedir"),
        ("col", EntityKind.decision_model, "/nodes/2/config/validator/value", "modelo@1"),
        ("conf", EntityKind.tool, "/nodes/3/config/action/tool", "escribir@1"),
        ("conf", EntityKind.template, "/nodes/3/config/summary_template", "t/resumen"),
        ("conf", EntityKind.template, "/nodes/3/config/reprompt_template", "t/aclarar"),
        ("gen", EntityKind.prompt, "/nodes/4/config/generate/prompt_ref", "p/gen"),
        ("gen", EntityKind.template, "/nodes/4/config/generate/fallback_template_ref", "t/seguro"),
    }


def test_non_decide_validator_is_not_a_ref_site() -> None:
    d = _all_sites_flow()
    d["nodes"][2]["config"]["validator"] = {"kind": "regex", "value": "^a$"}  # type: ignore[index]
    assert all(s.node_id != "col" or s.kind is EntityKind.template for s in flow_ref_sites(flow(d)))  # type: ignore[arg-type]


def test_agent_ref_site_pointers_navigate_the_dump() -> None:
    ag = agent(understand="modelo@1")
    sites = agent_ref_sites(ag)
    _check_sites(ag, sites)
    kinds = {pointer_str(s.pointer): s.kind for s in sites}
    assert kinds["/entry_flow"] is EntityKind.flow
    assert kinds["/understand"] is EntityKind.decision_model
    assert kinds["/tools_allowed/1"] is EntityKind.tool
    assert kinds["/templates/clarify"] is EntityKind.template
    assert len([s for s in sites if s.pointer[0] == "templates"]) == len(AGENT["templates"])
    assert entity_ref_sites(ag) == sites


def test_entity_ref_sites_dispatch() -> None:
    reg = registry()
    prompt = reg.get_exact(EntityKind.prompt, "p/gen", "1.0.0")
    sites = entity_ref_sites(prompt)
    assert [(s.kind, pointer_str(s.pointer), str(s.ref)) for s in sites] == [
        (EntityKind.model_profile, "/model_profile", "perfil@1")
    ]
    _check_sites(prompt, sites)
    fl = flow(base())
    assert entity_ref_sites(fl) == flow_ref_sites(fl)
    assert entity_ref_sites(reg.get_exact(EntityKind.tool, "leer", "1.0.0")) == []


def test_pointer_str() -> None:
    assert pointer_str(("nodes", 0, "config", "model")) == "/nodes/0/config/model"
    assert pointer_str(()) == "/"
