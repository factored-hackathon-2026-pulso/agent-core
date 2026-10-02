from datetime import timedelta

from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas


def test_default_quotas_are_the_ones_decided_for_the_autonomous_builder() -> None:  # TEMAS #16
    assert DEFAULT_QUOTAS == Quotas(proposals_per_day=10, evals_per_proposal=20, window=timedelta(hours=24))
