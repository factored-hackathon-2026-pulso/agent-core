import json
from pathlib import Path
from typing import Any

import pytest

from agent_core.domain import (
    EntityKind,
    EntityRef,
    Flow,
    InjectionRuleset,
    KnowledgeSnapshot,
    LanguageDetection,
    RefSpec,
    SchemaError,
    require_exact_refs,
)
from agent_core.flows import pin as pin_module
from agent_core.flows.claims import derive_claims
from agent_core.flows.pin import pin_release
from agent_core.flows.refs import entity_ref_sites
from agent_core.flows.registry import AuthoringRegistry, ReleaseAgent, ReleaseDecl, kind_of, load_registry
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


INJECTION = InjectionRuleset.model_validate(
    {"id": "inj", "version": "1.0.0", "rules": [{"id": "r1", "pattern": "ignora", "kind": "phrase"}]}
)


def test_interrupts_and_injection_resolve_through_release_view() -> None:
    interrupts = [
        {"id": "salir", "priority": 1, "action": {"type": "start_flow", "flow": "base@^1"},
         "signal_policy": "pol@1"},
        {"id": "humano", "priority": 2,
         "action": {"type": "escalate", "target_queue": "q", "priority": "high"}},
    ]
    decl = _decl(flows=["base@^1"], interrupts=interrupts, injection_ruleset="inj@1")
    reg = AuthoringRegistry.from_entities(
        [*ENTITIES, LANG, INJECTION, _flow_version("1.0.0"), _flow_version("1.2.0"), agent()], [decl]
    )
    pinned = pin_release(reg, "r")
    assert str(pinned.release.injection_ruleset) == "inj@1.0.0"
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    view = release_view(memory, pinned.release)
    start = pinned.release.interrupts[0]
    assert str(start.action.flow) == "base@1.2.0"  # type: ignore[union-attr]
    assert view.resolve(EntityKind.flow, start.action.flow) is not None  # type: ignore[union-attr]
    assert start.signal_policy is not None
    assert view.resolve(EntityKind.policy, start.signal_policy) is not None
    assert pinned.release.injection_ruleset is not None
    injection = RefSpec.parse(str(pinned.release.injection_ruleset))
    assert view.resolve(EntityKind.injection_ruleset, injection) is not None
    assert _walk_every_site(reg, "r") > 10


def _flow_with(node: dict[str, Any]) -> Flow:
    doc = base()
    doc["nodes"].append(node)
    return flow(doc)


PRODUCTION_NODES = [
    {"id": "sub", "type": "subflow", "config": {"flow": "otro@%s"}},
    {"id": "ag", "type": "agent", "config": {"tools_allowed": ["leer@%s"], "max_steps": 3,
                                              "prompt_ref": "p/gen@%s", "goal": "x"}},
]


@pytest.mark.parametrize("spec", ["1.0.0", "1"])
@pytest.mark.parametrize("node", PRODUCTION_NODES, ids=["subflow", "agent"])
def test_pin_fails_closed_on_unknown_ref_sites(
    monkeypatch: pytest.MonkeyPatch, node: dict[str, Any], spec: str
) -> None:
    monkeypatch.setattr(pin_module, "validate_flow", lambda flow, reg: [])
    text = str(node).replace("%s", spec).replace("'", '"')
    built = json.loads(text)
    reg = AuthoringRegistry.from_entities(
        [*ENTITIES, LANG, _flow_with(built), flow({**base(), "id": "otro"}), agent()], [_decl()]
    )
    with pytest.raises(SchemaError, match="referencia sin fijar"):
        pin_release(reg, "r")


@pytest.mark.parametrize("alias", ["PROD !", "a b", ""])
def test_pin_rejects_invalid_alias(alias: str) -> None:
    entry = ReleaseAgent.model_construct(agent=RefSpec.parse("atencion@1"), aliases=[alias])  # sin validar
    decl = _decl().model_copy(update={"agents": [entry]})
    reg = AuthoringRegistry.from_entities([*ENTITIES, LANG, _flow_version("1.0.0"), agent()], [decl])
    with pytest.raises(SchemaError, match="alias"):
        pin_release(reg, "r")


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


# --- Snapshot de conocimiento en la clausura (registry, ADR 0017; spec del registry §15) ---

def _snapshot(version: str, path: str = "faq/disputas") -> KnowledgeSnapshot:
    return KnowledgeSnapshot.model_validate(
        {"id": "kb", "version": version, "pages": [
            {"path": path, "hash": "a" * 64, "audience": "public", "status": "approved",
             "approved_by": "aprobador-1", "lang": "es"}]}
    )


