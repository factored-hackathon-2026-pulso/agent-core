"""Regla 8 (registry §2): el motor solo lee lo publicado. Es la condición que hace admisible `write_draft`
(ADR 0019 §5, spec write-draft D1 y D2)."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "agent_core"
RUNTIME = ROOT / "registry" / "postgres" / "runtime.py"
# Lo único que el RegistryPort de producción le pide a la transacción: todo es contenido publicado.
ALLOWED_TX_CALLS = {"get_release", "release_status", "get_alias", "latest_release_for_agent_version",
                    "get_version", "blobs"}


def test_the_runtime_registry_only_touches_published_content() -> None:
    tree = ast.parse(RUNTIME.read_text("utf-8"))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "tx"}
    assert used <= ALLOWED_TX_CALLS, sorted(used - ALLOWED_TX_CALLS)
    for forbidden in ("get_proposal", "get_changes", "get_draft_write"):
        assert forbidden not in used


def test_snapshot_registry_is_only_built_by_the_registry_service() -> None:
    users = {path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*.py")
             if "SnapshotRegistry" in path.read_text("utf-8")}
    allowed = {"registry/__init__.py", "registry/service.py", "registry/snapshot.py"}
    assert users == allowed, sorted(users ^ allowed)
