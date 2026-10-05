import importlib
import inspect
import pkgutil
from typing import Protocol

import agent_core.domain as domain
import agent_core.ports as ports


def test_domain_exports() -> None:
    for name in ["RunState", "Principal", "EntityRef", "RefSpec", "Node", "Flow", "Agent", "Release",
                 "ToolDef",
                 "AnyEvent", "EngineEvent", "EngineError", "ProblemCode", "canonical_bytes", "loads", "dumps",
                 "EscalationRequest", "RejectedDraft", "ConfirmationPrompt", "TurnResult", "SCHEMA_VERSION",
                 "GatewayError", "GatewayErrorKind", "KnowledgePage", "KnowledgeSnapshot", "KnowledgeNode",
                 "KnowledgeView", "PageMeta", "PageView", "PageRecord", "Purpose", "KnowledgeRead"]:
        assert hasattr(domain, name), name
    assert domain.SCHEMA_VERSION == "1.7.0"


def test_ports_exports() -> None:
    for name in ["Clock", "IdSource", "IdKind", "RegistryPort", "ToolExecutor", "ToolResult", "ToolStatus",
                 "ToolCallContext", "AuthzPort", "AuthzDecision", "IdentityVerifier", "UnitOfWork",
                 "UnitOfWorkFactory", "AuditSink", "Outbox", "LLMGateway", "GenerationResult",
                 "TranscriptStore",
                 "KnowledgeSource", "KeyProvider", "KeyPurpose", "CostCounters"]:
        assert hasattr(ports, name), name


# Constantes de módulo que son detalle interno o un import (`UTC`), no contrato.
_INTERNAL = {"UTC", "MAX_DECIMAL_EXPONENT", "NUM_PATTERN", "ID_PATTERN", "EXACT_VERSION_PATTERN",
             "MAX_PAGE_SOURCE_REFS", "TYPE_CHECKING"}


def _public_defs(module_name: str) -> set[str]:
    mod = importlib.import_module(module_name)
    found = set()
    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if (inspect.isclass(obj) or inspect.isfunction(obj)) and obj.__module__ == module_name:
            found.add(name)
        elif name.isupper() and name not in _INTERNAL:  # constantes públicas (SCHEMA_VERSION, RESULTS...)
            found.add(name)
    return found


def test_domain_reexports_every_public_definition() -> None:
    """Ninguna clase/función/constante pública de un módulo de domain queda fuera de `agent_core.domain`."""
    missing: dict[str, set[str]] = {}
    for info in pkgutil.iter_modules(domain.__path__):
        module_name = f"agent_core.domain.{info.name}"
        gap = _public_defs(module_name) - set(domain.__all__)
        if gap:
            missing[module_name] = gap
    assert not missing, missing


def test_domain_all_is_resolvable_and_unique() -> None:
    assert len(domain.__all__) == len(set(domain.__all__))
    for name in domain.__all__:
        assert hasattr(domain, name), name


def test_ports_all_is_resolvable_and_reexports_every_public_definition() -> None:
    assert len(ports.__all__) == len(set(ports.__all__))
    for name in ports.__all__:
        assert hasattr(ports, name), name
    missing: dict[str, set[str]] = {}
    for info in pkgutil.iter_modules(ports.__path__):
        module_name = f"agent_core.ports.{info.name}"
        gap = _public_defs(module_name) - set(ports.__all__)
        if gap:
            missing[module_name] = gap
    assert not missing, missing


def test_tool_status_is_the_domain_one() -> None:
    assert ports.ToolStatus is domain.ToolStatus


