"""`TranscriptStore` persistente para `serve --transcript agent_core.composition.transcript:transcript`."""

from agent_core.adapters.postgres_transcript import PgTranscriptStore
from agent_core.composition.serve_ports import DemoContext
from agent_core.domain import SchemaError


def transcript(ctx: DemoContext) -> PgTranscriptStore:
    """Comparte la base y el pool del motor (`DemoContext.store`): no abre conexiones propias."""
    if ctx.store is None:
        raise SchemaError("transcript: el contexto no trae la base del motor")
    return PgTranscriptStore(ctx.store)
