import pytest

from agent_core.domain import EntityKind, RefSpec, Template, ToolDef
from agent_core.flows.refs import flow_ref_sites
from agent_core.flows.view import best_version, parse_version, satisfies
from tests.m01.cases import base, flow, registry


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
