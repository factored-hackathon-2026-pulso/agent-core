"""Public interface of unit 5."""

import agent_core.adapters.llm as llm


def test_the_public_names_are_exported() -> None:
    assert set(llm.__all__) == {"HttpLLMGateway", "LLMAgentPort", "UnconfiguredLLMGateway", "gateway_is_up"}
    for name in llm.__all__:
        assert hasattr(llm, name)


def test_the_in_process_openai_gateway_is_gone() -> None:
    for name in ("OpenAICompatGateway", "load_endpoints", "EndpointConfig", "price_of"):
        assert not hasattr(llm, name)
