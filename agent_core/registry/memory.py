"""`RegistryStore` en memoria para pruebas: un lock global (equivale a FOR UPDATE) y rollback por copia."""

import copy
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_core.registry.blobs import BlobStore, InMemoryBlobStore
from agent_core.registry.models import (
    AliasChange,
    Approval,
    DraftWrite,
    EntityDraft,
    EvalRun,
    Proposal,
    RegistryEvent,
    StoredRelease,
    StoredVersion,
    VersionRef,
)
from agent_core.registry.store import RegistryTx, Status


@dataclass
class _State:
    proposals: dict[str, Proposal] = field(default_factory=dict)
    changes: dict[str, list[EntityDraft]] = field(default_factory=dict)
    versions: dict[VersionRef, StoredVersion] = field(default_factory=dict)
    releases: dict[str, StoredRelease] = field(default_factory=dict)
    release_refs: dict[str, list[VersionRef]] = field(default_factory=dict)
    status: dict[str, Status] = field(default_factory=dict)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    alias_log: list[AliasChange] = field(default_factory=list)
    eval_runs: list[EvalRun] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    events: list[RegistryEvent] = field(default_factory=list)
    publish_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    draft_writes: dict[str, DraftWrite] = field(default_factory=dict)
    blobs: InMemoryBlobStore = field(default_factory=InMemoryBlobStore)


class _Tx:
    def __init__(self, state: _State, fail_on: Callable[[str], bool] | None) -> None:
        self._s = state
        self._fail_on = fail_on
        self.blobs: BlobStore = state.blobs

    def __getattribute__(self, name: str) -> Any:
        fail_on = object.__getattribute__(self, "_fail_on")
        if fail_on is not None and not name.startswith("_") and fail_on(name):
            raise RuntimeError(f"falla inyectada en {name}")
        return object.__getattribute__(self, name)

    def get_proposal(self, proposal_id: str, *, for_update: bool = False) -> Proposal | None:
        return self._s.proposals.get(proposal_id)

    def list_proposals(self) -> list[Proposal]:
        return list(self._s.proposals.values())

    def save_proposal(self, proposal: Proposal) -> None:
        self._s.proposals[proposal.proposal_id] = proposal

    def get_changes(self, proposal_id: str) -> list[EntityDraft]:
        return list(self._s.changes.get(proposal_id, []))

    def replace_changes(self, proposal_id: str, drafts: Sequence[EntityDraft]) -> None:
        self._s.changes[proposal_id] = list(drafts)

    def get_version(self, ref: VersionRef) -> StoredVersion | None:
        return self._s.versions.get(ref)

    def list_versions(self, kind: str, entity_id: str) -> list[StoredVersion]:
        return [v for r, v in self._s.versions.items() if (r.kind, r.id) == (kind, entity_id)]

    def insert_version(self, version: StoredVersion) -> None:
        if version.ref in self._s.versions:
            raise ValueError(f"{version.ref} ya existe")
        self._s.versions[version.ref] = version

    def get_release(self, release_id: str) -> StoredRelease | None:
        return self._s.releases.get(release_id)

    def release_refs(self, release_id: str) -> list[VersionRef]:
        return list(self._s.release_refs.get(release_id, []))

    def insert_release(self, stored: StoredRelease, refs: Sequence[VersionRef]) -> None:
        rid = stored.release.id
        if rid in self._s.releases:
            raise ValueError(f"la release {rid} ya existe")
        self._s.releases[rid] = stored
        self._s.release_refs[rid] = list(refs)
        self._s.status[rid] = "active"

    def release_status(self, release_id: str) -> Status | None:
        return self._s.status.get(release_id)

    def set_release_status(self, release_id: str, status: Status) -> None:
        self._s.status[release_id] = status

    def latest_release_for_agent_version(self, agent_id: str, version: str) -> str | None:
        found = [r for r in self._s.releases.values() if (r.agent_id, r.agent_version) == (agent_id, version)]
        return max(found, key=lambda r: (r.published_at, r.release.id)).release.id if found else None

    def lock_agent(self, agent_id: str) -> None:
        """Sin efecto: el lock global de la transacción ya serializa todo."""

    def get_alias(self, agent_id: str, alias: str, *, for_update: bool = False) -> str | None:
        return self._s.aliases.get((agent_id, alias))

    def set_alias(self, change: AliasChange) -> None:
        self._s.aliases[(change.agent_id, change.alias)] = change.after
        self._s.alias_log.append(change)

    def aliases_to(self, release_id: str) -> list[tuple[str, str]]:
        return sorted(k for k, v in self._s.aliases.items() if v == release_id)

    def aliases_named(self, alias: str) -> list[tuple[str, str]]:
        return sorted((a, rid) for (a, name), rid in self._s.aliases.items() if name == alias)

    def insert_eval_run(self, run: EvalRun) -> None:
        self._s.eval_runs.append(run)

    def latest_eval_run(self, proposal_id: str, candidate_hash: str) -> EvalRun | None:
        key = (proposal_id, candidate_hash)
        runs = [r for r in self._s.eval_runs if (r.proposal_id, r.candidate_hash) == key]
        return runs[-1] if runs else None

    def insert_approval(self, approval: Approval) -> None:
        self._s.approvals.append(approval)

    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None:
        key = (proposal_id, candidate_hash)
        found = [a for a in self._s.approvals if (a.proposal_id, a.candidate_hash) == key]
        return found[-1] if found else None

    def latest_decision(self, proposal_id: str) -> Approval | None:
        found = [a for a in self._s.approvals if a.proposal_id == proposal_id]
        return found[-1] if found else None

    def append_event(self, event: RegistryEvent) -> None:
        self._s.events.append(event)

    def events(self) -> list[RegistryEvent]:
        return list(self._s.events)

    def get_publish_key(self, key: str) -> tuple[str, str] | None:
        return self._s.publish_keys.get(key)

    def put_publish_key(self, key: str, proposal_id: str, release_id: str) -> None:
        self._s.publish_keys[key] = (proposal_id, release_id)

    def get_draft_write(self, key: str) -> DraftWrite | None:
        return self._s.draft_writes.get(key)

    def put_draft_write(self, write: DraftWrite) -> None:
        if write.idempotency_key in self._s.draft_writes:
            raise ValueError(f"la clave {write.idempotency_key} ya existe")
        self._s.draft_writes[write.idempotency_key] = write

    def get_eval_run(self, eval_run_id: str) -> EvalRun | None:
        return next((r for r in self._s.eval_runs if r.eval_run_id == eval_run_id), None)

    def count_created_after(self, origin: str, after: datetime) -> int:
        return sum(1 for e in self._s.events
                   if e.type == "proposal_created" and e.origin == origin and e.at > after)

    def count_eval_runs(self, proposal_id: str) -> int:
        return sum(1 for r in self._s.eval_runs if r.proposal_id == proposal_id)


class InMemoryRegistryStore:
    def __init__(self, fail_on: Callable[[str], bool] | None = None) -> None:
        self._state = _State()
        self._lock = threading.RLock()
        self.fail_on = fail_on

    @contextmanager
    def transaction(self) -> Iterator[RegistryTx]:
        with self._lock:
            snapshot = copy.deepcopy(self._state)
            try:
                yield _Tx(self._state, self.fail_on)
            except BaseException:
                self._state = snapshot
                raise
