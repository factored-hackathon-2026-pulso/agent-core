"""Carga un registro de autoría, lo valida, fija una release y lo deja en un InMemoryRegistry (M1 §2)."""

from pathlib import Path

from agent_core.domain import SchemaError
from agent_core.flows.pin import pin_release
from agent_core.flows.registry import load_registry
from agent_core.flows.validate import validate_registry
from testing.fakes.registry import InMemoryRegistry


def registry_from_directory(root: Path, release_id: str) -> InMemoryRegistry:
    reg, violations = load_registry(root)
    problems = [*violations, *validate_registry(reg)]
    if problems:
        shown = [f"{v.rule} {v.path}: {v.message}"[:240] for v in problems[:20]]
        detail = "; ".join(shown) + (f"; y {len(problems) - 20} más" if len(problems) > 20 else "")
        raise SchemaError("registro inválido: " + detail)
    pinned = pin_release(reg, release_id)
    memory = InMemoryRegistry()
    memory.add(*pinned.entities)
    for agent_id, aliases in pinned.aliases.items():
        for alias in aliases:
            memory.add_release(pinned.release, agent_id, alias=alias)
    return memory
