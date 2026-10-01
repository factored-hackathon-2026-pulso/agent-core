"""Whether a principal may be transferred to an agent (ADR 0021 D8, spec §4). Pure: no ports."""

from agent_core.domain.base import Locale
from agent_core.domain.entities import Agent
from agent_core.domain.identity import Principal, SubjectRef


def transfer_ineligibility(agent: Agent, principal: Principal, subject: SubjectRef | None,
                           locale: Locale) -> str | None:
    """`None` if eligible; otherwise the first failing reason, with no principal data."""
    if agent.mode != "conversational":
        return "mode"
    if agent.accepts is None:
        return "no_contract"
    if principal.type not in agent.invocable_by:
        return "principal_type"
    if subject is not None and subject.kind not in agent.subject_kinds:
        return "subject_kind"
    if locale not in agent.supported_locales:
        return "locale"
    if principal.auth.level < agent.min_auth_level:
        return "auth_level"
    return None
