from pathlib import Path

import pytest

from agent_core.domain import (
    EntityKind,
    EntityRef,
    Flow,
    LanguageDetection,
    RefSpec,
    SchemaError,
    require_exact_refs,
)
from agent_core.flows.claims import derive_claims
from agent_core.flows.pin import pin_release
from agent_core.flows.refs import entity_ref_sites
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl, kind_of, load_registry
from agent_core.flows.view import release_view
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.registry_dir import registry_from_directory
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


def _flow_version(version: str) -> Flow:
    return flow({**base(), "version": version})


# T-M1-43
def test_pin_fixture_release() -> None:
    reg, violations = load_registry(FIXTURE)
    assert violations == []
    pinned = pin_release(reg, "demo")
    assert pinned.release.status == "active"
    assert pinned.release.entities[EntityKind.flow] == {"disputa-cargo": "1.0.0"}
    assert pinned.release.entities[EntityKind.decision_model] == {"match-cargo": "2.0.0"}
    assert str(pinned.release.language_detection) == "lang-es-pt@1.0.0"
    assert pinned.aliases == {"atencion": ["prod"]}
    for entity in pinned.entities:
        require_exact_refs(entity)
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    loaded = memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow)
    assert str(loaded.nodes[1].config.tool) == "buscar_transacciones@1.0.0"  # type: ignore[union-attr]


def test_pin_conflicting_versions() -> None:
    reg = AuthoringRegistry.from_entities(
        [*ENTITIES, _flow_version("1.0.0"), _flow_version("1.1.0"), agent(entry_flow="base@^1")], [_decl()]
    )
    with pytest.raises(SchemaError, match="base"):
        pin_release(reg, "r")


def test_pin_unknown_release() -> None:
    with pytest.raises(SchemaError):
        pin_release(AuthoringRegistry(), "nada")


def test_pin_unresolved_lists_everything_and_pins_nothing() -> None:
    reg = AuthoringRegistry.from_entities([*ENTITIES, _flow_version("1.0.0"), agent()], [_decl()])
    with pytest.raises(SchemaError, match=r"lang@1.*no resuelve"):
        pin_release(reg, "r")


def test_pin_incompatible_range_is_schema_error() -> None:
    reg = AuthoringRegistry.from_entities(
        [*ENTITIES, LANG, _flow_version("1.0.0"), agent(entry_flow="base@^2")], [_decl(flows=["base@^2"])]
    )
    with pytest.raises(SchemaError, match="base@\\^2"):
        pin_release(reg, "r")


def test_pin_refuses_closure_with_violations() -> None:
    broken = base()
    broken["nodes"][1]["config"]["tool"] = "no_existe@1"  # G0-02
    reg = AuthoringRegistry.from_entities([*ENTITIES, LANG, flow(broken), agent()], [_decl()])
    with pytest.raises(SchemaError, match="G0-02"):
        pin_release(reg, "r")


def _range_registry(*versions: str, order: str = "asc") -> AuthoringRegistry:
    flows = [_flow_version(v) for v in versions]
    if order == "desc":
        flows.reverse()
    return AuthoringRegistry.from_entities(
        [*ENTITIES, LANG, *flows, agent(entry_flow="base@^1")], [_decl(flows=["base@^1"])]
    )


def test_range_picks_highest_and_later_versions_do_not_change_pin() -> None:
    reg = _range_registry("1.0.0", "1.3.0")
    pinned = pin_release(reg, "r")
    assert pinned.release.entities[EntityKind.flow] == {"base": "1.3.0"}
    agent_entity = next(e for e in pinned.entities if e.id == "atencion")
    assert str(agent_entity.entry_flow) == "base@1.3.0"  # type: ignore[attr-defined]
    reg.add(_flow_version("1.4.0"))
    assert pin_release(reg, "r").release.entities[EntityKind.flow] == {"base": "1.4.0"}
    assert pinned.release.entities[EntityKind.flow] == {"base": "1.3.0"}  # lo ya fijado es inmutable


def test_pin_is_deterministic_regardless_of_insertion_order() -> None:
    first = pin_release(_range_registry("1.0.0", "1.3.0", "1.10.0"), "r")
    second = pin_release(_range_registry("1.0.0", "1.3.0", "1.10.0", order="desc"), "r")
    assert first.release.entities[EntityKind.flow] == {"base": "1.10.0"}
    assert first.release.model_dump_json() == second.release.model_dump_json()
    assert [e.model_dump_json() for e in first.entities] == [e.model_dump_json() for e in second.entities]
    assert first.aliases == second.aliases


def test_pin_release_with_only_exact_refs_matches_authoring() -> None:
    reg = AuthoringRegistry.from_entities([*ENTITIES, LANG, _flow_version("1.0.0"), agent()], [_decl()])
    pinned = pin_release(reg, "r")
    assert {e.id for e in pinned.entities if isinstance(e, Flow)} == {"base"}
    assert pinned.aliases == {"atencion": ["prod"]}


def _walk_every_site(reg: AuthoringRegistry, release_id: str) -> int:
    """Completitud: toda referencia de toda entidad fijada resuelve por `release_view`."""
    pinned = pin_release(reg, release_id)
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    view = release_view(memory, pinned.release)
    count = 0
    for entity in pinned.entities:
        for site in entity_ref_sites(entity):
            resolved = view.resolve(site.kind, site.ref)
            assert resolved is not None, (entity.id, site.pointer)
            assert (resolved.id, resolved.version) == (site.ref.id, site.ref.spec)
            count += 1
        # cada entidad fijada está declarada en la tabla de la release
        assert pinned.release.entities[kind_of(entity)][entity.id] == entity.version
    ld = pinned.release.language_detection
    assert view.resolve(EntityKind.language_detection, RefSpec.parse(str(ld))) is not None
    return count


def test_every_ref_site_resolves_through_release_view_fixture() -> None:
    reg, _ = load_registry(FIXTURE)
    assert _walk_every_site(reg, "demo") > 20


def test_every_ref_site_resolves_through_release_view_ranges() -> None:
    assert _walk_every_site(_range_registry("1.0.0", "1.3.0"), "r") > 10


# T-M1-45
def test_claims_same_with_release_view() -> None:
    reg, _ = load_registry(FIXTURE)
    pinned = pin_release(reg, "demo")
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    authoring = reg.resolve(EntityKind.flow, RefSpec.parse("disputa-cargo"))
    assert isinstance(authoring, Flow)
    runtime = memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow)
    view = release_view(memory, pinned.release)
    assert dict(derive_claims(runtime, view)) == dict(derive_claims(authoring, reg))
    assert dict(derive_claims(runtime, view)) == {
        "responder_ok": frozenset({"confirmar"}), "aclarar": frozenset(), "no_confirmado": frozenset()}


def test_registry_from_directory() -> None:
    memory = registry_from_directory(FIXTURE, "demo")
    assert memory.release_status("demo") == "active"
    assert memory.get(EntityRef.parse("disputa-cargo@1.0.0"), Flow).id == "disputa-cargo"
