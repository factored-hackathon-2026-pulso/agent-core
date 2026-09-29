import json
import shutil
from pathlib import Path

import pytest

from agent_core.adapters.system_clock import SystemClock
from agent_core.cli import main
from agent_core.flows.cli_validate import MAX_PRINTED, format_report
from agent_core.flows.validate import validate_flow
from agent_core.flows.violations import Violation
from tests.m01.cases import flow, registry

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
ROTO = """id: roto
version: 1.0.0
priority: 1
nodes:
  - id: leer
    type: tool
    config: {tool: buscar_transacciones@1, args: {texto: slots.x}, save_as: d}
    next: {ok: fin, error: esc, timeout: esc}
  - {id: fin, type: end, config: {outcome: resolved}}
  - {id: esc, type: escalate, config: {reason_code: tool_failure}}
"""


# T-M1-44
def test_cli_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(FIXTURE)]) == 0
    assert "sin violaciones" in capsys.readouterr().out


def test_cli_json_with_violation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    (root / "flows" / "roto@1.0.0.yaml").write_text(ROTO, encoding="utf-8")
    assert main(["validate", str(root), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == 1 and data["ok"] is False
    assert data["counts"] == {"G0-03": 1}
    assert data["violations"][0]["flow"] == "roto@1.0.0"
    assert data["violations"][0]["node_id"] == "leer"


def test_cli_text_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    (root / "flows" / "roto@1.0.0.yaml").write_text(ROTO, encoding="utf-8")
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    assert "G0-03 roto@1.0.0 leer /nodes/0/next: el resultado 'denied' no tiene next" in out


def test_cli_missing_root(tmp_path: Path) -> None:
    assert main(["validate", str(tmp_path / "nada")]) == 2


def _long_flow(k: int) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {"id": f"r{i}", "type": "respond", "config": {"template_ref": "t/seguro"},
         "next": {"next": f"r{i + 1}"}}
        for i in range(99)
    ]
    nodes.append({"id": "r99", "type": "end", "config": {"outcome": "resolved"}})
    return {"id": f"perf-{k}", "version": "1.0.0", "priority": 1, "nodes": nodes}


def test_validation_time_budget() -> None:
    flows = [flow(_long_flow(k)) for k in range(50)]
    reg = registry(*flows)
    clock = SystemClock()
    start = clock.monotonic_ns()
    for item in flows:
        assert validate_flow(item, reg) == []
    assert (clock.monotonic_ns() - start) / 1e9 < 2.0


def _broken_registry(tmp_path: Path, count: int = 1) -> Path:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    for k in range(count):
        text = ROTO.replace("id: roto", f"id: roto{k}")
        (root / "flows" / f"roto{k}@1.0.0.yaml").write_text(text, encoding="utf-8")
    return root


def test_cli_output_is_byte_identical(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _broken_registry(tmp_path, 3)
    outs = []
    for args in (["validate", str(root)], ["validate", str(root)], ["validate", str(root), "--json"],
                 ["validate", str(root), "--json"]):
        assert main(args) == 1
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1] and outs[2] == outs[3]


def test_cli_hides_machine_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _broken_registry(tmp_path)
    (root / "flows" / "malo@1.0.0.yaml").write_text("a: [", encoding="utf-8")
    assert main(["validate", str(root)]) == 1
    assert main(["validate", str(root), "--json"]) == 1
    out = capsys.readouterr().out
    assert str(tmp_path) not in out and tmp_path.as_posix() not in out
    assert main(["validate", str(tmp_path / "nada")]) == 2
    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out + captured.err


def test_cli_root_is_a_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "archivo.txt"
    target.write_text("x", encoding="utf-8")
    assert main(["validate", str(target)]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_cli_caps_printed_violations_but_not_exit_code() -> None:
    violations = [
        Violation(rule="G0-03", flow="f@1.0.0", node_id=f"n{i:04d}", message="m")
        for i in range(MAX_PRINTED + 7)
    ]
    text_lines = format_report(violations, as_json=False).splitlines()
    assert len(text_lines) == MAX_PRINTED + 2
    assert text_lines[-2] == "y 7 más"
    assert f"{MAX_PRINTED + 7} violaciones: G0-03={MAX_PRINTED + 7}" in text_lines[-1]
    data = json.loads(format_report(violations, as_json=True))
    assert len(data["violations"]) == MAX_PRINTED and data["omitted"] == 7
    assert data["counts"] == {"G0-03": MAX_PRINTED + 7} and data["ok"] is False


def test_cli_timing_only_on_stderr_in_text_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(FIXTURE)]) == 0
    captured = capsys.readouterr()
    assert "ms" in captured.err and "ms" not in captured.out
    assert main(["validate", str(FIXTURE), "--json"]) == 0
    assert capsys.readouterr().err == ""
