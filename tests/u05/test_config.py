"""`load_endpoints` y `default_client` (spec del gateway §2, T-U5-06)."""

import pytest

from agent_core.adapters.llm.config import EndpointConfig, default_client, load_endpoints
from agent_core.domain import SchemaError

OK = (
    '{"openrouter": {"base_url": "https://openrouter.test/api/v1", '
    '"api_key_env": "OPENROUTER_API_KEY"}}'
)


def test_load_endpoints_reads_the_alias_map() -> None:
    endpoints = load_endpoints({"LLM_ENDPOINTS": OK})
    assert endpoints == {"openrouter": EndpointConfig(
        "openrouter", "https://openrouter.test/api/v1", "OPENROUTER_API_KEY")}


def test_missing_or_empty_variable_means_no_endpoints() -> None:
    assert load_endpoints({}) == {} and load_endpoints({"LLM_ENDPOINTS": ""}) == {}


@pytest.mark.parametrize("raw", [
    "no es json", "[]", '{"a": "x"}', '{"a": {"base_url": "u"}}', '{"a": {"api_key_env": "K"}}',
    '{"a": {"base_url": "", "api_key_env": "K"}}',
])
def test_malformed_configuration_is_a_schema_error_without_echoing_the_input(raw: str) -> None:
    with pytest.raises(SchemaError) as caught:
        load_endpoints({"LLM_ENDPOINTS": raw})
    assert raw not in str(caught.value) or raw == ""


def test_default_client_never_retries_and_uses_the_endpoint() -> None:
    client = default_client(EndpointConfig("o", "https://openrouter.test/api/v1", "K"), "clave", 8)
    assert client.max_retries == 0
    assert str(client.base_url).startswith("https://openrouter.test/api/v1")
