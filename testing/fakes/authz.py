from collections.abc import Collection
from typing import TYPE_CHECKING

from agent_core.domain import (
    Agent,
    AuthLevel,
    KnowledgeView,
    OnBehalfOf,
    Principal,
    PrincipalType,
    Purpose,
    SubjectRef,
)
from agent_core.ports import AuthzDecision

_ALLOW = AuthzDecision(allowed=True)
# Vistas de conocimiento (M12): solo lo público y aprobado puede citarse al cliente (ADR 0015).
_CITABLE = KnowledgeView(audiences=frozenset({"public"}), approved_only=True)
_ADVISOR = KnowledgeView(audiences=frozenset({"public", "internal"}), approved_only=False)
_GUIDANCE = KnowledgeView(audiences=frozenset({"public", "internal", "agent_only"}), approved_only=False)
_NOTHING = KnowledgeView(audiences=frozenset(), approved_only=False)


def _deny(reason: str) -> AuthzDecision:
    return AuthzDecision(allowed=False, reason=reason)


def is_platform_admin(principal: Principal) -> bool:
    """El administrador de la plataforma: un `builder` persona, con el rol `admin` y autenticación reforzada.
    Es el único `builder` que llega a datos de clientes (ADR 0006, enmienda 2026-09-30, tema #14)."""
    return (principal.type is PrincipalType.builder and "admin" in principal.roles
            and principal.attrs.get("actor") == "human" and principal.auth.level is AuthLevel.step_up)


class TableAuthz:
    """`AuthzPort` de prueba con la tabla de ADR 0006 y spec general §4.2 (la real es de la unidad 3).

    Convenciones propias del doble, no del spec: los scopes de `service` y `builder` se expresan como
    `subject:<kind>` o `subject:*`; las claves de `bind_params` son `subject_ref` y `builder_id`; los campos
    se conceden por pares `(campo, purpose)` y nadie lee nada por defecto."""

    def __init__(
        self,
        field_grants: Collection[tuple[str, str]] = (),
        reportable: frozenset[str] = frozenset({"country", "segment"}),
    ) -> None:
        self._grants = frozenset(field_grants)
        self._reportable = reportable

    def authorize_agent(
        self, principal: Principal, agent: Agent, subject: SubjectRef | None
    ) -> AuthzDecision:
        if principal.type not in agent.invocable_by:
            return _deny("principal_type")
        if subject is None:
            if agent.subject_kinds:
                return _deny("subject_required")
        elif subject.kind not in agent.subject_kinds:
            return _deny("subject_kind")
        if principal.auth.level < agent.min_auth_level:
            return _deny("auth_level")
        return _ALLOW

    def authorize_subject(
        self, principal: Principal, obo: OnBehalfOf | None, subject: SubjectRef | None
    ) -> AuthzDecision:
        if subject is None:
            return _ALLOW
        match principal.type:
            case PrincipalType.customer:
                if principal.auth.level is AuthLevel.anonymous or principal.id is None:
                    return _deny("anonymous_subject")
                own = SubjectRef(kind="customer", ref=principal.id)
                return _ALLOW if subject == own else _deny("not_own_subject")
            case PrincipalType.advisor:
                if obo is None:
                    return _deny("sin_delegacion")
                if obo.grantee != principal.key or obo.subject != subject:
                    return _deny("delegacion_ajena")
                return _ALLOW
            case PrincipalType.service | PrincipalType.builder:
                if (principal.type is PrincipalType.builder and subject.kind == "customer"
                        and not is_platform_admin(principal)):
                    return _deny("builder_sin_datos_de_clientes")
                scopes = set(principal.scopes)
                if {f"subject:{subject.kind}", "subject:*"} & scopes:
                    return _ALLOW
                return _deny("scope")
        return _deny("principal_type")  # pragma: no cover

    def bind_params(
        self, principal: Principal, obo: OnBehalfOf | None, subject: SubjectRef | None
    ) -> dict[str, str]:
        match principal.type:
            case PrincipalType.customer:
                return {} if principal.id is None else {"subject_ref": principal.id}
            case PrincipalType.advisor:
                return {} if obo is None else {"subject_ref": obo.subject.ref}
            case PrincipalType.service:
                return {} if subject is None else {"subject_ref": subject.ref}
            case PrincipalType.builder:
                if principal.id is None:
                    return {}
                if is_platform_admin(principal) and subject is not None:
                    return {"builder_id": principal.id, "subject_ref": subject.ref}
                return {"builder_id": principal.id}
        return {}  # pragma: no cover

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        if reader.type is PrincipalType.builder and not is_platform_admin(reader):
            return False  # ADR 0006, ADR 0019: solo el administrador lee datos de clientes, y con concesión
        return reader.id is not None and (field, purpose) in self._grants

    def knowledge_view(self, principal: Principal, purpose: Purpose) -> KnowledgeView:
        match purpose:
            case "customer_answer":
                # sea quien sea el que pregunta: lo que se cita al cliente es público y aprobado
                return _CITABLE
            case "advisor_view":
                return _NOTHING if principal.type is PrincipalType.customer else _ADVISOR
            case "agent_guidance":
                return _GUIDANCE
        return _NOTHING  # pragma: no cover

    def reportable_attrs(self) -> frozenset[str]:
        return self._reportable


if TYPE_CHECKING:
    from agent_core.ports import AuthzPort

    def _conforms(x: TableAuthz) -> AuthzPort:
        return x
