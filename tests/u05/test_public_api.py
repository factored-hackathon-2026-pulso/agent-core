"""Interfaz pública de la unidad 5 (spec §2)."""

import agent_core.adapters.llm as llm


def test_the_public_names_are_exported() -> None:
    assert set(llm.__all__) >= {"EndpointConfig", "OpenAICompatGateway", "default_client", "load_endpoints",
                                "price_of"}
    for name in llm.__all__:
        assert hasattr(llm, name)
