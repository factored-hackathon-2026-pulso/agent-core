"""In-memory evaluator of the metric DSL and of scenario assertions (ADR 0020; `EvalPort`).

Pure and deterministic, over the engine events of one or several runs. Semantics (decision D4 of the
integration plan; the analytics SQL compiler must reproduce them, T-EVAL-14):

- Only `engine.*` events are observed: `registry.*` events do not happen inside a scenario, so a metric or
  an assertion over them is left unmeasured (never zero).
- A missing field is NULL: it satisfies no filter (not even `ne`) and does not enter `sum`, `avg` or
  `percentile`.
- `count` and `sum` over no rows are 0; `avg` and `percentile` over no values, and `rate` with a zero
  denominator, are left unmeasured. `avg` and `rate` are rounded to 4 decimals (`ROUND_HALF_EVEN`); the
  percentile is discrete (`percentile_disc`: the smallest value whose cumulative frequency reaches p).
- The window trims nothing here: the gate measures the whole suite run. A metric with `group_by` is left
  unmeasured (it has no scalar shape, spec 13.10) and so is a `judge` metric (there is no judge in the
  gate, spec 13.4).
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum

from agent_core.domain import EngineEvent, JudgeExpr, MetricExpr, Predicate, catalog_fields
from agent_core.registry.suite import Assertion

OBSERVABLE_PREFIX = "engine."
QUANTUM = Decimal("0.0001")
_ENVELOPE = frozenset({"release", "run_id"})

Value = str | bool | int | Decimal
Record = dict[str, Value]


def _scalar(value: object) -> Value | None:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool | int | str | Decimal):
        return value
    return None


def _lookup(event: EngineEvent, field: str) -> Value | None:
    if field in _ENVELOPE:
        return _scalar(getattr(event, field, None))
    current: object = getattr(event, "payload", None)
    for part in field.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return _scalar(current)


def catalog_record(event: EngineEvent) -> tuple[str, Record] | None:
    """The event as a catalog row: `engine.<type>` and the fields it carries. None if not measurable."""
    kind = getattr(event, "type", None)
    if not isinstance(kind, str):
        return None
    name = OBSERVABLE_PREFIX + kind
    fields = catalog_fields(name)
    if fields is None:
        return None
    record: Record = {}
    for field in fields:
        value = _lookup(event, field)
        if value is not None:
            record[field] = value
    return name, record


def _number(value: Value | None) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    return Decimal(value)


def _equal(left: Value, right: Value) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        return a == b
    return isinstance(left, str) and isinstance(right, str) and left == right


def _holds(record: Record, predicate: Predicate) -> bool:
    value = record.get(predicate.field)
    if value is None:
        return False
    target = predicate.value
    if isinstance(target, list):
        return predicate.op == "in" and any(_equal(value, item) for item in target)
    if predicate.op == "eq":
        return _equal(value, target)
    if predicate.op == "ne":
        return not _equal(value, target)
    a, b = _number(value), _number(target)
    if a is None or b is None:
        return False
    if predicate.op == "lt":
        return a < b
    if predicate.op == "le":
        return a <= b
    if predicate.op == "gt":
        return a > b
    return predicate.op == "ge" and a >= b


def _rows(events: Iterable[EngineEvent], event: str, where: Sequence[Predicate]) -> list[Record]:
    rows: list[Record] = []
    for item in events:
        found = catalog_record(item)
        if found is not None and found[0] == event and all(_holds(found[1], p) for p in where):
            rows.append(found[1])
    return rows


def _observable(event: str) -> bool:
    return event.startswith(OBSERVABLE_PREFIX) and catalog_fields(event) is not None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, ROUND_HALF_EVEN)


def evaluate_metric(expr: MetricExpr | JudgeExpr, events: Sequence[EngineEvent]) -> Decimal | None:
    """Value of the metric over `events`, or None if it cannot be measured (see the module docstring)."""
    if isinstance(expr, JudgeExpr) or expr.group_by or not _observable(expr.event):
        return None
    rows = _rows(events, expr.event, expr.where)
    if expr.aggregation == "count":
        return Decimal(len(rows))
    if expr.aggregation == "rate":
        denominator = expr.denominator
        if denominator is None or not _observable(denominator.event):
            return None
        total = len(_rows(events, denominator.event, denominator.where))
        return None if total == 0 else _quantize(Decimal(len(rows)) / Decimal(total))
    values = sorted(v for v in (_number(row.get(expr.field or "")) for row in rows) if v is not None)
    if expr.aggregation == "sum":
        return sum(values, Decimal(0))
    if not values:
        return None
    if expr.aggregation == "avg":
        return _quantize(sum(values, Decimal(0)) / Decimal(len(values)))
    if expr.percentile is None:
        return None
    rank = (expr.percentile * len(values) + 99) // 100
    return values[max(rank, 1) - 1]


def assertion_holds(assertion: Assertion, events: Sequence[EngineEvent]) -> bool:
    """`at_least_one`: some event satisfies the filter; `none`: no event does. An assertion over an event the
    evaluation does not observe never holds (fails closed)."""
    if not _observable(assertion.event):
        return False
    found = bool(_rows(events, assertion.event, assertion.where))
    return found if assertion.expect == "at_least_one" else not found
