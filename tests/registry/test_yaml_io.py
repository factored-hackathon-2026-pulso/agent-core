from pathlib import Path

import pytest

from agent_core.domain import EntityKind
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.service import RegistryService
from agent_core.registry.yaml_io import FOLDERS, dump_entities, load_seed
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, REGISTRY_DEMO, admin, bot, demo_pinned
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
