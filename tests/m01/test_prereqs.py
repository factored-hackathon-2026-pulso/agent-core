"""M1 necesita estos nombres de M0 (rev. 5). Si falla, M0 no está completo: detente y repórtalo."""

import agent_core.domain as domain
from agent_core.domain import EntityKind

NEEDED = [
    "Agent", "DecisionModelDef", "DECLARABLE", "DomainError", "EntityRef", "Flow", "InjectionRuleset",
    "Interrupt", "JsonValue", "LanguageDetection", "ModelProfile", "Outcome", "Policy", "Prompt",
    "RefSpec", "RegistryEntity", "Release", "RiskClass", "SchemaError", "StartFlowAction", "Template",
    "ToolDef", "is_declarable", "require_exact_refs", "node_kind", "RESULTS", "PRODUCTION_NODE_KINDS",
    "Node", "DecideNode", "RuleNode", "CollectNode", "ToolNode", "WriteToolNode", "ConfirmNode",
    "VerifyNode", "RespondNode", "EscalateNode", "EndNode", "AgentNode", "SlotValidator",
]


def test_domain_exports_what_m1_needs() -> None:
    missing = [name for name in NEEDED if not hasattr(domain, name)]
    assert missing == []


def test_model_profile_kind_exists() -> None:
    assert EntityKind.model_profile == "model_profile"


def test_ports_and_fakes_exist() -> None:
    from agent_core.adapters.system_clock import SystemClock
    from agent_core.ports import RegistryPort
    from testing.fakes.registry import InMemoryRegistry

    assert SystemClock and RegistryPort and InMemoryRegistry
