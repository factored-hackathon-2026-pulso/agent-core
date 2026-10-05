"""Eco de un valor tokenizado (M7 §3.7, ADR 0026): lo que el vault ocultó al modelo no vuelve en claro."""

import pytest

from agent_core.views import TokenVault
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m07.helpers import make_service

CUSTOMER_TEXT = (
    "Mi tarjeta es 4111 1111 1111 1111, mi correo ana.prueba@example.test, mi cédula 1012345678 "
    "y mi celular 3001234567."
)


def vault_with_the_customer_text() -> tuple[object, TokenVault]:
    service = make_service()
    vault = TokenVault("run-0001", FakeKeyProvider.default(), FakeIds())
    tokenized = service.tokenize_text(CUSTOMER_TEXT, vault)
    assert "4111" not in tokenized and len(vault) >= 3  # the detector hid them
    return service, vault


def test_the_vault_lists_its_entries_without_exposing_them_in_repr() -> None:
    _, vault = vault_with_the_customer_text()
    entries = vault.entries()
    assert len(entries) == len(vault) and {e.token for e in entries} == {e.token for e in entries if e.token}
    assert "ana.prueba" not in repr(vault) and "ana.prueba" not in repr(entries)


@pytest.mark.parametrize(
    "echo",
    [
        "Recibí tu tarjeta 4111 1111 1111 1111.",
        "Recibí tu tarjeta 4111-1111-1111-1111.",
        "Tarjeta 4111111111111111 bloqueada.",
        "Te escribo a ANA.PRUEBA@example.test",
        "Tu cédula es 1.012.345.678",
        "Te llamo al 300 123 4567",
    ],
)
def test_an_echo_of_a_tokenized_value_is_found_by_token_never_by_value(echo: str) -> None:
    service, vault = vault_with_the_customer_text()
    found = service.find_tokenized_echo(echo, vault)  # type: ignore[attr-defined]
    assert found, echo
    assert all(item.startswith("⟦") for item in found)  # tokens: safe to report
    assert not any(secret in item for item in found for secret in ("4111", "ana.prueba", "1012345678"))


def test_text_without_the_values_is_clean_even_with_figures_and_tokens() -> None:
    service, vault = vault_with_the_customer_text()
    clean = "Tu saldo es de 1342.80 USD (⟦pii:1⟧), la fecha límite es 2026-10-20 y el cupo de 5000.00."
    assert service.find_tokenized_echo(clean, vault) == []  # type: ignore[attr-defined]


def test_an_unclassified_field_is_not_an_echo_target() -> None:
    """M7 tokeniza como `pii` todo valor sin clasificar (p. ej. el slot `vencido`): decirlo no es una fuga."""
    service = make_service()
    vault = TokenVault("run-0001", FakeKeyProvider.default(), FakeIds())
    projected = service.project("vencido", "slots", [], vault)
    assert projected.model != "vencido" and len(vault) == 1  # it went to the vault as `pii`
    assert service.find_tokenized_echo("El SLA está vencido.", vault) == []


def test_an_empty_vault_never_finds_anything() -> None:
    service = make_service()
    vault = TokenVault("run-0001", FakeKeyProvider.default(), FakeIds())
    assert service.find_tokenized_echo("4111111111111111 ana@example.test", vault) == []
