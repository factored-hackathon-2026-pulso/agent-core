"""Roles del registry y barrera de actor humano (spec §8). Se verifica en el servidor.

Solo un `builder` puede operar el registry. El supervisor es una persona con `constructor` y `aprobador`; el
administrador suma `admin`; el agente constructor autentica con su propia credencial de servicio, que solo
puede traer `constructor` y nunca `actor = "human"`. Aprobar, publicar, promover, revocar e importar exigen
además un principal humano con `auth.level = step_up` (ADR 0010)."""

from agent_core.domain import AuthLevel, Principal, PrincipalType
from agent_core.registry.errors import RegistryError, RegistryErrorCode

CONSTRUCTOR = "constructor"
APROBADOR = "aprobador"
ADMIN = "admin"
ROLES = frozenset({CONSTRUCTOR, APROBADOR, ADMIN})  # lista cerrada: un rol desconocido no concede nada
HUMAN_ATTR = "actor"


def is_human(p: Principal) -> bool:
    return p.id is not None and p.attrs.get(HUMAN_ATTR) == "human"


def actor_id(p: Principal) -> str:
    if p.id is None:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita un principal identificado")
    return p.id


def require_builder(p: Principal) -> None:
    """Solo un `builder` identificado opera el registry (nunca un cliente, un asesor ni un servicio)."""
    actor_id(p)
    if p.type is not PrincipalType.builder:
        raise RegistryError(RegistryErrorCode.forbidden_role, "el registry solo lo opera un builder")


def require_constructor(p: Principal) -> None:
    require_builder(p)
    if CONSTRUCTOR not in p.roles:
        raise RegistryError(RegistryErrorCode.forbidden_role, "se necesita el rol constructor")


def _require_human_step_up(p: Principal, role: str, message: str) -> None:
    require_builder(p)
    if role not in p.roles or not is_human(p):
        raise RegistryError(RegistryErrorCode.forbidden_role, message)
    if p.auth.level < AuthLevel.step_up:
        raise RegistryError(RegistryErrorCode.step_up_required, "la operación exige autenticación reforzada")


def require_approver(p: Principal) -> None:
    _require_human_step_up(p, APROBADOR, "solo una persona con rol aprobador puede hacer esta operación")


def require_admin(p: Principal) -> None:
    _require_human_step_up(p, ADMIN, "solo una persona con rol admin puede hacer esta operación")
