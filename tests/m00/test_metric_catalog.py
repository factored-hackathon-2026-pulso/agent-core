import types
import typing

import pytest
from pydantic import BaseModel

import agent_core.domain.events as events
from agent_core.domain import (
    METRIC_EVENT_CATALOG,
    PLATFORM_METRIC_PREFIX,
    EngineEvent,
    Predicate,
    catalog_fields,
    predicate_problems,
)

# Field names that can never be measurable: they are customer data or free text (CLAUDE.md rule 6).
FORBIDDEN_FIELDS = {"args", "text", "email", "name", "phone", "document_id", "account_number", "message"}


def _engine_event_classes() -> dict[str, type[EngineEvent]]:
    found: dict[str, type[EngineEvent]] = {}
    for obj in vars(events).values():
        if isinstance(obj, type) and issubclass(obj, EngineEvent) and obj is not EngineEvent:
            found[obj.model_fields["type"].default] = obj
    return found


def _unwrap(annotation: object) -> type[BaseModel] | None:
    """The model behind `X` or `X | None`."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def _has_field(event_cls: type[EngineEvent], dotted: str) -> bool:
    head, *rest = dotted.split(".")
    if head in EngineEvent.model_fields and head != "payload" and not rest:
        return True
    model = _unwrap(event_cls.model_fields["payload"].annotation)
    for part in [head, *rest]:
        if model is None or part not in model.model_fields:
            return False
        last = model.model_fields[part].annotation
        model = _unwrap(last)
    return True


def test_catalog_is_not_empty_and_namespaced() -> None:
    assert METRIC_EVENT_CATALOG
    assert all(name.startswith(("engine.", "registry.")) for name in METRIC_EVENT_CATALOG)


def test_every_engine_field_exists_in_the_real_event() -> None:
    classes = _engine_event_classes()
    for name, fields in METRIC_EVENT_CATALOG.items():
        if not name.startswith("engine."):
            continue
        event_cls = classes.get(name.removeprefix("engine."))
        assert event_cls is not None, f"{name} no es un evento de M0"
        for dotted in fields:
            assert _has_field(event_cls, dotted), f"{name}: el campo {dotted} no existe en el evento"


def test_no_catalog_field_exposes_customer_data() -> None:
    for name, fields in METRIC_EVENT_CATALOG.items():
        leaves = {dotted.rsplit(".", 1)[-1] for dotted in fields}
        assert not leaves & FORBIDDEN_FIELDS, name


def test_every_event_exposes_run_grouping_fields() -> None:
    for name, fields in METRIC_EVENT_CATALOG.items():
        if name.startswith("engine."):
            assert fields["release"] == "str" and fields["run_id"] == "str", name
        else:
            assert fields["origin"] == "str" and fields["agent_id"] == "str", name


def test_catalog_fields_lookup() -> None:
    assert catalog_fields("engine.turn_completed") is not None
    assert catalog_fields("engine.nope") is None
    assert catalog_fields("") is None
    assert catalog_fields("x" * 10_000) is None


def test_catalog_is_read_only() -> None:
    with pytest.raises(TypeError):
        METRIC_EVENT_CATALOG["engine.nope"] = {}  # type: ignore[index]


def test_platform_prefix_is_reserved_for_the_platform() -> None:
    assert PLATFORM_METRIC_PREFIX == "platform_"


def _pred(field: str, op: str, value: object) -> Predicate:
    return Predicate.model_validate({"field": field, "op": op, "value": value})


def test_predicate_problems_valid_and_unknown_event() -> None:
    assert predicate_problems("engine.agent_step", [_pred("kind", "eq", "x"), _pred("step", "ge", 2)]) == []
    # an event outside the catalog is reported by the caller
    assert predicate_problems("engine.nope", [_pred("kind", "eq", "x")]) == []


def test_predicate_problems_report_index_subpath_and_message() -> None:
    preds = [
        _pred("kind", "eq", "x"),
        _pred("nope", "eq", "x"),
        _pred("kind", "gt", 1),
        _pred("step", "eq", "texto"),
    ]
    found = predicate_problems("engine.agent_step", preds)
    assert [(i, sub) for i, sub, _ in found] == [(1, "field"), (2, "field"), (3, "value")]
    assert all(msg for _, _, msg in found)
