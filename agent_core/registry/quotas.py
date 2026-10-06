"""Topes del constructor autónomo (TEMAS #16, spec write-draft §4.5).

Valen solo para propuestas con `origin = auto_detect`. El tope de costo por propuesta está diferido: no existe
aquí hasta que se fije su monto. El tope diario se puede ajustar por entorno y por principal (p. ej. el motor
de mejora crea varias propuestas por hallazgo); sin configuración rige el defecto."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

PER_DAY_ENV = "AGENTCORE_PROPOSAL_QUOTA_PER_DAY"
OVERRIDES_ENV = "AGENTCORE_PROPOSAL_QUOTA_OVERRIDES"


@dataclass(frozen=True)
class Quotas:
    proposals_per_day: int = 10  # ventana móvil medida con el Clock, no un día calendario
    evals_per_proposal: int = 20
    window: timedelta = timedelta(hours=24)
    # Tope diario propio de un principal (id -> tope); ese principal cuenta solo sus propias propuestas.
    overrides: Mapping[str, int] = field(default_factory=dict)

    def limit_for(self, principal_id: str) -> int:
        return self.overrides.get(principal_id, self.proposals_per_day)


DEFAULT_QUOTAS = Quotas()


def _positive(name: str, raw: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name}: se esperaba un entero positivo") from None
    if value < 1:
        raise ValueError(f"{name}: se esperaba un entero positivo")
    return value


def quotas_from_env(env: Mapping[str, str]) -> Quotas:
    """`AGENTCORE_PROPOSAL_QUOTA_PER_DAY` (defecto 10) y `AGENTCORE_PROPOSAL_QUOTA_OVERRIDES`
    (`principal=tope,principal=tope`). Un valor mal formado falla al arrancar, sin imprimirlo."""
    per_day = env.get(PER_DAY_ENV, "").strip()
    overrides: dict[str, int] = {}
    for item in filter(None, (s.strip() for s in env.get(OVERRIDES_ENV, "").split(","))):
        name, sep, raw = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"{OVERRIDES_ENV}: se esperaba `principal=tope`")
        overrides[name.strip()] = _positive(OVERRIDES_ENV, raw)
    daily = _positive(PER_DAY_ENV, per_day) if per_day else DEFAULT_QUOTAS.proposals_per_day
    return Quotas(proposals_per_day=daily, overrides=overrides)
