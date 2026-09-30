from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain.entities import (
    ENTITY_KIND,
    Agent,
    Budgets,
    EscalateAction,
    Flow,
    InjectionRuleset,
    Interrupt,
    KnowledgePage,
    KnowledgeSnapshot,
    LanguageDetection,
    ModelProfile,
    Prompt,
    Release,
    StartFlowAction,
    StructuredMode,
    ToolDef,
)
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.nodes import WriteToolNode
from agent_core.domain.refs import EntityKind, require_exact_refs
from tests.m00.fixtures import DISPUTA_CARGO

TEMPLATES = {
    "clarify": "t/aclarar", "abstain": "t/abstencion", "handoff": "t/traspaso", "pending_ack": "t/acuse",
    "pending_offer": "t/oferta", "unsupported_language": "t/idioma_no_soportado",
    "input_too_large": "t/mensaje_largo",
}
BUDGETS = {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
           "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000}


def _agent(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "disputa-cargo@1",
        "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"], "default_locale": "es",
        "tools_allowed": ["buscar_transacciones@1"],
        "budgets": BUDGETS, "understand": "understand@1", "templates": TEMPLATES, "max_clarifications": 2,
        "on_clarify_exhausted": "escalate", "default_target_queue": "general",
    }
    return base | over


def test_example_flow_parses() -> None:
    flow = Flow.model_validate(DISPUTA_CARGO)
    assert flow.nodes[0].id == "pedir_cargo"
    assert isinstance(next(n for n in flow.nodes if n.id == "radicar"), WriteToolNode)


def test_authoring_flow_is_not_runtime_ready() -> None:
    with pytest.raises(InvalidRuntimeRef):
        require_exact_refs(Flow.model_validate(DISPUTA_CARGO))


def test_flow_requires_nodes() -> None:
    with pytest.raises(ValidationError):
        Flow.model_validate({**DISPUTA_CARGO, "nodes": []})


def test_agent_defaults_and_locale_rule() -> None:
    agent = Agent.model_validate(_agent())
    assert agent.inactivity_ttl == timedelta(minutes=30)
    assert agent.max_repair_turns_per_run == 8
    assert agent.budgets.max_cost_per_run == Decimal("0.50")
    with pytest.raises(ValidationError):
        Agent.model_validate(_agent(default_locale="en"))


def test_budgets_positive() -> None:
    with pytest.raises(ValidationError):
        Budgets.model_validate({**BUDGETS, "max_nodes_per_turn": 0})
    with pytest.raises(ValidationError):
        Budgets.model_validate({**BUDGETS, "max_cost_per_run": "0"})


def _tool(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible",
                            "min_auth_level": "session", "idempotent": False,
                            "readback_by": "idempotency_key"}
    return base | over


def test_write_tool_requires_readback() -> None:
    tool = ToolDef.model_validate(_tool())
    assert tool.is_write
    assert tool.confirmation_ttl == timedelta(minutes=5)
    with pytest.raises(ValidationError):
        ToolDef.model_validate(_tool(readback_by=None))
    assert not ToolDef.model_validate(_tool(risk_class="read", readback_by=None)).is_write


def test_interrupt_action_discriminated() -> None:
    esc = Interrupt.model_validate(
        {"id": "fraude", "priority": 100,
         "action": {"type": "escalate", "target_queue": "fraude", "priority": "critical"}}
    )
    assert isinstance(esc.action, EscalateAction)
    start = Interrupt.model_validate(
        {"id": "bloqueo", "priority": 90, "action": {"type": "start_flow", "flow": "bloquear@1.0.0"}}
    )
    assert isinstance(start.action, StartFlowAction)


def test_release_entities_must_be_exact() -> None:
    base: dict[str, Any] = {"id": "rel-1", "status": "active", "interrupts": [],
                            "language_detection": "lang-detect@1.0.0"}
    release = Release.model_validate({**base, "entities": {"flow": {"disputa-cargo": "1.0.0"}}})
    assert release.max_input_chars == 4000
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "entities": {"flow": {"disputa-cargo": "^1"}}})


# --- Endurecimiento y campos de la spec (rev. 5) ---

_MODEL_PROFILE: dict[str, Any] = {
    "id": "perfil-base", "version": "1.0.0", "endpoint_alias": "gw-default", "model": "modelo-sintetico",
    "temperature": "0", "max_tokens": 512,
    "price": {"input_per_mtok": "1.50", "output_per_mtok": "6.00", "source": "lista-sintetica",
              "as_of": "2026-09-28"},
}


def test_model_profile_and_prompt_pin_profile() -> None:
    profile = ModelProfile.model_validate(_MODEL_PROFILE)
    assert profile.timeout_s == 8
    assert profile.structured == StructuredMode.native
    assert profile.price.input_per_mtok == Decimal("1.50")
    prompt = Prompt.model_validate(
        {"id": "p", "version": "1.0.0", "locales": {"es": "hola"}, "model_profile": "perfil-base@^1"}
    )
    assert prompt.model_profile.id == "perfil-base"
    with pytest.raises(ValidationError):
        Prompt.model_validate({"id": "p", "version": "1.0.0", "locales": {"es": "hola"}})
    assert ENTITY_KIND[ModelProfile] is EntityKind.model_profile


