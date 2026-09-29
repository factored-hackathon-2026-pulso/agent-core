"""Bases y tipos anotados comunes de M0 (convenciones transversales de §2)."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


class Model(BaseModel):
    """Tipo de valor de M0: inmutable y sin campos extra."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class MutableModel(BaseModel):
    """Solo para `RunState`: se actualiza con `model_copy(update=...)` (índice §5)."""

    model_config = ConfigDict(extra="forbid", frozen=False, populate_by_name=True)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Patrones solo ASCII (`[0-9]`, no `\d`, que en Unicode acepta otros dígitos). El motor de regex de pydantic
# (Rust) no hace backtracking, y ahí `$` no admite un salto de línea final.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Locale = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]
EntityId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_/-]*$")]
# semver 2.0 sin prerelease ni build: sin ceros a la izquierda.
_NUM = r"(?:0|[1-9][0-9]*)"
ExactVersion = Annotated[str, StringConstraints(pattern=rf"^{_NUM}\.{_NUM}\.{_NUM}$")]
NodeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_]*$")]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
