"""Configuración de M4 (decisión C15)."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class TurnConfig:
    lease_ttl: timedelta = timedelta(seconds=60)  # > max_wall_ms_per_turn
    sweep_batch: int = 100
    max_understand_calls_per_turn: int = 2  # ADR 0005: 1.ª llamada (JEV) y 2.ª de slots (`llm_structured`)
    max_transfers_per_session: int = 1  # ADR 0021 P5: one way only; counted by `RunOrigin.depth`