@pytest.mark.parametrize("field", ["max_tokens", "timeout_s"])
def test_model_profile_positive_ints(field: str) -> None:
    with pytest.raises(ValidationError):
        ModelProfile.model_validate({**_MODEL_PROFILE, field: 0})


def test_model_price_not_negative_and_finite() -> None:
    price = {**_MODEL_PROFILE["price"], "input_per_mtok": "-1"}
    with pytest.raises(ValidationError):
        ModelProfile.model_validate({**_MODEL_PROFILE, "price": price})
    price = {**_MODEL_PROFILE["price"], "output_per_mtok": "NaN"}
    with pytest.raises(ValidationError):
        ModelProfile.model_validate({**_MODEL_PROFILE, "price": price})


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1"])
def test_budget_cost_rejects_non_finite(bad: str) -> None:
    with pytest.raises(ValidationError):
        Budgets.model_validate({**BUDGETS, "max_cost_per_run": bad})


def test_every_entity_kind_has_mapping() -> None:
    assert set(ENTITY_KIND.values()) == set(EntityKind)
    with pytest.raises(TypeError):
        ENTITY_KIND[Agent] = EntityKind.flow  # type: ignore[index]


def test_tool_durations_positive() -> None:
    for field in ("confirmation_ttl", "max_auth_age"):
        with pytest.raises(ValidationError):
            ToolDef.model_validate(_tool(**{field: "PT0S"}))


def test_agent_inactivity_ttl_positive() -> None:
    with pytest.raises(ValidationError):
        Agent.model_validate(_agent(inactivity_ttl="PT0S"))


@pytest.mark.parametrize("risk", ["write_reversible", "write_irreversible", "money_movement"])
def test_all_write_classes_need_readback(risk: str) -> None:
    with pytest.raises(ValidationError):
        ToolDef.model_validate(_tool(risk_class=risk, readback_by=None))


def test_release_interrupt_refs_must_be_exact() -> None:
    base: dict[str, Any] = {"id": "rel-1", "status": "active", "language_detection": "lang-detect@1.0.0"}
    bad = {"id": "x", "priority": 1, "action": {"type": "start_flow", "flow": "bloquear@^1"}}
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "interrupts": [bad]})
    escalate = {"type": "escalate", "target_queue": "q", "priority": "p"}
    unversioned = {"id": "x", "priority": 1, "action": escalate, "signal_policy": "regla-sin-version"}
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "interrupts": [unversioned]})


def test_release_rejects_unknown_kind_and_non_exact_ref() -> None:
    base: dict[str, Any] = {"id": "rel-1", "status": "active", "language_detection": "lang-detect@1.0.0"}
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "entities": {"desconocido": {"a": "1.0.0"}}})
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "language_detection": "lang-detect@^1"})
    with pytest.raises(ValidationError):
        Release.model_validate({**base, "injection_ruleset": "inj"})


def test_language_detection_detector_pinned() -> None:
    ok: dict[str, Any] = {"id": "ld", "version": "1.0.0", "detector": "lingua@1.3.2", "candidates": ["es"],
                          "min_letters": 12, "min_letters_unsupported": 24}
    LanguageDetection.model_validate(ok)
    for bad in ("lingua", "lingua@^1", "lingua@1.3", "lingua@1.3.2\n"):
        with pytest.raises(ValidationError):
            LanguageDetection.model_validate({**ok, "detector": bad})


def test_injection_rule_regex_must_compile() -> None:
    InjectionRuleset.model_validate(
        {"id": "inj", "version": "1.0.0", "rules": [{"id": "r1", "pattern": "ignora .*", "kind": "regex"}]}
    )
    with pytest.raises(ValidationError):
        InjectionRuleset.model_validate(
            {"id": "inj", "version": "1.0.0", "rules": [{"id": "r1", "pattern": "([", "kind": "regex"}]}
        )
    with pytest.raises(ValidationError):
        InjectionRuleset.model_validate(
            {"id": "inj", "version": "1.0.0", "rules": [{"id": "r1", "pattern": "", "kind": "phrase"}]}
        )
    # una frase con metacaracteres es literal: no se compila
    InjectionRuleset.model_validate(
        {"id": "inj", "version": "1.0.0", "rules": [{"id": "r1", "pattern": "([", "kind": "phrase"}]}
    )


def test_entities_are_immutable_and_forbid_extras() -> None:
    with pytest.raises(ValidationError):
        Agent.model_validate(_agent(extra_field=1))
    agent = Agent.model_validate(_agent())
    with pytest.raises(ValidationError):
        agent.default_locale = "pt"  # type: ignore[misc]


def test_fixture_is_synthetic_and_has_no_secrets() -> None:
    text = str(DISPUTA_CARGO).lower()
    for marker in ("akia", "secret", "password", "token", "api_key"):
        assert marker not in text


