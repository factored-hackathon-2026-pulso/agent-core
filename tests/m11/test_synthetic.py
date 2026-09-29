"""T-M11-05: un fixture con un valor no sintético se rechaza."""

from pathlib import Path

import pytest

from agent_core.audit.replay.fixture import Fixture, FullToolResult
from agent_core.audit.replay.synthetic import FixtureRejected, check_fixture, load_catalog
from tests.m11.test_fixture_io import sample

CATALOG = load_catalog(Path("tests/fixtures/catalogo-datos-prueba.yaml"))


def with_full(**result: object) -> Fixture:
    full = FullToolResult(status="ok", result_full=dict(result), source="cliente")  # type: ignore[arg-type]
    return sample().model_copy(update={"full": {"call-0001": full}})


def test_synthetic_fixture_is_accepted() -> None:
    fx = with_full(document_number="1023456789", first_name="Ana Prueba", nota="ana@example.test")
    check_fixture(fx, CATALOG)
    check_fixture(sample(), CATALOG)


@pytest.mark.parametrize("result", [
    {"email": "cliente@gmail.com"},             # dominio real
    {"telefono_libre": "3157654321"},           # número de 6+ dígitos fuera del catálogo
    {"first_name": "Carlos Real"},              # campo pii_direct fuera de `values`
])
def test_t_m11_05_non_synthetic_value_is_rejected(result: dict[str, object]) -> None:
    with pytest.raises(FixtureRejected) as info:
        check_fixture(with_full(**result), CATALOG)
    text = str(info.value) + repr(info.value.violations)
    for leaked in ("gmail", "3157654321", "Carlos"):
        assert leaked not in text  # reporta rutas, nunca valores


def test_non_synthetic_text_in_inputs_and_drafts_is_rejected() -> None:
    fx = sample().model_copy(update={"inputs": [{"text_model": "escríbeme a real@banco.com"}]})
    with pytest.raises(FixtureRejected):
        check_fixture(fx, CATALOG)
    fx = sample().model_copy(update={"drafts": ["mi cédula es 80123456"]})
    with pytest.raises(FixtureRejected):
        check_fixture(fx, CATALOG)
