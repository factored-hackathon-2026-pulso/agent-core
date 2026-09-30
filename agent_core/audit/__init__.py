"""M11 — auditoría, transcript y replay (ADR 0003, ADR 0008)."""

from agent_core.audit.chain import ChainCheck, ChainedEvent, chain_events, check_chain
from agent_core.audit.export import chain_integrity, export_events
from agent_core.audit.log import AuditLog
from agent_core.audit.replay.fixture import (
    Fixture,
    FullToolResult,
    dump_fixture,
    load_fixture,
    load_fixture_file,
)
from agent_core.audit.replay.ports import FullViewAccessError, RecordedPorts, ReplayDesync
from agent_core.audit.replay.recording import RecordingGateway, RecordingToolExecutor, build_fixture
from agent_core.audit.replay.report import Divergence, ReplayReport
from agent_core.audit.replay.runner import EngineRunner, ReplayCase, Replayer
from agent_core.audit.replay.synthetic import FixtureRejected, SyntheticCatalog, check_fixture, load_catalog
from agent_core.audit.transcript import (
    TRANSCRIPT_PURPOSE,
    RenderedEntry,
    RunNotFound,
    TranscriptReader,
    TranscriptWriteError,
    TurnRecorder,
)

__all__ = [
    "TRANSCRIPT_PURPOSE", "AuditLog", "ChainCheck", "ChainedEvent", "Divergence", "EngineRunner", "Fixture",
    "FixtureRejected", "FullToolResult", "FullViewAccessError", "RecordedPorts", "RecordingGateway",
    "RecordingToolExecutor", "RenderedEntry", "ReplayCase", "ReplayDesync", "ReplayReport", "Replayer",
    "RunNotFound", "SyntheticCatalog", "TranscriptReader", "TranscriptWriteError", "TurnRecorder",
    "build_fixture", "chain_events", "chain_integrity", "check_chain", "check_fixture", "dump_fixture",
    "export_events", "load_catalog", "load_fixture", "load_fixture_file",
]
