from datetime import timedelta

from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas


def test_default_quotas_are_the_ones_decided_for_the_autonomous_builder() -> None:  # TEMAS #16
    assert DEFAULT_QUOTAS == Quotas(proposals_per_day=10, evals_per_proposal=20, window=timedelta(hours=24))


def test_quotas_from_env_default_and_per_principal_overrides() -> None:
    from agent_core.registry.quotas import quotas_from_env

    assert quotas_from_env({}) == DEFAULT_QUOTAS
    q = quotas_from_env({"AGENTCORE_PROPOSAL_QUOTA_PER_DAY": "7",
                         "AGENTCORE_PROPOSAL_QUOTA_OVERRIDES": "pulso-engine=200, otro=3"})
    assert q.proposals_per_day == 7
    assert q.limit_for("pulso-engine") == 200
    assert q.limit_for("otro") == 3
    assert q.limit_for("constructor-bot") == 7


def test_quotas_from_env_rejects_malformed_values() -> None:
    import pytest

    from agent_core.registry.quotas import quotas_from_env

    for env in ({"AGENTCORE_PROPOSAL_QUOTA_PER_DAY": "0"}, {"AGENTCORE_PROPOSAL_QUOTA_PER_DAY": "x"},
                {"AGENTCORE_PROPOSAL_QUOTA_OVERRIDES": "solo-nombre"},
                {"AGENTCORE_PROPOSAL_QUOTA_OVERRIDES": "a=-1"}):
        with pytest.raises(ValueError):
            quotas_from_env(env)
