"""Registro en memoria cargado con objetos Python (la carga desde YAML de agent-registry la agrega M1).

Sirve solo versiones exactas (búsqueda O(1) por `(tipo, id, versión)`; alias y versiones en diccionarios
separados), lo publicado es inmutable (republicar con otro contenido se rechaza) y toda entidad entra y sale
como copia profunda: ningún llamador puede mutar el registro. Los mensajes de error solo llevan ids."""

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from agent_core.domain import AgentSelector, EntityRef, Principal, RegistryEntity, Release, require_exact_refs

_EntityKey = tuple[type[BaseModel], str, str]


class InMemoryRegistry:
    def __init__(self) -> None:
        self._entities: dict[_EntityKey, BaseModel] = {}
        self._releases: dict[str, Release] = {}
        self._aliases: dict[tuple[str, str], str] = {}
        self._versions: dict[tuple[str, str], str] = {}

    def add(self, *entities: RegistryEntity) -> None:
        """Publica entidades. Todo o nada: un conflicto no deja publicada ninguna del lote."""
        pending: dict[_EntityKey, BaseModel] = {}
        for entity in entities:
            key: _EntityKey = (type(entity), entity.id, entity.version)
            existing = pending.get(key) or self._entities.get(key)
            if existing is not None and existing != entity:
                raise ValueError(f"{key[0].__name__} {key[1]}@{key[2]} ya está publicada con otro contenido")
            pending[key] = entity.model_copy(deep=True)
        self._entities.update(pending)

    def add_release(self, release: Release, agent_id: str, alias: str | None = "prod",
                    version: str | None = None) -> None:
        """Publica una release y la ata a una versión exacta del agente (si se da) o, si no, a un alias.

        La versión es inmutable (no se reasigna a otra release); un alias sí puede moverse."""
        if version is None and alias is None:
            raise ValueError("add_release necesita un alias o una versión exacta")
        selector = (AgentSelector(id=agent_id, version=version) if version is not None
                    else AgentSelector(id=agent_id, alias=alias))  # valida el formato
        existing = self._releases.get(release.id)
        if existing is not None and existing != release:
            raise ValueError(f"la release {release.id} ya está publicada con otro contenido")
        if selector.version is not None:
            bound = self._versions.get((agent_id, selector.version))
            if bound is not None and bound != release.id:
                raise ValueError(f"{agent_id}@{selector.version} ya apunta a otra release")
            self._versions[(agent_id, selector.version)] = release.id
        else:
            assert selector.alias is not None
            self._aliases[(agent_id, selector.alias)] = release.id
        self._releases[release.id] = release.model_copy(deep=True)

    def revoke(self, release_id: str) -> None:
        self._releases[release_id] = self._releases[release_id].model_copy(update={"status": "revoked"})

    def resolve_release(self, selector: AgentSelector, principal: Principal) -> Release:
        if selector.version is not None:
            release_id = self._versions[(selector.id, selector.version)]
        else:
            assert selector.alias is not None
            release_id = self._aliases[(selector.id, selector.alias)]
        release = self._releases[release_id]
        require_exact_refs(release)
        return release.model_copy(deep=True)

    def release_status(self, release_id: str) -> Literal["active", "revoked"]:
        return self._releases[release_id].status

    def get[T: RegistryEntity](self, ref: EntityRef, kind: type[T]) -> T:
        stored = self._entities[(kind, ref.id, ref.version)]
        require_exact_refs(stored)
        entity = stored.model_copy(deep=True)
        if not isinstance(entity, kind):
            raise TypeError(f"{ref.id}@{ref.version} no es {kind.__name__}")
        return entity


if TYPE_CHECKING:
    from agent_core.ports import RegistryPort

    def _conforms(x: InMemoryRegistry) -> RegistryPort:
        return x
