from agent_core.response.checks import check_tokens_pii
from agent_core.response.types import Draft
from agent_core.views import TokenVault
from tests.m07.helpers import make_service, make_vault
from tests.m08.helpers import make_ctx

FACTS_FULL = {"cliente": {"document_number": "1023456789", "email": "ana@example.test"}}


def _check(text: str, vault: TokenVault | None = None, *, with_pii: bool = False):  # type: ignore[no-untyped-def]
    over: dict[str, object] = {"vault": vault or make_vault()}
    if with_pii:
        service = make_service()
        over["find_clear_pii"] = lambda t: service.find_clear_pii(t, FACTS_FULL)
    return check_tokens_pii(Draft(text=text, citations=[]), make_ctx(**over))


def test_t_m8_06a_unknown_token_fails_and_known_token_passes() -> None:
    failures = _check("Tu documento es ⟦doc:7⟧")
    assert [f.check for f in failures] == ["tokens_pii"]
    assert failures[0].detail == "token desconocido en la posición 1" and "⟦" not in failures[0].detail
    vault = make_vault()
    token = vault.tokenize("1023456789", "document_number", "doc")
    assert _check(f"Tu documento es {token}", vault) == []


def test_t_m8_06b_document_in_clear_fails_with_path_and_without_value() -> None:
    failures = _check("tu documento 1023456789", with_pii=True)
    assert [f.check for f in failures] == ["tokens_pii"]
    assert "cliente.document_number" in failures[0].detail and "1023456789" not in failures[0].detail


def test_email_in_clear_reports_pattern() -> None:
    failures = _check("escribe a otra@example.test", with_pii=True)
    assert "pattern:email" in failures[0].detail and "otra@example.test" not in failures[0].detail


def test_clean_text_passes() -> None:
    assert _check("Tu caso sigue en revisión.", with_pii=True) == []


def test_unknown_token_does_not_leak_vault_values() -> None:
    vault = make_vault()
    vault.tokenize("1023456789", "document_number", "doc")
    failures = _check("⟦doc:9⟧", vault)
    assert all("1023456789" not in f.detail for f in failures)
