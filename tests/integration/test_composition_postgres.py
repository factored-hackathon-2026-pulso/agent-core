"""Motor compuesto (M2–M11 reales) sobre Postgres real: escenario `disputa-cargo` de punta a punta.

Solo los puertos externos son dobles (LLM, proveedores, tools, autorización); el almacenamiento, la cadena de
auditoría y el outbox son los de Postgres."""

import pytest

from agent_core.audit import AuditLog
from agent_core.domain import Outcome, ResponseEmitted
from testing.engine_world import DRAFT, EngineWorld
from tests.support.pg import postgres_store

pytestmark = pytest.mark.integration

TEXT = "no reconozco un cargo de ciento veinte dólares en una tienda"


def test_disputa_cargo_de_punta_a_punta_sobre_postgres() -> None:
    with postgres_store("cableado_e2e") as pg:
        w = EngineWorld(uow_factory=pg.uow, audit=pg.audit())
        started = w.start()
        w.understands("continue")
        w.matches()
        prompt = w.turn(TEXT)
        assert prompt.confirmation is not None
        done = w.confirm("yes")

        assert [m.text for m in done.messages] == [DRAFT] and done.messages[0].kind == "generated"
        with pg.uow() as uow:
            state = uow.load_run(started.run_id)
        assert state is not None and state.status == "closed" and state.outcome is Outcome.resolved
        assert state.budgets_used.run_tokens >= 52  # los 52 tokens del borrador de M8, cobrados por M2

        events = w.audit.read(started.run_id)
        (emitted,) = [e for e in events if isinstance(e, ResponseEmitted)]
        assert emitted.payload.llm is not None and emitted.payload.llm.calls == 1
        assert emitted.turn_id is not None and emitted.payload.transcript_fp is not None
        assert AuditLog(w.audit).verify_chain(started.run_id).ok
        assert {"turn_started", "decision_made", "command_emitted", "response_emitted", "turn_completed",
                "run_closed"} <= {e.type for e in events}
        assert len(w.transcript.read(started.run_id)) == 6


def test_dos_corridas_del_mismo_guion_producen_los_mismos_eventos() -> None:
    """Determinismo (regla 3): mismos puertos y mismo reloj, mismos eventos; base del replay."""

    def run(schema: str) -> list[tuple[str, str | None]]:
        with postgres_store(schema) as pg:
            w = EngineWorld(uow_factory=pg.uow, audit=pg.audit())
            started = w.start()
            w.understands("continue")
            w.matches()
            prompt = w.turn(TEXT)
            assert prompt.confirmation is not None
            w.confirm("yes")
            return [(e.type, e.turn_id) for e in w.audit.read(started.run_id)]

    assert run("cableado_det_a") == run("cableado_det_b")
