from typing import Any

from agent_core.flows.claims import derive_claims
from tests.m01.cases import base, flow, node, registry


def _claims(d: dict[str, Any]) -> dict[str, frozenset[str]]:
    return dict(derive_claims(flow(d), registry()))


def _respond(nid: str, template: str) -> dict[str, Any]:
    return {"id": nid, "type": "respond", "config": {"template_ref": template}, "next": {"next": "fin"}}


def test_base_claims() -> None:
    assert _claims(base()) == {"ok_msg": frozenset({"confirmar"})}


def test_derived_without_declaring() -> None:
    d = base()
    node(d, "ok_msg")["config"]["claims"] = []
    assert _claims(d)["ok_msg"] == frozenset({"confirmar"})


def test_safe_respond_has_empty_claims() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/seguro"))
    assert _claims(d)["aviso"] == frozenset()


def test_unrelated_fact_does_not_claim() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/lee_datos"))
    assert _claims(d)["aviso"] == frozenset()


def test_fallback_template_counts() -> None:
    d = base()
    d["nodes"].append({"id": "gen", "type": "respond",
                       "config": {"generate": {"prompt_ref": "p/gen", "fallback_template_ref": "t/lee_res"}},
                       "next": {"next": "fin"}})
    assert _claims(d)["gen"] == frozenset({"confirmar"})


def test_allowed_facts_count() -> None:
    d = base()
    d["nodes"].append({"id": "gen", "type": "respond",
                       "config": {"generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.res"],
                                               "fallback_template_ref": "t/seguro"}},
                       "next": {"next": "fin"}})
    assert _claims(d)["gen"] == frozenset({"confirmar"})


def test_read_tool_propagates() -> None:
    d = base()
    d["nodes"] += [
        {"id": "estado", "type": "tool",
         "config": {"tool": "leer@1", "args": {"id": "facts.res.value.id"}, "save_as": "estado"}},
        _respond("aviso", "t/lee_estado"),
    ]
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_decide_then_compute_propagates() -> None:
    d = base()
    d["nodes"] += [
        {"id": "d", "type": "decide",
         "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d",
                    "input_view": ["facts.res.value"]}},
        {"id": "calc_n", "type": "tool",
         "config": {"tool": "calc@1", "args": {"x": "decisions.d.campo"}, "save_as": "c"}},
        _respond("aviso", "t/lee_c"),
    ]
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_end_output_map_is_a_reader() -> None:
    d = base()
    d["nodes"].append({"id": "fin_map", "type": "end",
                       "config": {"outcome": "resolved", "output_map": {"r": "facts.verif.value.id"}}})
    assert _claims(d)["fin_map"] == frozenset({"confirmar"})


def test_unresolved_template_is_tolerated() -> None:
    d = base()
    d["nodes"].append(_respond("aviso", "t/noexiste"))
    assert _claims(d)["aviso"] == frozenset()


def test_unresolved_prompt_and_malformed_paths_are_tolerated() -> None:
    d = base()
    d["nodes"] += [
        {"id": "gen", "type": "respond",
         "config": {"generate": {"prompt_ref": "p/noexiste",
                                 "allowed_facts": ["facts.", "{{ x", "literal"],
                                 "fallback_template_ref": "t/noexiste"}},
         "next": {"next": "fin"}},
        {"id": "mal", "type": "tool",
         "config": {"tool": "leer@1", "args": {"id": "facts..bad", "y": "facts.res.value"},
                    "save_as": "mal"}},
        {"id": "fin_map", "type": "end",
         "config": {"outcome": "resolved", "output_map": {"r": "facts..x"}}},
    ]
    got = _claims(d)
    assert got["gen"] == frozenset()
    assert got["fin_map"] == frozenset()


def test_duplicate_ids_are_total_and_first_wins() -> None:
    d = base()
    d["nodes"] += [_respond("aviso", "t/lee_res"), _respond("aviso", "t/seguro")]
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_cycle_among_producers_terminates() -> None:
    d = base()
    d["nodes"] += [
        {"id": "a", "type": "tool",
         "config": {"tool": "leer@1", "args": {"x": "facts.b.value"}, "save_as": "a"}},
        {"id": "b", "type": "tool",
         "config": {"tool": "leer@1", "args": {"x": "facts.a.value", "y": "facts.res.value"},
                    "save_as": "b"}},
        _respond("aviso", "t/lee_datos"),
    ]
    d["nodes"][-1]["config"]["template_ref"] = "t/lee_res"
    assert _claims(d)["aviso"] == frozenset({"confirmar"})


def test_large_flow_terminates() -> None:
    d = base()
    for i in range(200):
        d["nodes"] += [
            {"id": f"c{i}", "type": "confirm",
             "config": {"action": {"tool": "escribir@1", "args": {}}, "summary_template": "t/resumen"},
             "next": {"yes": f"w{i}", "no": "fin_cancelado", "unclear": f"c{i}", "max_attempts": "esc"}},
            {"id": f"w{i}", "type": "tool", "config": {"action_from": f"c{i}", "save_as": f"w{i}"},
             "next": {"ok": "fin", "uncertain": "fin", "denied": "esc"}},
        ]
    for i in range(2000):
        src = "facts.w0.value" if i == 0 else f"facts.t{i - 1}.value"
        d["nodes"].append({"id": f"t{i}", "type": "tool",
                           "config": {"tool": "leer@1", "args": {"x": src}, "save_as": f"t{i}"}})
    d["nodes"].append({"id": "fin_map", "type": "end",
                       "config": {"outcome": "resolved", "output_map": {"r": "facts.t1999.value"}}})
    got = _claims(d)
    assert got["fin_map"] == frozenset({"c0"})
    assert got["ok_msg"] == frozenset({"confirmar"})
