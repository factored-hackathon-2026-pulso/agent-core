"""Importación y exportación YAML (spec §5.6). Formato del loader de M1; nunca es fuente de verdad."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agent_core.domain import EntityKind, Release, SchemaError
from agent_core.flows import PinnedRelease, load_registry, load_yaml, pin_release, validate_registry
from agent_core.registry.entities import AnyEntity, entity_kind
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.suite import EvalSuite

FOLDERS: Mapping[str, str] = MappingProxyType({
    "agent": "agents", "flow": "flows", "policy": "policies", "template": "templates", "prompt": "prompts",
    "tool": "tools", "decision_model": "decision_models", "model_profile": "model_profiles",
    "language_detection": "language_detection", "injection_ruleset": "injection_rulesets",
    "knowledge_snapshot": "knowledge_snapshots", "eval_suite": "eval_suites",
})


def _yaml(data: Any) -> bytes:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=True, default_flow_style=False).encode("utf-8")


def dump_entities(entities: Iterable[AnyEntity]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for e in sorted(entities, key=lambda x: (entity_kind(x), x.id, x.version)):
        path = f"{FOLDERS[entity_kind(e)]}/{e.id}@{e.version}.yaml"
        out[path] = _yaml(e.model_dump(mode="json", by_alias=True, exclude_none=True))
    return out


def dump_release(release: Release, agent_id: str) -> dict[str, bytes]:
    data: dict[str, Any] = {
        "id": release.id,
        "agents": [{"agent": f"{agent_id}@{release.entities[EntityKind.agent][agent_id]}",
                    "aliases": ["staging", "prod"]}],
        "flows": [f"{i}@{v}" for i, v in sorted(release.entities.get(EntityKind.flow, {}).items())],
        "interrupts": [i.model_dump(mode="json", by_alias=True, exclude_none=True)
                       for i in release.interrupts],
        "language_detection": str(release.language_detection),
        "max_input_chars": release.max_input_chars,
    }
    if release.injection_ruleset is not None:
        data["injection_ruleset"] = str(release.injection_ruleset)
    if release.knowledge_snapshot is not None:
        data["knowledge"] = str(release.knowledge_snapshot)
    return {f"releases/{release.id}.yaml": _yaml(data)}


def _suites(root: Path) -> list[EvalSuite]:
    folder = root / FOLDERS["eval_suite"]
    if not folder.is_dir():
        return []
    return [EvalSuite.model_validate(load_yaml(p.read_bytes())) for p in sorted(folder.rglob("*.yaml"))]


def load_seed(root: Path) -> tuple[list[PinnedRelease], list[EvalSuite]]:
    reg, violations = load_registry(root)
    problems = [*violations, *validate_registry(reg)]
    if problems:
        raise RegistryError(RegistryErrorCode.validation_failed, "el directorio no es un registro válido",
                            payload=[{"rule": v.rule, "path": v.path, "message": v.message}
                                     for v in problems[:50]])
    try:
        pinned = [pin_release(reg, decl.id) for decl in reg.releases()]
        return pinned, _suites(root)
    except SchemaError as exc:
        raise RegistryError(RegistryErrorCode.validation_failed, str(exc)[:500]) from None
