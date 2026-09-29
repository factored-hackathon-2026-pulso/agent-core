import configparser
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import pytest

import agent_core

ROOT = Path(__file__).resolve().parents[2]

_LINT_IMPORTS = (
    "import sys; sys.path.insert(0, '.'); from importlinter.cli import lint_imports_command; "
    "lint_imports_command()"
)


def _ruff(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--config", str(ROOT / "pyproject.toml"),
         *extra, str(path)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )


# T-M0-06
def test_banned_time_and_randomness_outside_adapters(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import random\nimport time\nimport uuid\nfrom datetime import datetime\n\n"
        "a = datetime.now()\nb = uuid.uuid4()\nc = time.monotonic_ns()\nd = random.random()\n",
        encoding="utf-8",
    )
    result = _ruff(offender, "--select", "TID251")
    assert result.returncode != 0
    for banned in ("datetime.datetime.now", "uuid.uuid4", "time.monotonic_ns", "random"):
        assert banned in result.stdout, banned


@pytest.mark.parametrize(("code", "banned"), [
    ("from datetime import datetime\nx = datetime.utcnow()\n", "datetime.datetime.utcnow"),
    ("from datetime import date\nx = date.today()\n", "datetime.date.today"),
    ("import time\nx = time.time()\n", "time.time"),
    ("import time\nx = time.time_ns()\n", "time.time_ns"),
    ("import time\nx = time.monotonic()\n", "time.monotonic"),
    ("import time\nx = time.perf_counter()\n", "time.perf_counter"),
    ("import uuid\nx = uuid.uuid1()\n", "uuid.uuid1"),
    ("import secrets\nx = secrets.token_hex(8)\n", "secrets"),
    ("from secrets import token_bytes\nx = token_bytes(8)\n", "secrets"),
])
def test_each_banned_api_fires_with_the_full_config(tmp_path: Path, code: str, banned: str) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(code, encoding="utf-8")
    result = _ruff(offender)
    assert result.returncode != 0, code
    assert "TID251" in result.stdout
    assert banned in result.stdout


def test_system_adapters_are_exempt_under_the_full_config() -> None:
    """Con TODAS las reglas (no solo TID251): los adaptadores no necesitan más excepciones."""
    for name in ("system_clock.py", "system_ids.py"):
        result = _ruff(ROOT / "agent_core" / "adapters" / name)
        assert result.returncode == 0, result.stdout


def test_exemption_is_limited_to_the_two_adapters(tmp_path: Path) -> None:
    """Copiar un adaptador a otra ruta debe hacer fallar el lint: la excepción es por archivo."""
    for name in ("system_clock.py", "system_ids.py"):
        copy = tmp_path / f"otro_{name}"
        source = (ROOT / "agent_core" / "adapters" / name).read_text(encoding="utf-8")
        copy.write_text(source, encoding="utf-8")
        assert _ruff(copy, "--select", "TID251").returncode != 0, name


def test_repo_import_contracts_pass() -> None:
    result = subprocess.run([sys.executable, "-c", _LINT_IMPORTS], capture_output=True, text=True,
                            cwd=ROOT, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_m0_boundary_contract_fires_on_a_violation(tmp_path: Path) -> None:
    """Réplica del `.importlinter` real sobre un paquete de juguete donde `domain` importa `flows`."""
    config = (ROOT / ".importlinter").read_text(encoding="utf-8")
    modules = set(re.findall(r"agent_core\.(\w+)", config))
    pkg = tmp_path / "fixpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in modules:
        (pkg / name).mkdir()
        (pkg / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "fix.ini").write_text(config.replace("agent_core", "fixpkg"), encoding="utf-8")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", _LINT_IMPORTS.replace("lint_imports_command()", "")
             + "sys.argv = ['lint-imports', '--config', 'fix.ini']; lint_imports_command()"],
            capture_output=True, text=True, cwd=tmp_path, check=False,
        )

    clean = run()
    assert clean.returncode == 0, clean.stdout + clean.stderr

    (pkg / "domain" / "bad.py").write_text("from fixpkg import flows  # noqa: F401\n", encoding="utf-8")
    (pkg / "ports" / "bad.py").write_text("from fixpkg import adapters  # noqa: F401\n", encoding="utf-8")
    broken = run()
    assert broken.returncode != 0, broken.stdout + broken.stderr
    assert "BROKEN" in broken.stdout


def _contracts() -> dict[str, dict[str, list[str]]]:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    result: dict[str, dict[str, list[str]]] = {}
    for section in parser.sections():
        if section.startswith("importlinter:contract:"):
            result[section] = {
                key: parser[section][key].split() for key in ("source_modules", "forbidden_modules")
                if key in parser[section]
            }
    return result


# `cli` y `contracts` son raíces de composición/herramienta: pueden importarlo todo.
_UNCONTRACTED = {"cli", "contracts"}


def test_every_subpackage_is_a_contract_source() -> None:
    """Cierra el hueco de la lista negra: un paquete nuevo sin contrato hace fallar esta prueba."""
    sources = {m for c in _contracts().values() for m in c.get("source_modules", [])}
    for info in pkgutil.iter_modules(agent_core.__path__):
        if info.name in _UNCONTRACTED:
            continue
        name = f"agent_core.{info.name}"
        assert name in sources, f"{name} no tiene contrato en .importlinter"


def test_every_module_contract_forbids_adapters_cli_and_contracts() -> None:
    for name, contract in _contracts().items():
        if name.endswith((":domain_sin_ports", ":adapters", ":telemetry")):  # ya prohíben cli y contracts
            continue
        forbidden = set(contract["forbidden_modules"])
        assert {"agent_core.adapters", "agent_core.cli", "agent_core.contracts"} <= forbidden, name


def test_domain_must_not_import_ports() -> None:
    contract = _contracts()["importlinter:contract:domain_sin_ports"]
    assert contract["source_modules"] == ["agent_core.domain"]
    assert contract["forbidden_modules"] == ["agent_core.ports"]


def test_module_cannot_import_adapters_nor_domain_ports(tmp_path: Path) -> None:
    config = (ROOT / ".importlinter").read_text(encoding="utf-8")
    modules = set(re.findall(r"agent_core\.(\w+)", config))
    pkg = tmp_path / "fixpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for name in modules:
        (pkg / name).mkdir()
        (pkg / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "fix.ini").write_text(config.replace("agent_core", "fixpkg"), encoding="utf-8")
    cmd = (_LINT_IMPORTS.replace("lint_imports_command()", "")
           + "sys.argv = ['lint-imports', '--config', 'fix.ini']; lint_imports_command()")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True, cwd=tmp_path,
                              check=False)

    assert run().returncode == 0
    (pkg / "flows" / "bad.py").write_text("from fixpkg import adapters  # noqa: F401\n", encoding="utf-8")
    assert run().returncode != 0
    (pkg / "flows" / "bad.py").unlink()
    (pkg / "domain" / "bad.py").write_text("from fixpkg import ports  # noqa: F401\n", encoding="utf-8")
    broken = run()
    assert broken.returncode != 0
    assert "BROKEN" in broken.stdout
