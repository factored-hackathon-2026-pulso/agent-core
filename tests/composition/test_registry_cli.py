import argparse
import json
from pathlib import Path

import pytest

from agent_core.cli import main
from agent_core.composition.registry import add_registry_parser, run_registry_cli
from agent_core.registry import InMemoryRegistryStore, RegistryStore
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.registry_demo import demo_suite, issue
from tests.registry.helpers import AGENT, REGISTRY_DEMO, docs, prompt_draft


def test_registry_help_lists_subcommands(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as info:
        main(["registry", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("import", "propose", "draft", "freeze", "evaluate", "approve", "publish", "promote", "revoke",
                "diff", "lineage", "export"):
        assert cmd in out


# --- run_registry_cli / _dispatch (revisión final I5, I6a) ----------------------------------------------




class Cli:
    """Corre `agentcore registry …` sobre un almacén compartido, con el mundo demo."""

    def __init__(self, capsys: pytest.CaptureFixture[str],
                 store: RegistryStore | None = None) -> None:
        self.capsys, self.store = capsys, store or InMemoryRegistryStore()
        self.clock, self.ids = FakeClock(), FakeIds()

    def run(self, *argv: str, actor: str = "human", env: dict[str, str] | None = None,
            credential: str | None = None) -> tuple[int, str, str]:
        parser = argparse.ArgumentParser()
        add_registry_parser(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["registry", *argv])
        args.credential = credential if credential is not None else issue(actor)  # type: ignore[arg-type]
        environment = {"AGENTCORE_ALLOW_DEMO": "1"} if env is None else env
        code = run_registry_cli(args, clock=self.clock, ids=self.ids, env=environment.get, store=self.store)
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def ok(self, *argv: str, **kw: object) -> object:
        code, out, err = self.run(*argv, **kw)  # type: ignore[arg-type]
        assert code == 0, err
        return json.loads(out) if out.strip() != "ok" else None


def _write_draft(directory: Path, name: str, kind: str, content: dict[str, object]) -> None:
    body = {"kind": kind, "docs": docs().model_dump(mode="json"), "content": content}
    (directory / f"{name}.yaml").write_text(json.dumps(body), encoding="utf-8")  # JSON es YAML válido


def test_full_cycle_through_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    [seed] = cli.ok("import", str(REGISTRY_DEMO))  # type: ignore[misc]
    base = seed["release_id"]
    proposal = cli.ok("propose", AGENT, "Confirmación más clara")
    pid = proposal["proposal_id"]  # type: ignore[index]
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    _write_draft(drafts, "prompt", "prompt", prompt_draft().content)
    _write_draft(drafts, "suite", "eval_suite", demo_suite().model_dump(mode="json"))
    assert cli.ok("draft", pid, str(drafts), "--rev", "0")["rev"] == 1  # type: ignore[index]

    assert cli.ok("validate", pid)["violations"] == []  # type: ignore[index]
    frozen = cli.ok("freeze", pid)
    candidate_hash = frozen["candidate_hash"]  # type: ignore[index]
    shown = cli.ok("show", pid)
    assert shown["proposal"]["state"] == "candidate"  # type: ignore[index]
    assert cli.ok("evaluate", pid, "disputas-suite")["verdict"] == "pass"  # type: ignore[index]
    assert cli.ok("approve", pid, candidate_hash)["decision"] == "approved"  # type: ignore[index]
    published = cli.ok("publish", pid, "cli-key-1")
    new = published["release_id"]  # type: ignore[index]
    assert published["status"] == "active"  # type: ignore[index]
    assert cli.ok("publish", pid, "cli-key-1")["release_id"] == new  # type: ignore[index]  # idempotente

    diff = cli.ok("diff", base, new)
    assert any(c["after"]["id"] == "p/resumen_radicado" for c in diff["changed"])  # type: ignore[index]

    assert cli.ok("promote", AGENT, "prod", new)["after"] == new  # type: ignore[index]
    assert cli.ok("revoke", base, "reemplazada")["status"] == "revoked"  # type: ignore[index]

    out = tmp_path / "export"
    assert cli.ok("export", new, str(out)) is None
    exported = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.yaml"))
    assert "prompts/p/resumen_radicado@1.1.0.yaml" in exported
    assert any(name.startswith("releases/") for name in exported)


def test_reject_and_reopen_through_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    cli.ok("import", str(REGISTRY_DEMO))
    pid = cli.ok("propose", AGENT, "t")["proposal_id"]  # type: ignore[index]
    drafts = tmp_path / "d"
    drafts.mkdir()
    _write_draft(drafts, "prompt", "prompt", prompt_draft().content)
    _write_draft(drafts, "suite", "eval_suite", demo_suite().model_dump(mode="json"))
    cli.ok("draft", pid, str(drafts), "--rev", "0")
    cli.ok("freeze", pid)
    assert cli.ok("reopen", pid)["state"] == "draft"  # type: ignore[index]
    cli.ok("freeze", pid)
    cli.ok("evaluate", pid, "disputas-suite")
    assert cli.ok("reject", pid, "no me convence")["state"] == "draft"  # type: ignore[index]


def test_errors_are_reported_with_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    code, _, err = cli.run("show", "no-existe")
    assert code == 1 and json.loads(err)["code"] == "not_found"
    code, _, err = cli.run("revoke", "rel-x", "r", actor="bot")
    assert code == 1 and json.loads(err)["code"] == "forbidden_role"
    code, _, err = cli.run("lineage", "run-1")  # sin lector de runs (spec §7.5)
    assert code == 1 and json.loads(err)["code"] == "not_found"


def test_malformed_draft_file_is_a_typed_error_not_a_traceback(tmp_path: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    cli.ok("import", str(REGISTRY_DEMO))
    pid = cli.ok("propose", AGENT, "t")["proposal_id"]  # type: ignore[index]
    (tmp_path / "lista.yaml").write_text("- uno\n- dos\n", encoding="utf-8")
    code, _, err = cli.run("draft", pid, str(tmp_path / "lista.yaml"), "--rev", "0")
    assert code == 1 and json.loads(err)["code"] == "validation_failed"
    (tmp_path / "sin-docs.yaml").write_text(json.dumps({"kind": "prompt", "content": {}}), encoding="utf-8")
    code, _, err = cli.run("draft", pid, str(tmp_path / "sin-docs.yaml"), "--rev", "0")
    assert code == 1 and json.loads(err)["code"] == "validation_failed"


def test_missing_dsn_or_credential_is_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    parser = argparse.ArgumentParser()
    add_registry_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["registry", "show", "p"])
    code = run_registry_cli(args, clock=FakeClock(), ids=FakeIds(), env={}.get)
    assert code == 2 and "--dsn" in capsys.readouterr().err


def test_verifier_and_harness_are_required_without_allow_demo(capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    code, out, err = cli.run("show", "p", env={})
    assert code == 2 and out == "" and "AGENTCORE_ALLOW_DEMO=1" in err
    code, _, err = cli.run("show", "p", env={"AGENTCORE_ALLOW_DEMO": "0"})
    assert code == 2
    assert cli.store._state.proposals == {}  # type: ignore[attr-defined]  # nada se ejecutó


def test_explicit_verifier_and_harness_work_without_allow_demo(capsys: pytest.CaptureFixture[str]) -> None:
    cli = Cli(capsys)
    code, _, err = cli.run("--verifier", "testing.registry_demo:demo_verifier",
                           "--harness", "testing.registry_demo:build_harness", "show", "no-existe", env={})
    assert code == 1 and json.loads(err)["code"] == "not_found"


@pytest.mark.parametrize("flag", ["--verifier", "--harness"])
@pytest.mark.parametrize("path", ["modulo_que_no_existe:x", "testing.registry_demo:no_existe",
                                  "sin-dos-puntos"])
def test_unloadable_verifier_or_harness_is_exit_2_with_clear_message(
        capsys: pytest.CaptureFixture[str], flag: str, path: str) -> None:
    cli = Cli(capsys)
    other = ("--harness", "testing.registry_demo:build_harness") if flag == "--verifier" else \
        ("--verifier", "testing.registry_demo:demo_verifier")
    code, out, err = cli.run(flag, path, *other, "show", "p", env={})
    assert code == 2 and out == "" and "no se pudo cargar" in err and "Traceback" not in err
