"""Renderer autorizado por campo (M7 §3.5). T-M7-04."""

from agent_core.views.service import Rendered
from agent_core.views.vault import TokenVault
from testing.builders import advisor_with_delegation, principal
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m07.helpers import FieldAuthz, make_service, make_vault

DOC = "1023456789"


def _setup(grants: set[tuple[str, str, str]]) -> tuple[FieldAuthz, FakeKeyProvider, TokenVault]:
    keys = FakeKeyProvider.default()
    return FieldAuthz(grants), keys, make_vault(keys=keys)


def test_t_m7_04_authorized_advisor_sees_value_others_get_the_mask() -> None:
    """T-M7-04: el renderer muestra al asesor autorizado y enmascara al no autorizado."""
    authz, keys, vault = _setup({("adv-7", "document_number", "handoff")})
    service = make_service(keys=keys, authz=authz)
    advisor, obo = advisor_with_delegation()
    text = f"Cliente con documento {vault.tokenize(DOC, 'document_number', 'doc')}"
    assert service.render(text, vault, advisor, "handoff", obo).text == f"Cliente con documento {DOC}"
    assert service.render(text, vault, advisor, "handoff").text == "Cliente con documento ***6789"
    assert service.render(text, vault, advisor, "otro_proposito", obo).text == "Cliente con documento ***6789"
    assert service.render(text, vault, principal(), "handoff").text == "Cliente con documento ***6789"


def test_authorization_is_per_field() -> None:
    authz, keys, vault = _setup({("adv-7", "email", "handoff")})
    service = make_service(keys=keys, authz=authz)
    advisor, obo = advisor_with_delegation()
    doc_token = vault.tokenize(DOC, "document_number", "doc")
    email_token = vault.tokenize("ana@example.test", "email", "email")
    text = f"{doc_token} / {email_token}"
    assert service.render(text, vault, advisor, "handoff", obo).text == "***6789 / ana@example.test"
    assert ("adv-7", "document_number", "handoff") in authz.calls


def test_unknown_token_is_masked_and_reported() -> None:
    authz, keys, vault = _setup(set())
    advisor, obo = advisor_with_delegation()
    result = make_service(keys=keys, authz=authz).render("ver ⟦doc:9⟧", vault, advisor, "handoff", obo)
    assert result == Rendered(text="ver ***", unknown_tokens=["⟦doc:9⟧"])


def test_rendered_values_are_not_rescanned() -> None:
    authz, keys, vault = _setup({("adv-7", "first_name", "handoff")})
    advisor, obo = advisor_with_delegation()
    vault.tokenize(DOC, "document_number", "doc")
    token = vault.tokenize("⟦doc:1⟧", "first_name", "name")
    result = make_service(keys=keys, authz=authz).render(token, vault, advisor, "handoff", obo)
    assert result == Rendered(text="⟦doc:1⟧", unknown_tokens=[])


def test_render_after_seal_and_open() -> None:
    authz, keys, vault = _setup({("adv-7", "document_number", "handoff")})
    ids = FakeIds()
    token = vault.tokenize(DOC, "document_number", "doc")
    reopened = TokenVault.open(vault.seal(), "run-0001", keys, ids)
    advisor, obo = advisor_with_delegation()
    assert make_service(keys=keys, authz=authz).render(token, reopened, advisor, "handoff", obo).text == DOC
