"""`agentcore replay` y `record`: códigos de salida (decisión 17) y sin motor disponible."""

from pathlib import Path

import pytest

from agent_core.audit import Fixture, chain_events, dump_fixture
from agent_core.cli import main
from tests.m11.helpers import event


def write_fixture(tmp_path: Path, tamper: bool = False) -> Path:
    events = chain_events("run-0001", [event("run_started", turn_id=None), event("run_closed", n=2)], None)
    if tamper:
        events[1] = events[1].model_copy(update={"release": "otra"})
    path = tmp_path / "camino.yaml"
    fixture = Fixture(name="camino", run_id="run-0001", release="rel-2026-09-28", inputs=[],
                      events=events, full={}, drafts=[])
    path.write_text(dump_fixture(fixture), encoding="utf-8")
    return path


def test_replay_chain_broken_exits_2_even_without_an_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["replay", str(write_fixture(tmp_path, tamper=True)), "--mode", "fixture", "--json"])
    assert code == 2 and '"chain_broken"' in capsys.readouterr().out


def test_replay_without_engine_available_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", str(write_fixture(tmp_path)), "--mode", "fixture"])
    assert code == 3 and "motor" in capsys.readouterr().err


def test_replay_rejects_unsynthetic_fixture_with_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_fixture(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("inputs: []", "inputs:\n- text_model: mail real@banco.com"),
        encoding="utf-8")
    catalog = "tests/fixtures/catalogo-datos-prueba.yaml"
    code = main(["replay", str(path), "--mode", "fixture", "--catalog", catalog])
    assert code == 3 and "no sintéticos" in capsys.readouterr().err


def test_record_without_engine_exits_3(tmp_path: Path) -> None:
    assert main(["record", "resuelto", "--out", str(tmp_path / "x.yaml")]) == 3
