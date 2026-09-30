"""Roles del registry y barrera de actor humano (spec §8). Se verifica en el servidor."""

from agent_core.domain import Principal
from agent_core.registry.errors import RegistryError, RegistryErrorCode

CONSTRUCTOR = "constructor"
APROBADOR = "aprobador"
HUMAN_ATTR = "actor"


def is_human(p: Principal) -> bool:
    return p.id is not None and p.attrs.get(HUMAN_ATTR) == "human"


def actor_id(p: Principal) -> str:
    if p.id is None:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita un principal identificado")
    return p.id


def require_constructor(p: Principal) -> None:
    actor_id(p)
    if CONSTRUCTOR not in p.roles:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita el rol constructor")


def require_approver(p: Principal) -> None:
    actor_id(p)
    if APROBADOR not in p.roles or not is_human(p):
        raise RegistryError(RegistryErrorCode.forbidden_role,
                            "solo una persona con rol aprobador puede hacer esta operación")
