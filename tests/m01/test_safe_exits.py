from typing import Any

from agent_core.flows.violations import Violation
from tests.m01.cases import base, check, node, rules, task_base


def _respond(
    nid: str, template: str, nxt: str, claims: list[str] | None = None, wait: bool = False
) -> dict[str, Any]:
    config: dict[str, Any] = {"template_ref": template, "await": wait}
    if claims is not None:
        config["claims"] = claims
    return {"id": nid, "type": "respond", "config": config, "next": {"next": nxt}}


def _g06(d: dict[str, Any]) -> list[Any]:
    return [v for v in check(d) if v.rule == "G0-06"]


# T-M1-06
def test_failure_to_resolved_end() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin"
    assert rules(check(d)) == {"G0-06"}


def test_failure_to_abstained_end_is_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin_abst"
    d["nodes"].append({"id": "fin_abst", "type": "end", "config": {"outcome": "abstained"}})
    assert check(d) == []


# T-M1-21
def test_claiming_respond_on_failed_branch() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "esc", ["confirmar"]))
    assert {"G0-05", "G0-06"} <= rules(check(d))


def test_safe_respond_on_failed_branch() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "esc"))
    assert check(d) == []


def test_safe_chain_ending_in_confirm_is_not_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "confirmar"))
    assert rules(check(d)) == {"G0-06"}


def test_awaiting_safe_respond_back_to_collect() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "aclarar"
    d["nodes"].append(_respond("aclarar", "t/seguro", "pedir", wait=True))
    assert check(d) == []


# T-M1-24 (lógica de G0-06; el resto de reglas de modo llega en la Task 12)
def test_task_flow_failed_end_is_safe() -> None:
    assert check(task_base()) == []


# --- cada resultado de fallo: negativo y positivo -------------------------------------------------


def test_tool_each_failure_result_to_resolved_end() -> None:
    for result in ("error", "timeout", "denied"):
        d = base()
        node(d, "buscar")["next"][result] = "fin"
        found = _g06(d)
        assert [v.node_id for v in found] == ["buscar"], result
        assert found[0].path == f"/nodes/1/next/{result}"


def test_write_denied_to_success_path() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "ok_msg"
    assert [v.node_id for v in _g06(d)] == ["escribir"]


def test_write_denied_to_claiming_respond_is_unsafe() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "falso_exito"
    d["nodes"].append(_respond("falso_exito", "t/seguro", "fin", ["confirmar"]))
    assert {"G0-05", "G0-06"} <= rules(check(d))
    assert "falso_exito" in _g06(d)[0].message


def test_write_denied_to_node_that_reaches_claim_is_unsafe() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "puente"
    d["nodes"].append(_respond("puente", "t/seguro", "ok_msg"))
    assert [v.node_id for v in _g06(d)] == ["escribir"]


def test_write_denied_to_safe_respond_is_safe() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "lamento"
    d["nodes"].append(_respond("lamento", "t/seguro", "esc"))
    assert check(d) == []


def test_write_failure_looping_back_to_write_is_unsafe() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "escribir"
    assert "G0-06" in rules(check(d))


def test_verify_failed_to_verify_or_write_is_unsafe() -> None:
    for dst in ("verificar", "escribir"):
        d = base()
        node(d, "verificar")["next"]["failed"] = dst
        assert "G0-06" in rules(check(d)), dst


def test_verify_failed_to_success_end_is_unsafe() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "fin"
    assert "G0-06" in rules(check(d))


def test_collect_max_attempts() -> None:
    bad = base()
    node(bad, "pedir")["next"]["max_attempts"] = "fin"
    assert [v.node_id for v in _g06(bad)] == ["pedir"]
    good = base()
    node(good, "pedir")["next"]["max_attempts"] = "fin_abst"
    good["nodes"].append({"id": "fin_abst", "type": "end", "config": {"outcome": "clarify_exhausted"}})
    assert check(good) == []


def test_collect_max_attempts_to_itself_is_safe() -> None:
    d = base()
    node(d, "pedir")["next"]["max_attempts"] = "pedir"
    assert check(d) == []


def test_confirm_max_attempts() -> None:
    bad = base()
    node(bad, "confirmar")["next"]["max_attempts"] = "fin_cancelado"
    assert [v.node_id for v in _g06(bad)] == ["confirmar"]
    good = base()
    node(good, "confirmar")["next"]["max_attempts"] = "fin_abst"
    good["nodes"].append({"id": "fin_abst", "type": "end", "config": {"outcome": "abstained"}})
    assert check(good) == []


