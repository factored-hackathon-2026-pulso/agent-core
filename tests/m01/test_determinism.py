from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_core.flows.validate import validate_flow
from agent_core.flows.violations import Violation
from tests.m01.cases import base, flow, registry

IDS = [n["id"] for n in base()["nodes"]] + ["fantasma"]
KEYS = ["ok", "error", "yes", "no", "next", "verified", "failed", "uncertain", "denied", "x"]
EDIT = st.tuples(st.integers(0, 8), st.sampled_from(KEYS), st.one_of(st.none(), st.sampled_from(IDS)))


def _strip(violations: list[Violation]) -> list[tuple[str, str | None, str | None, str]]:
    return sorted((v.rule, v.flow, v.node_id, v.message) for v in violations)


# T-M1-41
@settings(max_examples=200, deadline=None)
@given(st.lists(EDIT, max_size=12), st.randoms(use_true_random=False))
def test_validate_is_total_sorted_and_order_independent(
    edits: list[tuple[int, str, str | None]], rnd: Any
) -> None:
    d = base()
    for index, key, target in edits:
        nxt = d["nodes"][index].setdefault("next", {})
        if target is None:
            nxt.pop(key, None)
        else:
            nxt[key] = target
    reg = registry()
    first = validate_flow(flow(d), reg)
    assert first == sorted(first, key=Violation.sort_key)
    rest = d["nodes"][1:]
    rnd.shuffle(rest)
    second = validate_flow(flow({**d, "nodes": [d["nodes"][0], *rest]}), reg)
    assert _strip(first) == _strip(second)
