"""`agentcore sweep` cableado sobre Postgres real (m04 §3.6 y §10)."""

from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

from agent_core.audit import AuditLog
from agent_core.cli import main
from agent_core.domain import Outcome
from testing.builders import NOW, action, run_state
from testing.fakes.clock import FakeClock
from tests.support.pg import ADMIN_DSN, postgres_store

pytestmark = pytest.mark.integration

REGISTRY = Path(__file__).resolve().parents[1] / "m01" / "fixtures" / "registry"
SCHEMA = "m4_sweep"


def _dsn() -> str:
    return f"{ADMIN_DSN}?options={quote(f'-c search_path={SCHEMA}')}"


def test_sweep_cierra_abandoned_los_runs_vencidos_y_deja_la_cadena_valida(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with postgres_store(SCHEMA) as store:
        with store.uow() as uow:
            uow.save_run(run_state(
                run_id="run-a", session_id="s-a", inactive_after=NOW + timedelta(minutes=30),
                active_flow={"flow": "disputa@1.0.0", "node_id": "confirmar"}, awaiting="confirmation",
                awaiting_node_id="confirmar", actions=[action(flow="disputa@1.0.0", state="proposed")]), 0)
            uow.save_run(run_state(run_id="run-b", session_id="s-b",
                                   inactive_after=NOW + timedelta(hours=5)), 0)
            uow.commit()
        clock = FakeClock()
        clock.advance(timedelta(hours=1))
        code = main(["sweep", "--once", "--dsn", _dsn(), "--registry", str(REGISTRY)], clock=clock)
        assert code == 0
        assert capsys.readouterr().out.strip() == "evaluated=1 abandoned=1 skipped=0"
        with store.uow() as uow:
            closed, alive = uow.load_run("run-a"), uow.load_run("run-b")
        assert closed is not None and closed.status == "closed" and closed.outcome is Outcome.abandoned
        assert alive is not None and alive.status == "open"
        types = [e.type for e in store.audit().read("run-a")]  # type: ignore[attr-defined]
        assert "expiry_evaluated" in types and types[-1] == "run_closed"
        assert AuditLog(store.audit()).verify_chain("run-a").ok


def test_sweep_sin_agente_en_el_registro_falla_sin_imprimir_el_dsn(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    with postgres_store(SCHEMA) as store:
        with store.uow() as uow:
            uow.save_run(run_state(run_id="run-a", session_id="s-a"), 0)
            uow.commit()
        clock = FakeClock()
        clock.advance(timedelta(hours=1))
        code = main(["sweep", "--dsn", _dsn(), "--registry", str(tmp_path)], clock=clock)
        captured = capsys.readouterr()
        assert code != 0 and "agentcore-dev-only" not in captured.out + captured.err
        with store.uow() as uow:
            run = uow.load_run("run-a")
        assert run is not None and run.status == "open"  # nada a medias


def test_sweep_con_dsn_inalcanzable_no_filtra_la_credencial(capsys: pytest.CaptureFixture[str]) -> None:
    dsn = "postgresql://agentcore:secreto-inventado@127.0.0.1:1/agentcore?connect_timeout=1"
    code = main(["sweep", "--dsn", dsn, "--registry", str(REGISTRY)])
    captured = capsys.readouterr()
    assert code != 0 and "secreto-inventado" not in captured.out + captured.err
