"""Referencias de autoría → exactas, con los pines de la release (sin importar `flows`)."""

from agent_core.domain import EntityKind, EntityRef, RefSpec, Release, SchemaError


def pinned_ref(release: Release, kind: EntityKind, ref: RefSpec) -> EntityRef:
    """La versión la fija la release; una referencia ya exacta sin pin se usa tal cual."""
    pinned = release.entities.get(kind, {}).get(ref.id)
    if pinned is not None:
        return EntityRef(id=ref.id, version=pinned)
    if ref.is_exact:
        return ref.require_exact()
    raise SchemaError(f"{kind.value} {ref} no está fijada en la release {release.id}")
