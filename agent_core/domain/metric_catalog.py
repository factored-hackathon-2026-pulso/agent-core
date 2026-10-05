"""Closed catalog of measurable events (ADR 0020, evaluation spec section 4).

It only exposes fields the event already carries in the `audit` view; no customer data field. Adding a
measurable event is a catalog change and bumps `SCHEMA_VERSION`.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Literal

from agent_core.domain.metrics import Predicate

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
    "engine.suggestions_produced": _engine(
        {"llm.calls": "int", "llm.tokens_in": "int", "llm.tokens_out": "int", "llm.cost_usd": "decimal"},
        result="str", count="int", reply="int", tool="int", action="int", escalate="int", regenerations="int",
    ),
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
    """Measurable fields of `event`, or None if it is not in the catalog. Never raises."""
    return METRIC_EVENT_CATALOG.get(event)


_ORDER_OPS = frozenset({"lt", "le", "gt", "ge"})
_NUMERIC = frozenset({"int", "decimal"})


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _scalar_matches(kind: str, value: object) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "bool":
        return isinstance(value, bool)
    return isinstance(value, int | Decimal) and not isinstance(value, bool)


def predicate_problems(event: str, predicates: Sequence[Predicate]) -> list[tuple[int, str, str]]:
    """What is wrong with `predicates` against the fields of `event`: `(index, "field" | "value", message)`.

    The field must exist, the value must match its type and ordering operators require a numeric
    field. An event outside the catalog yields an empty list: the caller reports it. Never raises.
    """
    fields = catalog_fields(event)
    if fields is None:
        return []
    found: list[tuple[int, str, str]] = []
    for i, pred in enumerate(predicates):
        kind = fields.get(pred.field)
        name = _short(pred.field, 40)
        if kind is None:
            text = f"campo {_short(pred.field, 60)} no existe en {_short(event, 60)}"
            found.append((i, "field", text))
            continue
        if pred.op in _ORDER_OPS and kind not in _NUMERIC:
            found.append((i, "field", f"el operador {pred.op} exige un campo numérico y {name} es {kind}"))
            continue
        values = pred.value if isinstance(pred.value, list) else [pred.value]
        if not all(_scalar_matches(kind, v) for v in values):
            found.append((i, "value", f"el valor no corresponde al tipo {kind} de {name}"))
    return found
