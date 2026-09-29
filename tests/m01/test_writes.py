from typing import Any

import pytest

from agent_core.flows import graph as graph_module
from agent_core.flows.context import Ctx
from agent_core.flows.rules.writes import g0_05
from agent_core.flows.violations import Violation
from tests.m01.cases import _tool, base, check, flow, node, registry, rules, task_base


def _respond(nid: str, template: str, nxt: str, claims: list[str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"template_ref": template}
    if claims is not None:
        config["claims"] = claims
    return {"id": nid, "type": "respond", "config": config, "next": {"next": nxt}}


def _rule(nid: str, true: str, false: str) -> dict[str, Any]:
    return {"id": nid, "type": "rule", "config": {"expr": {"!": [{"var": "slots.desc"}]}},
            "next": {"true": true, "false": false}}


def _block(idx: str, ok_dst: str, action_tool: str = "escribir@1") -> list[dict[str, Any]]:
    """confirm, escritura y verify encadenados; los ids llevan el sufijo `idx`."""
    return [
        {"id": f"confirmar{idx}", "type": "confirm",
         "config": {"action": {"tool": action_tool, "args": {"q": "slots.desc"}},
                    "summary_template": "t/resumen"},
         "next": {"yes": f"escribir{idx}", "no": "fin_cancelado", "unclear": f"confirmar{idx}",
                  "max_attempts": "esc"}},
        {"id": f"escribir{idx}", "type": "tool",
         "config": {"action_from": f"confirmar{idx}", "save_as": f"res{idx}"},
         "next": {"ok": f"verificar{idx}", "uncertain": f"verificar{idx}", "denied": "esc"}},
        {"id": f"verificar{idx}", "type": "verify",
         "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                    "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": f"verif{idx}"},
         "next": {"verified": ok_dst, "failed": "esc"}},
    ]


def _messages(d: dict[str, Any]) -> list[str]:
    return [v.message for v in check(d)]


# ---------------------------------------------------------------- positivos
def test_base_is_valid() -> None:
    assert check(base()) == []


def test_flow_without_writes_is_valid() -> None:
    assert check(task_base()) == []


def test_money_movement_and_irreversible_tools_are_valid_in_confirm() -> None:
    extra = [_tool("mover", "money_movement", readback_by="idempotency_key"),
             _tool("borrar", "write_irreversible", readback_by="idempotency_key")]
    for tool in ("mover@1", "borrar@1"):
        d = base()
        node(d, "confirmar")["config"]["action"]["tool"] = tool
        assert check(d, registry(*extra)) == []


def test_write_denied_back_to_confirm_is_ok_for_g0_05_but_not_a_safe_exit() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "confirmar"
    assert rules(check(d)) == {"G0-06"}  # M1 §3.7: confirm no es una salida segura


def test_verify_failed_back_to_confirm_is_ok_for_g0_05_but_not_a_safe_exit() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "confirmar"
    assert rules(check(d)) == {"G0-06"}  # M1 §3.7: confirm no es una salida segura


def test_loop_back_to_confirm_after_verified_is_valid() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "otra"
    d["nodes"].append(_rule("otra", "confirmar", "fin"))
    assert check(d) == []


def test_non_claiming_respond_on_unsafe_branches_is_valid() -> None:
    d = base()
    node(d, "confirmar")["next"]["no"] = "aviso_no"
    node(d, "buscar")["next"]["error"] = "aviso"
    d["nodes"].append(_respond("aviso_no", "t/seguro", "fin_cancelado"))
    d["nodes"].append(_respond("aviso", "t/seguro", "esc"))
    assert check(d) == []


def test_end_output_map_after_verified_is_valid() -> None:
    d = base()
    node(d, "fin")["config"] = {"outcome": "resolved", "output_map": {"id": "facts.verif.value.id"}}
    assert check(d) == []


# ---------------------------------------------------------------- brief
# T-M1-05 y T-M1-16 (escritura sin confirm)
def test_action_from_not_a_confirm() -> None:
    d = base()
    node(d, "escribir")["config"]["action_from"] = "buscar"
    assert rules(check(d)) == {"G0-05"}


def test_action_from_unknown_node() -> None:
    d = base()
    node(d, "escribir")["config"]["action_from"] = "nada"
    assert rules(check(d)) == {"G0-05"}


def test_action_from_itself() -> None:
    d = base()
    node(d, "escribir")["config"]["action_from"] = "escribir"
    assert rules(check(d)) == {"G0-05"}


# T-M1-16 (escritura sin verify)
def test_write_without_verify() -> None:
    d = base()
    node(d, "escribir")["next"]["ok"] = "ok_msg"
    assert rules(check(d)) == {"G0-05"}


def test_verify_must_use_idempotency_key() -> None:
    d = base()
    node(d, "verificar")["config"]["by"] = "fact:facts.datos.value.id"
    assert rules(check(d)) == {"G0-05"}


# T-M1-17
def test_declared_claim_before_verify() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/seguro", "confirmar", ["confirmar"]))
    assert rules(check(d)) == {"G0-05"}


# T-M1-18
def test_derived_claim_before_verify() -> None:
    d = base()
    node(d, "buscar")["next"]["ok"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/lee_res", "confirmar"))
    assert rules(check(d)) == {"G0-05"}


# T-M1-19
def test_compute_inherits_claim() -> None:
    d = base()
    node(d, "confirmar")["next"]["no"] = "derivar"
    d["nodes"] += [
        {"id": "derivar", "type": "tool",
         "config": {"tool": "calc@1", "args": {"x": "facts.res.value.monto"}, "save_as": "monto_calc"},
         "next": {"ok": "aviso", "error": "esc", "timeout": "esc", "denied": "esc"}},
        _respond("aviso", "t/lee_calc", "fin_cancelado"),
    ]
    assert rules(check(d)) == {"G0-05"}


# T-M1-20
def test_two_writes_claim_between_is_fine() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    d["nodes"] += [
        {"id": "confirmar2", "type": "confirm",
         "config": {"action": {"tool": "escribir@1", "args": {"q": "slots.desc"}},
                    "summary_template": "t/resumen"},
         "next": {"yes": "escribir2", "no": "fin_cancelado", "unclear": "confirmar2", "max_attempts": "esc"}},
        {"id": "escribir2", "type": "tool", "config": {"action_from": "confirmar2", "save_as": "res2"},
         "next": {"ok": "verificar2", "uncertain": "verificar2", "denied": "esc"}},
        {"id": "verificar2", "type": "verify",
         "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                    "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": "verif2"},
         "next": {"verified": "ok2", "failed": "esc"}},
        _respond("ok2", "t/hecho2", "fin", ["confirmar2"]),
    ]
    assert check(d) == []


# T-M1-31
def test_path_through_confirm_no_is_violation() -> None:
    d = base()
    node(d, "confirmar")["next"]["no"] = "r"
    d["nodes"].append(_rule("r", "escribir", "fin_cancelado"))
    assert rules(check(d)) == {"G0-05"}


def test_retry_write_from_failed_is_violation() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "escribir"
    assert "G0-05" in rules(check(d))


# T-M1-32
def test_plain_tool_node_with_write_tool() -> None:
    d = base()
    node(d, "buscar")["config"]["tool"] = "escribir@1"
    assert rules(check(d)) == {"G0-05"}


def test_readback_must_be_read() -> None:
    d = base()
    node(d, "verificar")["config"]["readback"] = "calc@1"
    assert rules(check(d)) == {"G0-05"}


def test_two_writes_for_one_confirm() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "escribir_b"
    d["nodes"].append({"id": "escribir_b", "type": "tool",
                       "config": {"action_from": "confirmar", "save_as": "res_b"},
                       "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}})
    assert "G0-05" in rules(check(d))


def test_confirm_with_read_tool() -> None:
    d = base()
    node(d, "confirmar")["config"]["action"]["tool"] = "leer@1"
    assert rules(check(d)) == {"G0-05"}


# T-M1-33
def test_ok_and_uncertain_to_different_nodes() -> None:
    d = base()
    node(d, "escribir")["next"]["uncertain"] = "esc"
    assert "G0-05" in rules(check(d))


def test_intermediate_node_before_verify() -> None:
    d = base()
    node(d, "escribir")["next"].update({"ok": "paso", "uncertain": "paso"})
    d["nodes"].append(_respond("paso", "t/seguro", "verificar"))
    assert "G0-05" in rules(check(d))


# T-M1-34
def test_loop_back_to_confirm_then_claim_on_no() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "otra"
    node(d, "confirmar")["next"]["no"] = "aviso"
    d["nodes"] += [
        _rule("otra", "confirmar", "fin"),
        _respond("aviso", "t/seguro", "fin_cancelado", ["confirmar"]),
    ]
    assert "G0-05" in rules(check(d))


def test_claim_on_confirm_without_write() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    node(d, "ok_msg")["config"]["claims"] = ["confirmar", "confirmar2"]
    d["nodes"].append(
        {"id": "confirmar2", "type": "confirm",
         "config": {"action": {"tool": "escribir@1", "args": {"q": "slots.desc"}},
                    "summary_template": "t/resumen"},
         "next": {"yes": "fin", "no": "fin_cancelado", "unclear": "confirmar2", "max_attempts": "esc"}})
    assert rules(check(d)) == {"G0-05"}


# ---------------------------------------------------------------- rutas que saltan el confirm
@pytest.mark.parametrize("edge", ["no", "unclear", "max_attempts"])
def test_bypass_through_every_non_yes_confirm_edge(edge: str) -> None:
    d = base()
    node(d, "confirmar")["next"][edge] = "escribir"
    assert "G0-05" in rules(check(d))


def test_bypass_from_a_read_tool_failure_edge() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "escribir"
    assert rules(check(d)) == {"G0-05", "G0-06"}


def test_bypass_via_loop_after_verified() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "r"
    d["nodes"].append(_rule("r", "escribir", "fin"))
    assert "G0-05" in rules(check(d))  # tambien G0-04: el ciclo no espera


def test_write_as_entry_node_skips_the_confirm() -> None:
    d = base()
    idx = next(i for i, n in enumerate(d["nodes"]) if n["id"] == "escribir")
    d["nodes"].insert(0, d["nodes"].pop(idx))
    assert "G0-05" in rules(check(d))


def test_second_entry_is_rejected() -> None:
    d = base()
    d["nodes"].append(_respond("segunda_entrada", "t/seguro", "escribir"))
    assert "G0-03" in rules(check(d))


def test_confirm_yes_leading_to_a_write_of_another_confirm() -> None:
    """El confirm C congela una acción y su `yes` ejecuta la escritura de otro confirm C2."""
    d = base()
    node(d, "confirmar")["next"].update({"yes": "escribir2", "no": "confirmar2"})
    d["nodes"] += _block("2", "ok_msg")
    assert "G0-05" in rules(check(d))


def test_write_reachable_directly_by_another_confirm_yes() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "confirmar2"
    d["nodes"] += _block("2", "ok_msg")
    node(d, "confirmar2")["next"]["yes"] = "escribir"
    assert "G0-05" in rules(check(d))


# ---------------------------------------------------------------- tools y verify
def test_plain_tool_node_with_irreversible_and_money_tools() -> None:
    extra = [_tool("mover", "money_movement", readback_by="idempotency_key"),
             _tool("borrar", "write_irreversible", readback_by="idempotency_key")]
    for tool in ("mover@1", "borrar@1"):
        d = base()
        node(d, "buscar")["config"]["tool"] = tool
        assert rules(check(d, registry(*extra))) == {"G0-05"}


def test_confirm_with_compute_tool() -> None:
    d = base()
    node(d, "confirmar")["config"]["action"]["tool"] = "calc@1"
    assert rules(check(d)) == {"G0-05"}


def test_readback_with_write_tool() -> None:
    d = base()
    node(d, "verificar")["config"]["readback"] = "escribir@1"
    assert rules(check(d)) == {"G0-05"}


def test_two_writes_sharing_one_verify() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    d["nodes"] += _block("2", "fin")[:2]
    node(d, "escribir2")["next"].update({"ok": "verificar", "uncertain": "verificar"})
    assert "G0-05" in rules(check(d))


def test_missing_tools_report_only_g0_02() -> None:
    for mutate in (
        lambda d: node(d, "confirmar")["config"]["action"].update(tool="fantasma@1"),
        lambda d: node(d, "verificar")["config"].update(readback="fantasma@1"),
        lambda d: node(d, "buscar")["config"].update(tool="fantasma@1"),
    ):
        d = base()
        mutate(d)
        assert rules(check(d)) == {"G0-02"}


# ---------------------------------------------------------------- reclamos
def test_claim_reachable_from_write_denied() -> None:
    d = base()
    node(d, "escribir")["next"]["denied"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/seguro", "fin", ["confirmar"]))
    assert rules(check(d)) == {"G0-05", "G0-06"}


def test_claim_reachable_from_verify_failed() -> None:
    d = base()
    node(d, "verificar")["next"]["failed"] = "pronto"
    d["nodes"].append(_respond("pronto", "t/lee_res", "fin"))
    assert rules(check(d)) == {"G0-05", "G0-06"}


def test_claim_via_uncertain_edge_skipping_verify() -> None:
    d = base()
    node(d, "escribir")["next"]["uncertain"] = "ok_msg"
    assert "G0-05" in rules(check(d))


def test_end_output_map_before_verify() -> None:
    d = base()
    node(d, "buscar")["next"]["error"] = "fin_x"
    d["nodes"].append({"id": "fin_x", "type": "end",
                       "config": {"outcome": "resolved", "output_map": {"id": "facts.verif.value.id"}}})
    assert rules(check(d)) == {"G0-05", "G0-06"}


def test_claim_of_unverifiable_write_is_reported() -> None:
    d = base()
    node(d, "escribir")["next"].update({"ok": "ok_msg", "uncertain": "ok_msg"})
    assert any("reclama" in m for m in _messages(d))


# ---------------------------------------------------------------- totalidad
def test_rule_is_total_on_malformed_pydantic_valid_flows() -> None:
    mutations = [
        lambda d: d["nodes"].append(dict(node(d, "escribir"))),  # id duplicado
        lambda d: node(d, "escribir")["config"].update(action_from="nada"),
        lambda d: node(d, "escribir")["next"].update(ok="nada", uncertain="nada"),
        lambda d: node(d, "confirmar")["config"]["action"].update(tool="fantasma@1"),
        lambda d: node(d, "verificar")["config"].update(readback="fantasma@1"),
        lambda d: node(d, "ok_msg")["config"].update(claims=["nada", "fin"]),
    ]
    expected = [{"G0-05"}, {"G0-05"}, {"G0-05"}, set(), set(), set()]
    for mutate, want in zip(mutations, expected, strict=True):
        d = base()
        mutate(d)
        found = list(g0_05(Ctx.build(flow(d), registry())))
        assert all(isinstance(v, Violation) for v in found)
        assert {v.rule for v in found} == want  # solo G0-05, nunca G0-06 ni otra regla


def test_messages_clip_author_strings() -> None:
    d = base()
    node(d, "escribir")["config"]["action_from"] = "x" * 500
    for violation in check(d):
        assert len(violation.message) < 300


# ---------------------------------------------------------------- tamaño
def test_large_flow_uses_a_bounded_number_of_reachability_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = 500
    nodes: list[dict[str, Any]] = [
        {"id": "pedir", "type": "collect", "config": {"slot": "desc", "prompt_ref": "t/pedir"},
         "next": {"ok": "confirmar0", "max_attempts": "esc"}},
        {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "fin_cancelado", "type": "end", "config": {"outcome": "cancelled"}},
        {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
    ]
    for i in range(blocks):
        after = f"confirmar{i + 1}" if i + 1 < blocks else "fin"
        nodes += _block(str(i), f"r{i}")
        nodes.append(_respond(f"r{i}", "t/seguro", after, [f"confirmar{i}"]))
    d = {"id": "grande", "version": "1.0.0", "priority": 10, "nodes": nodes}
    assert len(nodes) >= 2000
    calls = 0
    original = graph_module.FlowGraph.reachable

    def counting(self: Any, sources: Any, without: Any = frozenset()) -> set[str]:
        nonlocal calls
        calls += 1
        return original(self, sources, without)

    monkeypatch.setattr(graph_module.FlowGraph, "reachable", counting)
    assert check(d) == []
    assert calls <= 6 * blocks


# ------------------------------------------------ endurecimiento de 3.5.7: entradas del verify
def test_verify_reached_by_a_rule_branch_skipping_the_write() -> None:  # P1
    d = base()
    node(d, "buscar")["next"]["ok"] = "r"
    d["nodes"].append(_rule("r", "verificar", "confirmar"))
    violations = [v for v in check(d) if v.rule == "G0-05"]
    assert any(v.node_id == "r" and v.path is not None and v.path.endswith("/next/true") for v in violations)


def test_verify_as_entry_node() -> None:  # P2
    d = base()
    idx = next(i for i, n in enumerate(d["nodes"]) if n["id"] == "verificar")
    d["nodes"].insert(0, d["nodes"].pop(idx))
    assert any(v.rule == "G0-05" and "no puede ser el nodo de entrada" in v.message for v in check(d))


def test_write_denied_to_its_own_verify() -> None:  # P3
    d = base()
    node(d, "escribir")["next"]["denied"] = "verificar"
    violations = [v for v in check(d) if v.rule == "G0-05"]
    assert [(v.node_id, v.path) for v in violations] == [("escribir", "/nodes/3/next/denied")]


def test_loop_back_to_verify_through_a_collect_node() -> None:  # P15
    d = base()
    node(d, "confirmar")["next"]["no"] = "volver"
    d["nodes"].append({"id": "volver", "type": "collect",
                       "config": {"slot": "otro", "prompt_ref": "t/pedir"},
                       "next": {"ok": "verificar", "max_attempts": "esc"}})
    assert any(v.node_id == "volver" for v in check(d) if v.rule == "G0-05")


def test_shared_verify_is_reported_once_without_entry_noise() -> None:
    d = base()
    node(d, "ok_msg")["next"]["next"] = "confirmar2"
    d["nodes"] += _block("2", "fin")[:2]
    node(d, "escribir2")["next"].update({"ok": "verificar", "uncertain": "verificar"})
    shared = [m for m in _messages(d) if "comparten" in m]
    assert len(shared) == 1
    assert not any("sobra" in m for m in _messages(d))
