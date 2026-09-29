"""La interfaz pública de M11 exporta lo que M4 y M9 consumen y nada de los internos."""

import agent_core.audit as audit

EXPECTED = {
    "AuditLog", "ChainCheck", "ChainedEvent", "TurnRecorder", "TranscriptReader", "RenderedEntry",
    "TranscriptWriteError", "RunNotFound", "TRANSCRIPT_PURPOSE", "export_events", "chain_integrity",
    "ReplayReport", "Divergence", "Replayer", "ReplayCase", "EngineRunner", "RecordedPorts", "ReplayDesync",
    "FullViewAccessError", "Fixture", "FullToolResult", "load_fixture", "dump_fixture", "load_fixture_file",
    "SyntheticCatalog", "load_catalog", "check_fixture", "FixtureRejected", "RecordingToolExecutor",
    "RecordingGateway", "build_fixture", "check_chain", "chain_events",
}


def test_exports_are_exactly_the_public_interface() -> None:
    assert set(audit.__all__) == EXPECTED
    for name in EXPECTED:
        assert hasattr(audit, name)
