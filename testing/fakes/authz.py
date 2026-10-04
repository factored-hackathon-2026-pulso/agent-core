"""`TableAuthz`: el `AuthzPort` real (`PolicyAuthz`) con la tabla de ADR 0006, para las pruebas. Es la misma
clase que usa producción: las pruebas de contrato ejercitan la implementación real."""

from agent_core.adapters.policy_authz import PolicyAuthz, is_platform_admin

__all__ = ["TableAuthz", "is_platform_admin"]


class TableAuthz(PolicyAuthz):
    pass
