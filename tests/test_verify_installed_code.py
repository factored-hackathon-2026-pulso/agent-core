from pathlib import Path

from scripts.verify_installed_code import mismatches


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_identical_trees_have_no_mismatches(tmp_path: Path) -> None:
    _tree(tmp_path / "src/pkg", {"a.py": "x=1", "sub/b.py": "y=2"})
    _tree(tmp_path / "site", {"a.py": "x=1", "sub/b.py": "y=2"})
    assert mismatches(tmp_path / "src", tmp_path / "site", "pkg") == []


def test_stale_installed_code_is_reported(tmp_path: Path) -> None:
    _tree(tmp_path / "src/pkg", {"a.py": "x=2", "new.py": "z=1"})
    _tree(tmp_path / "site", {"a.py": "x=1", "gone.py": "q=1"})
    assert mismatches(tmp_path / "src", tmp_path / "site", "pkg") == ["a.py", "gone.py", "new.py"]


def test_dockerfile_project_layer_does_not_reuse_the_uv_cache() -> None:
    text = Path("Dockerfile").read_text(encoding="utf-8")
    project = text.split("COPY agent_telemetry")[1].split("FROM python")[0]
    assert "--mount=type=cache" not in project and "UV_NO_CACHE=1" in project
    assert "--reinstall-package agent-core" in project and "verify_installed_code.py" in project
