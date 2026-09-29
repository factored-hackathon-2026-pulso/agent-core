"""Identidad y acceso (M0 §2.3, ADR 0006, ADR 0010)."""

from enum import StrEnum

from pydantic import Field, model_validator

from agent_core.domain.base import Model, UtcDatetime


class PrincipalType(StrEnum):
    """Tipo de principal que invoca el motor (ADR 0006, M0 §2.3)."""
    customer = "customer"
    advisor = "advisor"
    service = "service"
    builder = "builder"


# Si devolviera NotImplemented, Python caería al `str.__lt__` reflejado y compararía alfabéticamente
# (`AuthLevel.session >= "anonymous"` daría un resultado sin sentido): se falla cerrado.
_MIXED = "AuthLevel solo se compara con otro AuthLevel"
_AUTH_RANK = {"anonymous": 0, "session": 1, "step_up": 2}


class AuthLevel(StrEnum):
    """Nivel de autenticación; se compara por rango (`anonymous < session < step_up`)."""

    anonymous = "anonymous"
    session = "session"
    step_up = "step_up"

    @property
    def rank(self) -> int:
        return _AUTH_RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            raise TypeError(_MIXED)
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            raise TypeError(_MIXED)
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            raise TypeError(_MIXED)
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, AuthLevel):
            raise TypeError(_MIXED)
        return self.rank >= other.rank


class AuthInfo(Model):
    """Nivel de autenticación de un principal y el instante en que lo obtuvo (ADR 0010)."""
    level: AuthLevel
    at: UtcDatetime
    simulated: bool = False  # OTP de prueba etiquetado (ADR 0010)


class PrincipalKey(Model):
    """Identidad comparable de un principal (tipo e id), sin credenciales (M0 §2.3)."""
    type: PrincipalType
    id: str | None = Field(min_length=1)


class SubjectRef(Model):
    """Referencia al sujeto de un run (tipo y referencia opaca) (M0 §2.3)."""
    kind: str = Field(min_length=1)
    ref: str = Field(min_length=1)


class Principal(Model):
    """Quien invoca, ya verificado por M9. Snapshot sin credencial ni secretos."""

    type: PrincipalType
    id: str | None = Field(default=None, min_length=1)
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    attrs: dict[str, str] = Field(default_factory=dict)
    auth: AuthInfo
    exp: UtcDatetime

    @model_validator(mode="after")
    def _anonymous_rules(self) -> "Principal":
        anonymous = self.auth.level is AuthLevel.anonymous
        if anonymous != (self.id is None):
            raise ValueError("un principal es anónimo si y solo si no tiene id")
        if anonymous and self.type is not PrincipalType.customer:
            raise ValueError("solo un customer puede ser anónimo")
        return self

    @property
    def key(self) -> PrincipalKey:
        return PrincipalKey(type=self.type, id=self.id)


class OnBehalfOf(Model):
    """Delegación firmada por el emisor de asignaciones, atada al asesor `grantee` (ADR 0006)."""

    subject: SubjectRef
    grant_ref: str = Field(min_length=1)
    grantee: PrincipalKey
    scopes: list[str] = Field(default_factory=list)
    exp: UtcDatetime

    @model_validator(mode="after")
    def _grantee_is_advisor(self) -> "OnBehalfOf":
        if self.grantee.type is not PrincipalType.advisor or not self.grantee.id:
            raise ValueError("grantee debe ser un advisor con id")
        return self
