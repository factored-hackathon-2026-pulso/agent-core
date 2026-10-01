"""Topes del constructor autónomo (TEMAS #16, spec write-draft §4.5).

Valen solo para propuestas con `origin = auto_detect`. El tope de costo por propuesta está diferido: no existe
aquí hasta que se fije su monto."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Quotas:
    proposals_per_day: int = 10  # ventana móvil medida con el Clock, no un día calendario
    evals_per_proposal: int = 20
    window: timedelta = timedelta(hours=24)


DEFAULT_QUOTAS = Quotas()
