"""Fuente de datos de la unidad 6 (M11 §8) y métricas propias de integridad."""

from collections.abc import Iterator
from decimal import Decimal

from agent_core.audit.chain import check_chain
from agent_core.domain import JsonValue, to_jsonable
from agent_core.domain.base import Model
from agent_core.ports import AuditSink


def export_events(
    sink: AuditSink, run_ids: list[str], release: str | None = None
) -> Iterator[dict[str, JsonValue]]:
    """Un dict por evento (vista `audit`), con los campos de medición. Filtra por `release` si se indica."""
    for run_id in run_ids:
        for event in sink.read(run_id):
            if release is None or event.release == release:
                row: dict[str, JsonValue] = to_jsonable(event)
                yield row


class ChainIntegrity(Model):
    runs: int
    intact: int
    broken: list[str]
    ratio: Decimal  # objetivo 1 (100 %)


def chain_integrity(sink: AuditSink, run_ids: list[str]) -> ChainIntegrity:
    broken = [r for r in run_ids if not check_chain(r, sink.read(r)).ok]
    total = len(run_ids)
    ratio = Decimal(1) if total == 0 else Decimal(total - len(broken)) / Decimal(total)
    return ChainIntegrity(runs=total, intact=total - len(broken), broken=broken, ratio=ratio)
