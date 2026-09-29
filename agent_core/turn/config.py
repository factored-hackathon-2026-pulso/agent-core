"""Configuración de M4 (decisión C15)."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class TurnConfig:
    lease_ttl: timedelta = timedelta(seconds=60)  # > max_wall_ms_per_turn
    sweep_batch: int = 100
