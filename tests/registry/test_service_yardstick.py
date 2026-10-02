"""Double yardstick in the registry service (ADR 0020): the base's yardstick and the recorded suite."""

import json
import shutil
from pathlib import Path

from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import VersionRef
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import REGISTRY_DEMO, admin, prompt_draft, suite_content, suite_draft
from tests.registry.service_world import FakeEvaluator, World, publish_cycle

SUITE_REF = VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0")


def test_publish_records_the_suite_used_by_the_gate() -> None:
    w = World()
    release_id = publish_cycle(w, [prompt_draft(), suite_draft()])
    assert w.service.get_release(release_id).eval_suite_refs == [SUITE_REF]
    assert w.service.get_release("rel-demo").eval_suite_refs == []


def test_a_base_without_recorded_suite_gives_a_metrics_only_old_yardstick() -> None:  # decision D3
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    first = w.evaluator.requests[0]
    assert first.base is not None and first.base.release.id == "rel-demo"
    assert first.old is not None and first.old.suite is None and first.old.metrics == []
    assert first.new.suite is not None and first.new.suite.version == "1.0.0"


def test_the_next_proposal_is_measured_with_the_suite_of_its_base() -> None:
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    publish_cycle(w, [prompt_draft(version="1.2.0", text="Otra variante."), suite_draft("1.1.0")], key="k2")
    second = w.evaluator.requests[-1]
    assert second.old is not None and second.old.suite is not None and second.new.suite is not None
    assert (second.old.suite.version, second.new.suite.version) == ("1.0.0", "1.1.0")


def test_import_records_the_seed_suite(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    shutil.copytree(REGISTRY_DEMO, root)
    (root / "eval_suites").mkdir()
    (root / "eval_suites" / "disputas-suite@1.0.0.yaml").write_text(json.dumps(suite_content()),
                                                                   encoding="utf-8")  # JSON is valid YAML
    service = RegistryService(InMemoryRegistryStore(), FakeEvaluator(), FakeClock(), FakeIds())
    [detail] = service.import_seed(admin(), root)
    assert detail.eval_suite_refs == [SUITE_REF]
