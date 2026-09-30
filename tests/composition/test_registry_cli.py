import pytest

from agent_core.cli import main


def test_registry_help_lists_subcommands(capsys) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit) as info:
        main(["registry", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("import", "propose", "draft", "freeze", "evaluate", "approve", "publish", "promote", "revoke",
                "diff", "lineage", "export"):
        assert cmd in out
