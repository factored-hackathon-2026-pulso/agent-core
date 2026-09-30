from collections.abc import Collection
from typing import TYPE_CHECKING

from agent_core.domain import Agent, AuthLevel, OnBehalfOf, Principal, PrincipalType, SubjectRef
from agent_core.ports import AuthzDecision

_ALLOW = AuthzDecision(allowed=True)


def _deny(reason: str) -> AuthzDecision:
    return AuthzDecision(allowed=False, reason=reason)


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
                if principal.type is PrincipalType.builder and subject.kind == "customer":
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
                return {} if principal.id is None else {"builder_id": principal.id}
        return {}  # pragma: no cover

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        if reader.type is PrincipalType.builder:
            return False  # ADR 0006, ADR 0019: el builder nunca lee datos de clientes, ni con concesión
        return reader.id is not None and (field, purpose) in self._grants

    def reportable_attrs(self) -> frozenset[str]:
        return self._reportable


if TYPE_CHECKING:
    from agent_core.ports import AuthzPort

    def _conforms(x: TableAuthz) -> AuthzPort:
        return x
