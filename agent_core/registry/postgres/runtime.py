"""`RegistryPort` de producción (spec §7.1): solo lo publicado; release cacheada por id; estado con TTL."""

from datetime import datetime, timedelta
from typing import Literal

from agent_core.domain import (
    ENTITY_KIND,
    AgentSelector,
    EntityRef,
    Principal,
    RegistryEntity,
    Release,
    require_exact_refs,
)
from agent_core.ports import Clock
from agent_core.registry.entities import decode_entity
from agent_core.registry.models import VersionRef
from agent_core.registry.postgres.store import PgRegistryStore


def _kind_name(model: type) -> str:
    return ENTITY_KIND[model].value


class PostgresRegistry:
    def __init__(self, store: PgRegistryStore, clock: Clock,
                 status_ttl: timedelta = timedelta(seconds=5)) -> None:
        self._store, self._clock, self._ttl = store, clock, status_ttl
        self._releases: dict[str, Release] = {}
        self._status: dict[str, tuple[Literal["active", "revoked"], datetime]] = {}

    def _release(self, release_id: str) -> Release:
        cached = self._releases.get(release_id)
        if cached is None:
            with self._store.transaction() as tx:
                stored = tx.get_release(release_id)
            if stored is None:
                raise KeyError(release_id)
            cached = stored.release
            require_exact_refs(cached)
            self._releases[release_id] = cached
        return cached

    def release(self, release_id: str) -> Release:
        """La release fijada de un run por su id (para `EngineDeps.releases`)."""
        return self._release(release_id)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        now = self._clock.now()
        hit = self._status.get(release_id)
        if hit is not None and now - hit[1] < self._ttl:
            return hit[0]
        with self._store.transaction() as tx:
            status = tx.release_status(release_id)
        if status is None:
            raise KeyError(release_id)
        self._status[release_id] = (status, now)
        return status

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        with self._store.transaction() as tx:
            if selector.version is not None:
                release_id = tx.latest_release_for_agent_version(selector.id, selector.version)
            else:
                assert selector.alias is not None
                release_id = tx.get_alias(selector.id, selector.alias)
        if release_id is None:
            raise KeyError(str(selector.id))
        if self.release_status(release_id) != "active":
            raise KeyError(f"{release_id} revocada")
        release = self._release(release_id).model_copy(deep=True, update={"status": "active"})
        return release.model_copy(update={"id": release_id})

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        vref = VersionRef(kind=_kind_name(kind), id=ref.id, version=ref.version)
        with self._store.transaction() as tx:
            stored = tx.get_version(vref)
            if stored is None:
                raise KeyError(str(vref))
            data = tx.blobs.get(stored.content_hash)  # verifica el hash: IntegrityError
        entity = decode_entity(vref.kind, data)
        require_exact_refs(entity)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref.id}@{ref.version} no es {kind.__name__}")
        return entity
