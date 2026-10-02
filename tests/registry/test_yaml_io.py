import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from agent_core.domain import EntityKind
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import VersionRef
from agent_core.registry.service import RegistryService
from agent_core.registry.yaml_io import FOLDERS, dump_entities, load_seed
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, REGISTRY_DEMO, admin, bot, demo_pinned, suite_content
from tests.registry.service_world import FakeEvaluator


def _service() -> tuple[RegistryService, InMemoryRegistryStore]:
    store = InMemoryRegistryStore()
    return RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds()), store


def test_folders_cover_every_kind() -> None:
    assert set(FOLDERS) == {k.value for k in EntityKind} | {"eval_suite"}


def test_import_demo_creates_published_release_with_both_aliases() -> None:  # T-REG-26
    service, store = _service()
    [detail] = service.import_seed(admin(), REGISTRY_DEMO)
    with store.transaction() as tx:
        assert tx.get_alias(AGENT, "staging") == tx.get_alias(AGENT, "prod") == detail.release_id
        assert tx.events()[-1].type == "imported"
    assert detail.status == "active" and detail.proposal_id is None


def test_import_twice_fails() -> None:
    service, _ = _service()
    service.import_seed(admin(), REGISTRY_DEMO)
    with pytest.raises(RegistryError) as info:
        service.import_seed(admin(), REGISTRY_DEMO)
    assert info.value.code is RegistryErrorCode.illegal_transition


def test_import_requires_human_approver() -> None:
    service, _ = _service()
    with pytest.raises(RegistryError) as info:
        service.import_seed(bot("constructor", "aprobador"), REGISTRY_DEMO)
    assert info.value.code is RegistryErrorCode.forbidden_role


def test_export_then_load_keeps_content_hashes(tmp_path: Path) -> None:  # T-REG-26
    service, _ = _service()
    [detail] = service.import_seed(admin(), REGISTRY_DEMO)
    files = service.export(detail.release_id)
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    [pinned], _ = load_seed(tmp_path)
    assert {e.id: e.version for e in pinned.entities} == {e.id: e.version for e in demo_pinned().entities}
    again, _ = _service()
    [detail2] = again.import_seed(admin(), tmp_path)
    assert [e.content_hash for e in detail2.entities] == [e.content_hash for e in detail.entities]


def test_dump_is_deterministic() -> None:
    entities = demo_pinned().entities
    assert dump_entities(entities) == dump_entities(list(reversed(entities)))


def _seed_with_suite(tmp_path: Path, content: dict[str, Any]) -> Path:
    root = tmp_path / "seed"
    shutil.copytree(REGISTRY_DEMO, root)
    (root / "eval_suites").mkdir()
    (root / "eval_suites" / f"{content['id']}@{content['version']}.yaml").write_text(
        json.dumps(content), encoding="utf-8")  # JSON is valid YAML
    return root


def _assert_nothing_persisted(store: InMemoryRegistryStore) -> None:
    with store.transaction() as tx:
        assert tx.get_alias(AGENT, "staging") is None and tx.get_alias(AGENT, "prod") is None
        assert tx.events() == []
        assert tx.get_version(VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0")) is None


def test_import_refuses_a_seed_suite_with_a_dataset_scenario(tmp_path: Path) -> None:  # final review I-2
    dataset = {"id": "d1", "source": "dataset", "dataset_id": "casos", "dataset_hash": "a" * 64}
    root = _seed_with_suite(tmp_path, suite_content(scenarios=[dataset]))
    service, store = _service()
    with pytest.raises(RegistryError) as info:
        service.import_seed(admin(), root)
    assert info.value.code is RegistryErrorCode.validation_failed
    payload: Any = info.value.payload
    assert payload[0]["rule"] == "REG-SUITE" and payload[0]["message"].startswith("dataset_source_disabled")
    _assert_nothing_persisted(store)


def test_import_refuses_a_seed_suite_whose_agent_has_no_pinned_release(tmp_path: Path) -> None:  # I-2
    root = _seed_with_suite(tmp_path, suite_content(agent_id="agente-sin-release"))
    service, store = _service()
    with pytest.raises(RegistryError) as info:
        service.import_seed(admin(), root)
    assert info.value.code is RegistryErrorCode.validation_failed
    assert "disputas-suite" in info.value.detail
    _assert_nothing_persisted(store)
