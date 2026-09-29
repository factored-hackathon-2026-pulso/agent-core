from typing import Protocol

from agent_core.domain.base import UtcDatetime
from agent_core.domain.identity import OnBehalfOf, Principal


class IdentityVerifier(Protocol):
    """Falla cerrado: ante cualquier duda lanza `CredentialsInvalid`; nunca devuelve un `Principal`
    anónimo ni parcial en su lugar. Las credenciales crudas no se registran ni aparecen en errores."""

    def verify(self, raw_credential: str) -> Principal:
        """Solo valida la firma (`CredentialsInvalid`); NO chequea `exp`: eso lo hace M9 con el Clock."""
        ...

    def verify_delegation(self, raw: str) -> OnBehalfOf:
        """Ídem `verify`: firma inválida → `CredentialsInvalid`; no chequea `exp`."""
        ...

    def grant_active(self, grant_ref: str, now: UtcDatetime) -> bool:
        """`False` si el permiso no existe, está revocado o no se puede consultar (falla cerrado)."""
        ...
