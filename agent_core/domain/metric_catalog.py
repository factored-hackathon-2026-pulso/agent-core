"""Catálogo cerrado de eventos medibles (ADR 0020, spec de evaluación §4).

Solo expone campos que el evento ya lleva en vista `audit`; ningún campo de datos de cliente. Agregar un
evento medible es un cambio de catálogo y sube `SCHEMA_VERSION`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

FieldKind = Literal["str", "int", "decimal", "bool"]

_ENGINE_COMMON: dict[str, FieldKind] = {"release": "str", "run_id": "str"}
_REGISTRY_COMMON: dict[str, FieldKind] = {
    "origin": "str", "actor_role": "str", "agent_id": "str", "proposal_id": "str",
}


def _engine(
    extra: Mapping[str, FieldKind] | None = None, **fields: FieldKind
) -> Mapping[str, FieldKind]:
    return MappingProxyType({**_ENGINE_COMMON, **(extra or {}), **fields})


def _registry(**fields: FieldKind) -> Mapping[str, FieldKind]:
    return MappingProxyType(_REGISTRY_COMMON | fields)


METRIC_EVENT_CATALOG: Mapping[str, Mapping[str, FieldKind]] = MappingProxyType({
    "engine.turn_completed": _engine(entry="str", duration_ms="int", degraded="bool"),
    "engine.escalated": _engine(reason_code="str", target_queue="str", priority="str"),
    "engine.run_closed": _engine(outcome="str", closed_by="str"),
    "engine.tool_called": _engine(status="str", latency_ms="int", attempt="int"),
    "engine.agent_step": _engine(kind="str", status="str", step="int", latency_ms="int"),
    "engine.action_verified": _engine(result="str"),
    "engine.response_emitted": _engine(
        {
            "validator.ok": "bool", "validator.regenerations": "int", "llm.calls": "int",
            "llm.latency_ms": "int", "llm.tokens_in": "int", "llm.tokens_out": "int",
            "llm.cost_usd": "decimal",
        },
        kind="str", fallback_used="bool",
    ),
    "engine.response_failed": _engine({"validator.ok": "bool"}, reason_code="str"),
    "engine.access_denied": _engine(reason="str"),
    "engine.injection_flagged": _engine(scope="str", ruleset="str"),
    "engine.handoff_resolved": _engine(handoff_quality="str", resolution_code="str", reader_type="str"),
    "registry.proposal_created": _registry(),
    "registry.validated": _registry(),
    "registry.frozen": _registry(),
    "registry.evaluated": _registry(verdict="str"),
    "registry.approved": _registry(),
    "registry.rejected": _registry(),
    "registry.published": _registry(),
    "registry.promoted": _registry(alias="str"),
    "registry.revoked": _registry(),
})


def catalog_fields(event: str) -> Mapping[str, FieldKind] | None:
    """Campos medibles de `event`, o None si no está en el catálogo. Nunca lanza."""
    return METRIC_EVENT_CATALOG.get(event)