def test_injection_rule_pattern_is_bounded() -> None:
    from pydantic import ValidationError

    from agent_core.domain.entities import InjectionRule

    InjectionRule(id="r", pattern="a" * 2048, kind="phrase")
    with pytest.raises(ValidationError):
        InjectionRule(id="r", pattern="a" * 2049, kind="phrase")


# --- Snapshot de conocimiento (registry, ADR 0015 y 0017) ---

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _page(path: str = "faq/disputas", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"path": path, "hash": _HASH_A, "audience": "public", "status": "approved",
                            "approved_by": "aprobador-1", "lang": "es"}
    return base | over


def _snapshot(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"id": "kb-base", "version": "1.0.0", "pages": list(pages) or [_page()]}


def test_knowledge_snapshot_is_a_registry_entity() -> None:
    snapshot = KnowledgeSnapshot.model_validate(_snapshot(_page(), _page("faq/disputas-pt", lang="pt",
                                                                        translation_of="faq/disputas")))
    assert ENTITY_KIND[KnowledgeSnapshot] is EntityKind.knowledge_snapshot
    assert snapshot.pages[0].source_refs == []
    assert snapshot.pages[1].translation_of == "faq/disputas"


def test_knowledge_page_approval_is_coherent() -> None:
    with pytest.raises(ValidationError):  # aprobada sin quién la aprobó
        KnowledgePage.model_validate(_page(approved_by=None))
    with pytest.raises(ValidationError):  # borrador con aprobador
        KnowledgePage.model_validate(_page(status="draft", approved_by="aprobador-1"))
    assert KnowledgePage.model_validate(_page(status="draft", approved_by=None)).status == "draft"


@pytest.mark.parametrize("bad", ["../secreto", "a/../b", "/abs", "a//b", "", " a", "a b", "ñ/x", "a/"])
def test_knowledge_page_path_must_be_safe_and_relative(bad: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgePage.model_validate(_page(bad))


def test_knowledge_page_hash_and_enums_are_strict() -> None:
    for over in ({"hash": "A" * 64}, {"hash": "abc"}, {"audience": "cliente"}, {"status": "publicada"},
                 {"lang": "es-CO"}):
        with pytest.raises(ValidationError):
            KnowledgePage.model_validate(_page(**over))


def test_knowledge_page_validity_range_is_ordered() -> None:
    ok = KnowledgePage.model_validate(_page(valid_from="2026-01-01", valid_to="2026-01-01"))
    assert ok.valid_from == ok.valid_to
    with pytest.raises(ValidationError):
        KnowledgePage.model_validate(_page(valid_from="2026-02-01", valid_to="2026-01-01"))


def test_knowledge_snapshot_rejects_duplicate_paths_and_dangling_translations() -> None:
    with pytest.raises(ValidationError):
        KnowledgeSnapshot.model_validate(_snapshot(_page(), _page(hash=_HASH_B)))
    with pytest.raises(ValidationError):
        KnowledgeSnapshot.model_validate(_snapshot(_page(translation_of="no-existe")))
    with pytest.raises(ValidationError):  # una página no es traducción de sí misma
        KnowledgeSnapshot.model_validate(_snapshot(_page(translation_of="faq/disputas")))


def test_knowledge_snapshot_is_bounded_and_immutable() -> None:
    with pytest.raises(ValidationError):
        KnowledgePage.model_validate(_page(source_refs=[f"ref-{i}" for i in range(51)]))
    with pytest.raises(ValidationError):
        KnowledgePage.model_validate(_page(source_refs=["x" * 501]))
    snapshot = KnowledgeSnapshot.model_validate(_snapshot())
    with pytest.raises(ValidationError):
        snapshot.version = "2.0.0"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        KnowledgeSnapshot.model_validate({**_snapshot(), "extra": 1})


def test_release_pins_an_optional_exact_knowledge_snapshot() -> None:
    base: dict[str, Any] = {"id": "rel-1", "status": "active", "interrupts": [],
                            "language_detection": "lang-detect@1.0.0"}
    assert Release.model_validate(base).knowledge_snapshot is None
    release = Release.model_validate({**base, "knowledge_snapshot": "kb-base@1.0.0"})
    assert release.knowledge_snapshot is not None and str(release.knowledge_snapshot) == "kb-base@1.0.0"
    for bad in ("kb-base@^1", "kb-base", "kb-base@1"):
        with pytest.raises(ValidationError):
            Release.model_validate({**base, "knowledge_snapshot": bad})


def test_tool_def_documentation_fields_are_optional_and_round_trip() -> None:
    base = {"id": "leer", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
            "idempotent": True}
    bare = ToolDef.model_validate(base)
    assert bare.description is None and bare.args_schema is None
    doc = ToolDef.model_validate(base | {"description": "Lee un cargo", "args_schema": {"type": "object"}})
    assert doc.description == "Lee un cargo" and doc.args_schema == {"type": "object"}
