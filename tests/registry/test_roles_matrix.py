"""Matriz de permisos del registry por perfil (tema #14): quién puede qué, en el servicio."""

from collections.abc import Callable
from typing import Any

import pytest

from agent_core.domain import Principal
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.service import RegistryService
from testing.builders import NOW, principal
from tests.registry.helpers import AGENT, REGISTRY_DEMO, admin, bot, human
from tests.registry.service_world import World

Op = Callable[[RegistryService, Principal], Any]

CONSTRUCTOR_OPS: dict[str, Op] = {
    "create_proposal": lambda s, a: s.create_proposal(a, AGENT, Origin.manual, "t"),
    "put_draft": lambda s, a: s.put_draft(a, "p-x", [], 0),
    "validate": lambda s, a: s.validate(a, "p-x"),
    "freeze": lambda s, a: s.freeze(a, "p-x"),
    "reopen": lambda s, a: s.reopen(a, "p-x"),
    "evaluate": lambda s, a: s.evaluate(a, "p-x", "disputas-suite"),
    "lineage": lambda s, a: s.lineage_for_run(a, "run-x"),
}
APPROVER_OPS: dict[str, Op] = {
    "approve": lambda s, a: s.approve(a, "p-x", "h"),
    "reject": lambda s, a: s.reject(a, "p-x", "r"),
    "publish": lambda s, a: s.publish(a, "p-x", "k"),
    "promote_prod": lambda s, a: s.promote(a, AGENT, "prod", "rel-x"),
    "promote_staging": lambda s, a: s.promote(a, AGENT, "staging", "rel-x"),
}
ADMIN_OPS: dict[str, Op] = {
    "revoke": lambda s, a: s.revoke(a, "rel-x", "r"),
    "import_seed": lambda s, a: s.import_seed(a, REGISTRY_DEMO),
}
ALL_OPS = {**CONSTRUCTOR_OPS, **APPROVER_OPS, **ADMIN_OPS}
DENIED = {RegistryErrorCode.forbidden_role, RegistryErrorCode.step_up_required}


def _who(kind: str = "builder", *roles: str, actor: bool = True, level: str = "step_up") -> Principal:
    return principal(type=kind, id="p-1", roles=list(roles), attrs={"actor": "human"} if actor else {},
                     auth={"level": level, "at": NOW})


def _code(service: RegistryService, actor: Principal, op: Op) -> RegistryErrorCode | None:
    """El código de denegación, o `None` si el rol pasó (la operación puede fallar por otra causa)."""
    try:
        op(service, actor)
    except RegistryError as error:
        return error.code if error.code in DENIED else None
    return None


def _denies(actor: Principal, names: dict[str, Op]) -> None:
    service = World().service
    for name, op in names.items():
        assert _code(service, actor, op) is RegistryErrorCode.forbidden_role, name


def _allows(actor: Principal, names: dict[str, Op]) -> None:
    service = World().service
    for name, op in names.items():
        assert _code(service, actor, op) is None, name


@pytest.mark.parametrize("kind", ["customer", "advisor", "service"])
def test_only_a_builder_operates_the_registry_even_with_every_role(kind: str) -> None:
    _denies(_who(kind, "constructor", "aprobador", "admin"), ALL_OPS)


def test_a_builder_without_roles_or_with_unknown_ones_can_do_nothing() -> None:
    for actor in (_who("builder"), _who("builder", "superuser", "root")):
        _denies(actor, ALL_OPS)


def test_the_bot_builds_but_never_decides_even_if_its_credential_claims_the_roles() -> None:
    claimed = bot("constructor", "aprobador", "admin")
    _allows(claimed, CONSTRUCTOR_OPS)
    _denies(claimed, {**APPROVER_OPS, **ADMIN_OPS})


def test_the_supervisor_builds_approves_publishes_and_promotes_to_prod_but_cannot_revoke_or_import() -> None:
    _allows(human(), {**CONSTRUCTOR_OPS, **APPROVER_OPS})
    _denies(human(), ADMIN_OPS)


def test_the_administrator_can_do_everything() -> None:
    _allows(admin(), ALL_OPS)


def test_the_approver_without_the_constructor_role_does_not_build() -> None:
    _denies(human("aprobador"), CONSTRUCTOR_OPS)


def test_decisions_need_step_up() -> None:
    service = World().service
    supervisor, root = human(level="session"), human("constructor", "aprobador", "admin", level="session")
    for name, op in APPROVER_OPS.items():
        assert _code(service, supervisor, op) is RegistryErrorCode.step_up_required, name
    for name, op in ADMIN_OPS.items():
        assert _code(service, root, op) is RegistryErrorCode.step_up_required, name
    _allows(supervisor, CONSTRUCTOR_OPS)  # construir no exige autenticación reforzada


def test_step_up_does_not_replace_the_human_barrier() -> None:
    _denies(_who("builder", "constructor", "aprobador", "admin", actor=False), {**APPROVER_OPS, **ADMIN_OPS})
