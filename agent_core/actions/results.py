"""Resultados de la interfaz pública de M3 (M3 §2)."""

from typing import Literal

Answer = Literal["yes", "no", "unclear"]
AnswerResult = Literal["yes", "no", "unclear", "max_attempts"]
WriteResult = Literal["ok", "denied", "uncertain", "step_up_required"]
VerifyResult = Literal["verified", "failed", "unavailable"]
