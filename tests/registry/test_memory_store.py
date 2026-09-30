from datetime import timedelta

import pytest

from agent_core.domain import sha256_hex
from agent_core.registry.blobs import InMemoryBlobStore
from agent_core.registry.errors import IntegrityError
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AliasChange, Origin, Proposal, ProposalState
from testing.builders import NOW


def _proposal(pid: str = "prop-1") -> Proposal:
    return Proposal(proposal_id=pid, agent_id="atencion", origin=Origin.manual, state=ProposalState.draft,
                    base_release_id=None, title="t", created_by="ana", updated_at=NOW)


def test_blob_round_trip_and_integrity() -> None:  # T-REG-19 (memoria)
    blobs = InMemoryBlobStore()
    digest = blobs.put(b"hola")
    assert digest == sha256_hex(b"hola") and blobs.get(digest) == b"hola"
    blobs.corrupt(digest, b"otro")
    with pytest.raises(IntegrityError):
        blobs.get(digest)


def test_transaction_commits() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.save_proposal(_proposal())
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is not None


def test_exception_rolls_back_everything() -> None:
    store = InMemoryRegistryStore()
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.save_proposal(_proposal())
        tx.set_alias(AliasChange(agent_id="a", alias="staging", before=None, after="rel-1", actor="ana",
                                 reason="r", at=NOW))
        raise RuntimeError("falla a mitad")
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is None
        assert tx.get_alias("a", "staging") is None


def test_injected_failure_by_method_name() -> None:
    store = InMemoryRegistryStore(fail_on=lambda name: name == "set_alias")
    with pytest.raises(RuntimeError), store.transaction() as tx:
        tx.save_proposal(_proposal())
        tx.set_alias(AliasChange(agent_id="a", alias="staging", before=None, after="r", actor="x", reason="r",
                                 at=NOW + timedelta(seconds=1)))
    with store.transaction() as tx:
        assert tx.get_proposal("prop-1") is None


def test_returned_objects_do_not_alias_state() -> None:
    store = InMemoryRegistryStore()
    with store.transaction() as tx:
        tx.save_proposal(_proposal())
    with store.transaction() as tx:
        p = tx.get_proposal("prop-1")
        assert p is not None and p.model_copy(update={"title": "x"}).title == "x"
        assert tx.get_proposal("prop-1") == p
