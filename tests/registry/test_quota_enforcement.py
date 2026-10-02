from datetime import timedelta

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.quotas import Quotas
from agent_core.registry.service import RegistryService
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _service(w: World, quotas: Quotas) -> RegistryService:
    return RegistryService(w.store, w.evaluator, w.clock, w.ids, quotas=quotas)


def _code(info: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return info.value.code


def test_the_autonomous_builder_cannot_create_more_than_its_daily_proposals() -> None:  # Review Focus 4
    w = World()
    service = _service(w, Quotas(proposals_per_day=2))
    for _ in range(2):
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "señal")
    with pytest.raises(RegistryError) as info:
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "señal")
    assert _code(info) is RegistryErrorCode.quota_exceeded


def test_the_window_is_a_rolling_24_hours() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "a")
    w.clock.advance(timedelta(hours=23, minutes=59))
    with pytest.raises(RegistryError):
        service.create_proposal(bot(), AGENT, Origin.auto_detect, "b")
    w.clock.advance(timedelta(minutes=1))  # exactamente 24 h: la primera ya no cuenta
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "c")


def test_other_origins_have_no_quota() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    for origin in (Origin.manual, Origin.builder_chat, Origin.manual):
        service.create_proposal(bot(), AGENT, origin, "t")
    service.create_proposal(bot(), AGENT, Origin.auto_detect, "una")  # las anteriores no cuentan


def test_a_replayed_creation_does_not_count_twice() -> None:
    w = World()
    service = _service(w, Quotas(proposals_per_day=1))
    first = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t", idempotency_key="k")
    again = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t", idempotency_key="k")
    assert again.proposal_id == first.proposal_id


def test_the_autonomous_builder_cannot_exceed_its_evaluations_per_proposal() -> None:
    w = World()
    service = _service(w, Quotas(evals_per_proposal=1))
    p = service.create_proposal(bot(), AGENT, Origin.auto_detect, "t")
    service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")  # la 1.ª
    service.reopen(bot(), p.proposal_id)
    service.freeze(bot(), p.proposal_id)
    calls = len(w.evaluator.calls)
    with pytest.raises(RegistryError) as info:
        service.evaluate(bot(), p.proposal_id, "disputas-suite")  # la 2.ª
    assert _code(info) is RegistryErrorCode.quota_exceeded
    assert len(w.evaluator.calls) == calls  # no se gastó una evaluación


def test_manual_proposals_have_no_evaluation_quota() -> None:
    w = World()
    service = _service(w, Quotas(evals_per_proposal=1))
    p = service.create_proposal(bot(), AGENT, Origin.manual, "t")
    service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")
    service.reopen(bot(), p.proposal_id)
    service.freeze(bot(), p.proposal_id)
    service.evaluate(bot(), p.proposal_id, "disputas-suite")  # no lanza
