"""M0 — puertos hacia las unidades 2–7 (typing.Protocol, síncronos)."""

from agent_core.ports.audit import AuditSink, Outbox
from agent_core.ports.authz import AuthzDecision, AuthzPort
from agent_core.ports.clock import Clock
from agent_core.ports.costs import CostCounters
from agent_core.ports.directory import AgentDirectory, DirectoryMember
from agent_core.ports.export import RunExport, RunSummary
from agent_core.ports.identity import IdentityVerifier
from agent_core.ports.ids import IdKind, IdSource
from agent_core.ports.keys import KeyProvider, KeyPurpose
from agent_core.ports.knowledge import KnowledgeSource
from agent_core.ports.llm import GenerationResult, LLMGateway
from agent_core.ports.registry import RegistryPort
from agent_core.ports.tools import ToolCallContext, ToolExecutor, ToolResult, ToolStatus
from agent_core.ports.transcript import TranscriptStore
from agent_core.ports.uow import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AgentDirectory", "AuditSink", "AuthzDecision", "AuthzPort", "Clock", "CostCounters", "DirectoryMember",
    "GenerationResult", "IdKind", "IdSource", "IdentityVerifier", "KeyProvider", "KeyPurpose",
    "KnowledgeSource", "LLMGateway", "Outbox", "RegistryPort", "RunExport", "RunSummary", "ToolCallContext",
    "ToolExecutor", "ToolResult", "ToolStatus", "TranscriptStore", "UnitOfWork", "UnitOfWorkFactory",
]