def test_confirm_max_attempts_to_a_write_is_unsafe() -> None:
    d = base()
    node(d, "confirmar")["next"]["max_attempts"] = "escribir"
    assert "G0-06" in rules(check(d))


def _with_decide(d: dict[str, Any], low: str) -> dict[str, Any]:
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append({"id": "elige", "type": "decide",
                       "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d"},
                       "next": {"a": "confirmar", "b": "confirmar", "low_confidence": low}})
    return d


def test_decide_low_confidence() -> None:
    assert check(_with_decide(base(), "esc")) == []
    for dst in ("fin", "confirmar", "escribir"):
        found = _g06(_with_decide(base(), dst))
        assert [v.node_id for v in found] == ["elige"], dst


def test_failure_into_decide_or_rule_is_unsafe() -> None:
    d = _with_decide(base(), "esc")
    node(d, "buscar")["next"]["error"] = "elige"
    assert [v.node_id for v in _g06(d)] == ["buscar"]
    r = base()
    r["nodes"].append({"id": "regla", "type": "rule",
                       "config": {"expr": {"==": [{"var": "facts.datos.value.id"}, "x"]}},
                       "next": {"true": "esc", "false": "esc"}})
    node(r, "buscar")["next"]["error"] = "regla"
    assert [v.node_id for v in _g06(r)] == ["buscar"]


def test_failure_into_respond_reading_write_facts_is_unsafe() -> None:
    # sin `claims` declarados: el reclamo se deriva de lo que lee la plantilla (falso éxito al cliente)
    d = base()
    node(d, "escribir")["next"]["denied"] = "falso_exito"
    d["nodes"].append(_respond("falso_exito", "t/hecho", "esc"))
    assert "G0-06" in rules(check(d))


def test_failure_into_respond_reading_read_facts_is_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "informa"
    d["nodes"].append(_respond("informa", "t/lee_datos", "esc"))
    assert check(d) == []


def test_safe_respond_cycle_is_safe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "r1"
    d["nodes"] += [_respond("r1", "t/seguro", "r2", wait=True), _respond("r2", "t/seguro", "r1", wait=True)]
    assert "G0-06" not in rules(check(d))


def test_chain_ending_in_success_end_is_unsafe() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "r1"
    d["nodes"] += [_respond("r1", "t/seguro", "r2"), _respond("r2", "t/seguro", "fin")]
    assert [v.node_id for v in _g06(d)] == ["buscar"]


def test_task_flow_failure_to_completed_end_is_unsafe() -> None:
    d = task_base()
    d["nodes"][0]["next"]["error"] = "fin_ok"
    assert [v.node_id for v in _g06(d)] == ["buscar"]


def test_failed_end_is_not_safe_in_conversational_flow() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin_fallo"
    d["nodes"].append({"id": "fin_fallo", "type": "end", "config": {"outcome": "failed"}})
    assert "G0-06" in rules(check(d))


# --- totalidad y sin ruido -------------------------------------------------------------------------


def test_missing_destination_is_not_reported_by_g0_06() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "no_existe"
    assert "G0-06" not in rules(check(d))


def test_missing_next_of_safe_respond_is_not_reported_by_g0_06() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "lamento"
    d["nodes"].append({"id": "lamento", "type": "respond", "config": {"template_ref": "t/seguro"}})
    assert "G0-06" not in rules(check(d))


def test_duplicate_ids_and_unresolved_tool_do_not_raise() -> None:
    d = base()
    d["nodes"].append(dict(node(d, "buscar")))
    node(d, "buscar")["config"]["tool"] = "no_existe@1"
    node(d, "buscar")["next"]["error"] = "fin"
    found = check(d)
    assert all(isinstance(v, Violation) for v in found)
    assert rules(found) == {"G0-01", "G0-02"}  # sin cascada de G0-05 ni G0-06


def test_many_failure_edges_into_one_long_chain() -> None:
    size = 2000
    d = base()
    for i in range(size):
        d["nodes"].append(_respond(f"c{i}", "t/seguro", f"c{i + 1}" if i + 1 < size else "esc"))
        d["nodes"].append({"id": f"t{i}", "type": "tool",
                           "config": {"tool": "leer@1", "args": {}, "save_as": "s"},
                           "next": {"ok": f"t{i + 1}" if i + 1 < size else "esc",
                                    "error": "c0", "timeout": "c0", "denied": "c0"}})
    node(d, "buscar")["next"]["ok"] = "t0"
    assert "G0-06" not in rules(check(d))
