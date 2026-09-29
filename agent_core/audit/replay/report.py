"""Salida del replay (M11 §2, decisión 12)."""

from typing import Literal

from agent_core.domain import JsonValue
from agent_core.domain.base import Model

ReplayMode = Literal["fixture", "audit"]


class Divergence(Model):
    event_seq: int | None  # seq grabado del evento que difiere; el siguiente seq si el replay produjo de más
    expected: JsonValue  # lo grabado (normalizado); None si faltaba
    actual: JsonValue  # lo recalculado (normalizado); None si faltó


class ReplayReport(Model):
    mode: ReplayMode
    run_id: str
    release: str
    verdict: Literal["match", "diverged", "chain_broken"]
    first_divergence: Divergence | None = None
    chain_broken_at: int | None = None
    duration_ms: int | None = None
