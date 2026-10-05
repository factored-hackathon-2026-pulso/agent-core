"""G0-29: `capture_start` solo en los collect de entrada (y los que los siguen por ok)."""

from tests.m01.cases import base, check, rules


def test_capture_start_on_the_entry_collect_is_valid() -> None:
    d = base()
    d["nodes"][0]["config"]["capture_start"] = True
    assert "G0-29" not in rules(check(d))


def test_capture_start_in_the_middle_of_the_flow_is_refused() -> None:
    d = base()
    d["nodes"].append({"id": "otra", "type": "collect", "config": {"slot": "mas", "prompt_ref": "t/pedir",
                                                                   "capture_start": True},
                       "next": {"ok": "esc", "max_attempts": "esc"}})
    found = [v for v in check(d) if v.rule == "G0-29"]
    assert [v.node_id for v in found] == ["otra"]


def test_an_entry_without_the_mark_does_not_carry_it_to_the_next_collect() -> None:
    d = base()
    d["nodes"][0]["next"] = {"ok": "segundo", "max_attempts": "esc"}
    d["nodes"].insert(1, {"id": "segundo", "type": "collect",
                          "config": {"slot": "mas", "prompt_ref": "t/pedir", "capture_start": True},
                          "next": {"ok": "buscar", "max_attempts": "esc"}})
    assert {v.node_id for v in check(d) if v.rule == "G0-29"} == {"segundo"}


def test_an_extract_validator_needs_one_capture_group() -> None:
    d = base()
    d["nodes"][0]["config"]["validator"] = {"kind": "extract", "value": "agente: .*"}
    assert "G0-01" in rules(check(d))
    d["nodes"][0]["config"]["validator"] = {"kind": "extract", "value": "agente: (.*)"}
    assert "G0-01" not in rules(check(d))
