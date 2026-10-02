"""Carga un registro de autoría, lo valida, fija una o varias releases y las deja en un InMemoryRegistry
(M1 §2)."""

from collections.abc import Iterable, Sequence
from pathlib import Path

from agent_core.domain import SchemaError
from agent_core.flows import Violation, load_registry, pin_release, validate_registry
from testing.fakes.registry import InMemoryRegistry


def _raise_if_invalid(problems: Sequence[Violation]) -> None:
    if problems:
        shown = [f"{v.rule} {v.path}: {v.message}"[:240] for v in problems[:20]]
        detail = "; ".join(shown) + (f"; y {len(problems) - 20} más" if len(problems) > 20 else "")
        raise SchemaError("registro inválido: " + detail)


def registry_from_releases(root: Path, release_ids: Iterable[str] | None = None) -> InMemoryRegistry:
    """Like `registry_from_directory` for several releases (one per agent, as `registry import` requires).

    `None` pins every release of the directory."""
    reg, violations = load_registry(root)
    _raise_if_invalid([*violations, *validate_registry(reg)])
    memory = InMemoryRegistry()
    for release_id in (list(release_ids) if release_ids is not None else [d.id for d in reg.releases()]):
        pinned = pin_release(reg, release_id)
        memory.add(*pinned.entities)
        for agent_id, aliases in pinned.aliases.items():
            for alias in aliases:
                memory.add_release(pinned.release, agent_id, alias=alias)
    return memory


def registry_from_directory(root: Path, release_id: str) -> InMemoryRegistry:
    return registry_from_releases(root, [release_id])