# Métodos por puerto según M0 §2.9 (el spec manda sobre el plan).
SPEC_METHODS: dict[str, set[str]] = {
    "Clock": {"now", "monotonic_ns"},
    "IdSource": {"new_id", "secret_token"},
    "RegistryPort": {"resolve_release", "release_status", "get"},
    "ToolExecutor": {"execute", "definition"},
    "AuthzPort": {"authorize_agent", "authorize_subject", "bind_params", "can_read_field", "knowledge_view",
                  "reportable_attrs"},
    "IdentityVerifier": {"verify", "verify_delegation", "grant_active"},
    "UnitOfWork": {"acquire_turn", "release_turn", "load_run", "find_run_by_session", "list_runs_by_session",
                   "save_run",
                   "get_turn_result", "put_turn_result", "get_run_idempotency", "put_run_idempotency",
                   "reserve_run_idempotency", "release_run_idempotency",
                   "put_handoff", "get_handoff", "append_events", "last_event", "enqueue_outbox", "add_usage",
                   "list_inactive", "commit", "__enter__", "__exit__"},
    "AuditSink": {"read", "append_outside_turn"},
    "Outbox": {"pending", "mark_delivered"},
    "LLMGateway": {"generate"},
    "TranscriptStore": {"append", "write_turn", "read", "recent_turns"},
    "KnowledgeSource": {"capabilities", "index", "read"},
    "KeyProvider": {"current_kid", "key"},
    "CostCounters": {"spent_today", "hits"},
}


def test_every_port_is_a_protocol_with_exactly_the_spec_methods() -> None:
    for name, expected in SPEC_METHODS.items():
        proto = getattr(ports, name)
        assert Protocol in proto.__mro__, name
        actual = {
            m for m, v in vars(proto).items()
            if callable(v) and (not m.startswith("_") or m in {"__enter__", "__exit__"})
        }
        assert actual == expected, (name, actual ^ expected)


def test_generation_result_reports_tokens_in_and_out() -> None:
    fields = set(ports.GenerationResult.model_fields)
    assert fields == {"output", "tokens_in", "tokens_out", "cost_usd", "model", "usage_known"}


def test_tool_result_fields_match_spec() -> None:
    assert set(ports.ToolResult.model_fields) == {
        "status", "result_full", "source", "call_id", "error", "required_level"}
    assert set(ports.ToolCallContext.model_fields) == {
        "run_id", "release", "principal", "on_behalf_of", "subject", "turn_id", "at"}


def test_id_kinds_match_spec() -> None:
    assert {k.value for k in ports.IdKind} == {
        "run", "session", "turn", "action", "decision", "fact", "call", "handoff", "event", "message",
        "proposal", "eval_run", "transfer"}
    assert {k.value for k in ports.KeyPurpose} == {"fingerprint", "token_map"}


def _missing_docstrings(module: object) -> list[str]:
    """Clases y funciones exportadas cuyo `__dict__` propio no trae docstring (no vale el heredado)."""
    exported = getattr(module, "__all__")  # noqa: B009
    missing = []
    for name in exported:
        obj = getattr(module, name)
        if inspect.isclass(obj):
            doc = vars(obj).get("__doc__")
        elif inspect.isfunction(obj):
            doc = obj.__doc__
        else:
            continue
        if not (isinstance(doc, str) and doc.strip()):
            missing.append(name)
    return sorted(missing)


def test_every_exported_class_and_function_has_its_own_docstring() -> None:
    """DoD M0 §10: la API pública está documentada."""
    assert _missing_docstrings(domain) == []
    assert _missing_docstrings(ports) == []


def test_port_time_parameters_are_utc_aware_annotated() -> None:
    """M0 §2.9: todo instante que cruza un puerto es `UtcDatetime`, no un `datetime` suelto."""
    import datetime
    import typing

    from agent_core.domain.base import UtcDatetime

    for name in ports.__all__:
        proto = getattr(ports, name)
        if not (inspect.isclass(proto) and Protocol in proto.__mro__):
            continue
        for method_name, method in vars(proto).items():
            if not callable(method) or method_name.startswith("__"):
                continue
            for arg, hint in typing.get_type_hints(method, include_extras=True).items():
                assert hint is not datetime.datetime, f"{name}.{method_name}({arg}) usa datetime plano"
                if arg in {"now", "return"} and "datetime" in repr(hint):
                    assert hint == UtcDatetime, (name, method_name, arg)
