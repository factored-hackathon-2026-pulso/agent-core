from datetime import timedelta


def test_interfaz_publica_de_m4() -> None:
    import agent_core.turn as turn

    for name in (
        "TurnEngine",
        "TurnConfig",
        "SweepReport",
        "Sweeper",
        "UnderstandPort",
        "UnderstandRequest",
        "UnderstandOutcome",
        "TurnRecorderPort",
        "EventChain",
        "GuardsPort",
        "RuntimeFactory",
        "TurnRuntime",
        "TraceIds",
    ):
        assert hasattr(turn, name), name
    assert set(turn.__all__) >= {"TurnEngine", "Sweeper"}


def test_turn_config_por_defecto() -> None:
    from agent_core.turn import TurnConfig

    cfg = TurnConfig()
    assert cfg.lease_ttl == timedelta(seconds=60)
    assert cfg.sweep_batch == 100
