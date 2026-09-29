from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.m01.cases import base, check, node, rules

EDGES = [("buscar", "ok"), ("confirmar", "no"), ("verificar", "verified"), ("ok_msg", "next")]


def _insert_chains(d: dict[str, Any], chains: list[tuple[tuple[str, str], int]]) -> None:
    counter = 0
    for (source, result), length in chains:
        origin = node(d, source)
        target = origin["next"][result]
        ids = [f"s{counter + i}" for i in range(length)]
        counter += length
        for i, sid in enumerate(ids):
            nxt = ids[i + 1] if i + 1 < len(ids) else target
            d["nodes"].append({"id": sid, "type": "respond", "config": {"template_ref": "t/seguro"},
                               "next": {"next": nxt}})
        origin["next"][result] = ids[0]


# T-M1-26
@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.lists(st.tuples(st.sampled_from(EDGES), st.integers(1, 3)), max_size=4, unique_by=lambda t: t[0]))
def test_bypassing_verified_always_breaks_g0_05(chains: list[tuple[tuple[str, str], int]]) -> None:
    d = base()
    _insert_chains(d, chains)
    assert check(d) == []
    verify = node(d, "verificar")
    verify["next"]["failed"] = verify["next"]["verified"]
    assert "G0-05" in rules(check(d))
