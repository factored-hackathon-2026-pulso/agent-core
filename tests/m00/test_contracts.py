import enum
import json
import re
import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel

from agent_core import domain as d
from agent_core.cli import main
from agent_core.contracts import PUBLIC_TYPES, check_contracts, render_contracts, write_contracts
from agent_core.domain import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_render_is_deterministic_and_complete() -> None:
    first, second = render_contracts(), render_contracts()
    assert first == second
    assert first["VERSION"] == SCHEMA_VERSION + "\n"
    for name in ("RunState", "AnyEvent", "Node", "Flow", "Agent", "TurnResult", "ProblemCode", "ToolResult"):
        assert f"schemas/{name}.json" in first
        json.loads(first[f"schemas/{name}.json"])


def test_every_public_domain_model_and_enum_is_published() -> None:
    """Un tipo nuevo exportado por `agent_core.domain` no puede quedar fuera del contrato."""
    exported = {
        name
        for name in d.__all__
        if isinstance(getattr(d, name), type)
        and issubclass(getattr(d, name), BaseModel | enum.Enum)
        and name not in {"Model", "MutableModel"}
    }
    assert exported <= set(PUBLIC_TYPES)
    assert {"GatewayErrorKind", "ModelProfile", "LlmUsage", "AnyEvent", "Node"} <= set(PUBLIC_TYPES)


def test_output_is_canonical_lf_sorted_with_generated_header() -> None:
    for rel, content in render_contracts().items():
        assert "\r" not in content
        assert content.endswith("\n") and not content.endswith("\n\n")
        if rel.startswith("schemas/"):
            parsed = json.loads(content)
            assert "no editar a mano" in parsed["$comment"]
            assert content == json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_names_are_safe_file_names() -> None:
    for rel in render_contracts():
        assert re.fullmatch(r"VERSION|schemas/[A-Za-z][A-Za-z0-9]*\.json", rel)


def test_schema_uses_wire_aliases() -> None:
    node = json.loads(render_contracts()["schemas/RespondNode.json"])
    props = node["$defs"]["RespondConfig"]["properties"]
    assert "await" in props and "await_" not in props


def test_no_env_or_secret_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTCORE_SECRET_PROBE", "s3cr3t-probe-value")
    assert all("s3cr3t-probe-value" not in c for c in render_contracts().values())


# T-M0-05
def test_committed_contracts_are_current() -> None:
    assert check_contracts(ROOT / "contracts") == []


def test_check_detects_stale_and_missing(tmp_path: Path) -> None:
    out = tmp_path / "contracts"
    write_contracts(out)
    assert check_contracts(out) == []
    (out / "schemas" / "RunState.json").write_text("{}\n", encoding="utf-8")
    (out / "schemas" / "Agent.json").unlink()
    (out / "schemas" / "Sobrante.json").write_text("{}\n", encoding="utf-8")
    (out / "schemas" / "notas.txt").write_text("x", encoding="utf-8")
    assert sorted(check_contracts(out)) == [
        "schemas/Agent.json",
        "schemas/RunState.json",
        "schemas/Sobrante.json",
        "schemas/notas.txt",
    ]


def test_check_detects_crlf_and_version_drift(tmp_path: Path) -> None:
    out = tmp_path / "contracts"
    write_contracts(out)
    path = out / "schemas" / "Flow.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    (out / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    assert check_contracts(out) == ["VERSION", "schemas/Flow.json"]


def test_write_only_touches_managed_paths(tmp_path: Path) -> None:
    out = tmp_path / "contracts"
    out.mkdir()
    (out / "openapi.json").write_text("{}\n", encoding="utf-8")  # lo agrega M9
    (out / "schemas").mkdir()
    (out / "schemas" / "Sobrante.json").write_text("{}\n", encoding="utf-8")
    write_contracts(out)
    assert (out / "openapi.json").read_text(encoding="utf-8") == "{}\n"
    assert not (out / "schemas" / "Sobrante.json").exists()
    assert check_contracts(out) == []
    written = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert written == set(render_contracts()) | {"openapi.json"}


def test_cli_check_exit_codes(tmp_path: Path) -> None:
    out = tmp_path / "c"
    assert main(["contracts", "--out", str(out)]) == 0
    assert main(["contracts", "--check", "--out", str(out)]) == 0
    shutil.rmtree(out / "schemas")
    assert main(["contracts", "--check", "--out", str(out)]) == 1


def test_cli_check_reports_drift_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "c"
    main(["contracts", "--out", str(out)])
    (out / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    assert main(["contracts", "--check", "--out", str(out)]) == 1
    assert "VERSION" in capsys.readouterr().err


def test_cli_requires_a_command() -> None:
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2