def _knowledge_registry(*snapshots: KnowledgeSnapshot, knowledge: str | None = "kb@^1") -> AuthoringRegistry:
    decl = _decl(knowledge=knowledge) if knowledge is not None else _decl()
    return AuthoringRegistry.from_entities(
        [*ENTITIES, LANG, _flow_version("1.0.0"), agent(), *snapshots], [decl]
    )


def test_release_decl_knowledge_is_optional_and_a_valid_ref() -> None:
    assert _decl().knowledge is None
    assert str(_decl(knowledge="kb@^1").knowledge) == "kb@^1"
    with pytest.raises(ValueError):
        _decl(knowledge="kb@")


def test_kind_of_knowledge_snapshot() -> None:
    assert kind_of(_snapshot("1.0.0")) is EntityKind.knowledge_snapshot


def test_pin_fixes_the_highest_snapshot_in_release_and_closure() -> None:
    pinned = pin_release(_knowledge_registry(_snapshot("1.0.0"), _snapshot("1.2.0", "faq/otra")), "r")
    assert str(pinned.release.knowledge_snapshot) == "kb@1.2.0"
    assert pinned.release.entities[EntityKind.knowledge_snapshot] == {"kb": "1.2.0"}
    (snapshot,) = [e for e in pinned.entities if isinstance(e, KnowledgeSnapshot)]
    assert snapshot.version == "1.2.0"
    require_exact_refs(pinned.release)


def test_pinned_snapshot_resolves_through_the_runtime_port_and_view() -> None:
    pinned = pin_release(_knowledge_registry(_snapshot("1.0.0")), "r")
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    memory.add_release(pinned.release, "atencion")
    assert pinned.release.knowledge_snapshot is not None
    assert memory.get(pinned.release.knowledge_snapshot, KnowledgeSnapshot).pages[0].path == "faq/disputas"
    view = release_view(memory, pinned.release)
    assert view.resolve(EntityKind.knowledge_snapshot, RefSpec.parse("kb@1")) is not None


def test_pin_without_knowledge_leaves_the_snapshot_unset() -> None:
    pinned = pin_release(_knowledge_registry(_snapshot("1.0.0"), knowledge=None), "r")
    assert pinned.release.knowledge_snapshot is None
    assert EntityKind.knowledge_snapshot not in pinned.release.entities
    assert not any(isinstance(e, KnowledgeSnapshot) for e in pinned.entities)


def test_pin_unresolved_knowledge_is_a_schema_error() -> None:
    with pytest.raises(SchemaError, match=r"knowledge.*kb@\^2.*no resuelve"):
        pin_release(_knowledge_registry(_snapshot("1.0.0"), knowledge="kb@^2"), "r")


def test_pin_with_knowledge_is_deterministic() -> None:
    first = pin_release(_knowledge_registry(_snapshot("1.0.0"), _snapshot("1.1.0", "faq/otra")), "r")
    second = pin_release(_knowledge_registry(_snapshot("1.1.0", "faq/otra"), _snapshot("1.0.0")), "r")
    assert first.release.model_dump_json() == second.release.model_dump_json()
    assert [e.model_dump_json() for e in first.entities] == [e.model_dump_json() for e in second.entities]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_SNAPSHOT_YAML = """\
id: kb
version: 1.0.0
pages:
  - {{path: {path}, hash: '{digest}', audience: public, status: {status}, lang: es{extra}}}
"""


def test_loader_reads_knowledge_snapshots_and_the_release_reference(tmp_path: Path) -> None:
    _write(tmp_path / "knowledge_snapshots" / "kb@1.0.0.yaml", _SNAPSHOT_YAML.format(
        path="faq/disputas", digest="a" * 64, status="approved", extra=", approved_by: aprobador-1"))
    _write(tmp_path / "releases" / "r.yaml",
           "id: r\nagents: [{agent: atencion@1}]\nlanguage_detection: lang@1\nknowledge: kb@^1\n")
    reg, violations = load_registry(tmp_path)
    assert violations == []
    assert isinstance(reg.get_exact(EntityKind.knowledge_snapshot, "kb", "1.0.0"), KnowledgeSnapshot)
    decl = reg.release("r")
    assert decl is not None and str(decl.knowledge) == "kb@^1"


def test_loader_reports_an_invalid_snapshot_without_aborting(tmp_path: Path) -> None:
    _write(tmp_path / "knowledge_snapshots" / "kb@1.0.0.yaml", _SNAPSHOT_YAML.format(
        path="'../secreto'", digest="a" * 64, status="draft", extra=""))
    reg, violations = load_registry(tmp_path)
    assert [v.rule for v in violations] == ["G0-01"]
    assert reg.versions(EntityKind.knowledge_snapshot, "kb") == []
