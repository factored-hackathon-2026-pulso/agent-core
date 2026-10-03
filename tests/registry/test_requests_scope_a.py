"""Solicitudes N-01, N-03, N-05 y N-10 al registry (§31.12): contrato publicado, release reconstruible,
equivalencia de formas de referencia y `eval_run_id` en `gate_failed`."""

import json
from pathlib import Path

import pytest

from agent_core.contracts import render_contracts
from agent_core.domain import Agent, EntityKind, Release
from agent_core.registry.candidate import release_hash
from agent_core.registry.entities import content_hash
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import Origin
from tests.registry.helpers import AGENT, demo_pinned, human, prompt_draft
from tests.registry.service_world import SUITE, World, publish_cycle

ANA = human()


def test_n01_the_registry_models_are_published_as_json_schema() -> None:
    files = render_contracts()
    for name in ("EvalSuite", "EntityDraft", "VersionDocs", "PutDraftBody", "ReleaseDetail", "EvalRun"):
        assert f"registry/{name}.json" in files
    assert "interrupts" in json.loads(files["registry/ReleaseDetail.json"])["properties"]


def test_n01_the_checked_in_registry_contracts_are_current() -> None:
    root = Path(__file__).resolve().parents[2] / "contracts"
    for rel, content in render_contracts().items():
        if rel.startswith("registry/"):
            assert (root / rel).read_bytes() == content.encode("utf-8"), rel


def test_n03_release_detail_rebuilds_the_release_hash() -> None:
    w = World()
    rid = publish_cycle(w, [prompt_draft(), SUITE])
    detail = w.service.get_release(rid)
    entities: dict[EntityKind, dict[str, str]] = {}
    for e in detail.entities:
        entities.setdefault(EntityKind(e.ref.kind), {})[e.ref.id] = e.ref.version
    rebuilt = Release(
        id=rid, status="active", entities=entities, interrupts=detail.interrupts,
        language_detection=detail.language_detection, injection_ruleset=detail.injection_ruleset,
        knowledge_snapshot=detail.knowledge_snapshot, max_input_chars=detail.max_input_chars)  # type: ignore[arg-type]
    with w.store.transaction() as tx:
        stored = tx.get_release(rid)
    assert stored is not None and release_hash(rebuilt) == stored.release_hash


def test_n05_both_ref_forms_give_the_same_content_hash() -> None:
    agent = next(e for e in demo_pinned().entities if isinstance(e, Agent) and e.id == AGENT)
    as_object = agent.model_dump(mode="json", by_alias=True)
    as_text = {**as_object, "entry_flow": f"{agent.entry_flow.id}@{agent.entry_flow.spec}",
               "tools_allowed": [f"{r.id}@{r.spec}" for r in agent.tools_allowed]}
    assert as_text != as_object
    assert content_hash(Agent.model_validate(as_text)) == content_hash(agent)
    assert content_hash(Agent.model_validate(as_object)) == content_hash(agent)


def _failing_world() -> tuple[World, str]:
    w = World()
    w.evaluator.reports.append(EvalReport(verdict="fail"))
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "t")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    return w, p.proposal_id


def test_n10_gate_failed_carries_the_eval_run_id_and_it_survives_a_replay() -> None:
    w, pid = _failing_world()
    ids = []
    for _ in range(2):
        with pytest.raises(RegistryError) as info:
            w.service.evaluate(ANA, pid, "disputas-suite", idempotency_key="k1")
        assert info.value.code is RegistryErrorCode.gate_failed
        payload = info.value.payload
        assert isinstance(payload, dict) and payload["verdict"] == "fail"
        ids.append(payload["eval_run_id"])
    assert isinstance(ids[0], str) and ids[0] and ids[0] == ids[1]
    with w.store.transaction() as tx:
        run = tx.get_eval_run(ids[0])
    assert run is not None and run.verdict == "fail" and run.proposal_id == pid
