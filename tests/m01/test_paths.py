import pytest

from agent_core.flows.paths import bad_paths, parse_path, template_vars, value_paths
from agent_core.flows.violations import FlowSchemaError, Violation, sort_violations


@pytest.mark.parametrize(
    ("text", "ns", "name", "rest"),
    [
        ("slots.desc", "slots", "desc", ()),
        ("facts.datos", "facts", "datos", ()),
        ("facts.datos.value", "facts", "datos", ("value",)),
        ("facts.tx.value.transaction_id", "facts", "tx", ("value", "transaction_id")),
        ("decisions.d.campo", "decisions", "d", ("campo",)),
        ("readback", "readback", None, ()),
        ("readback.status", "readback", None, ("status",)),
    ],
)
def test_parse_path_valid(text: str, ns: str, name: str | None, rest: tuple[str, ...]) -> None:
    path = parse_path(text)
    assert path is not None
    assert (path.ns, path.name, path.rest, path.raw) == (ns, name, rest, text)


@pytest.mark.parametrize("text", ["USD", "hola", "", "slotsx", "factsy.a"])
def test_literals_are_not_paths(text: str) -> None:
    assert parse_path(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "slots.a.b",
        "facts.X.value",
        "facts.a.valor",
        "decisions.d",
        "slots.",
        "readback.",
        "slots.a\n",
    ],
)
def test_malformed_paths_raise(text: str) -> None:
    with pytest.raises(ValueError):
        parse_path(text)


def test_whole_fact() -> None:
    whole, with_value = parse_path("facts.a"), parse_path("facts.a.value")
    assert whole is not None and whole.whole_fact
    assert with_value is not None and not with_value.whole_fact


def test_value_paths_recursive() -> None:
    args = {"a": "slots.x", "b": ["facts.y.value", {"c": "decisions.d.campo"}], "d": "USD", "e": 3}
    assert [p.raw for p in value_paths(args)] == ["slots.x", "facts.y.value", "decisions.d.campo"]


def test_value_paths_strict_and_bad_paths() -> None:
    args = {"a": "slots.x.y", "b": "slots.ok"}
    with pytest.raises(ValueError):
        value_paths(args)
    assert [p.raw for p in value_paths(args, strict=False)] == ["slots.ok"]
    assert bad_paths(args) == ["slots.x.y"]


def test_value_paths_survive_deep_nesting() -> None:
    deep: object = "slots.x"
    for _ in range(50_000):
        deep = [deep]
    assert [p.raw for p in value_paths(deep)] == ["slots.x"]  # type: ignore[arg-type]


def test_template_vars() -> None:
    text = "Radicado {{ facts.pqr.value.id }} para {{slots.nombre}}. {{ facts.pqr.value.id }}"
    assert template_vars(text) == frozenset({"facts.pqr.value.id", "slots.nombre"})
    assert template_vars("sin variables") == frozenset()


@pytest.mark.parametrize(
    "text", ["{{ }}", "{{ USD }}", "a {{ facts.x.value", "a }} b", "{{ slots.a.b }}", "{{ {x} }}"]
)
def test_template_vars_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        template_vars(text)


@pytest.mark.parametrize(
    "hostile",
    ["{{" + " " * 200_000, "{{ " * 50_000, "{{" + "a" * 200_000, "facts.a" + ".value" * 50_000 + "!"],
    ids=["spaces", "open-braces", "long-body", "long-path"],
)
def test_hostile_input_is_linear(hostile: str) -> None:
    # Una regex con retroceso catastrófico no terminaría: la prueba colgaría en vez de fallar rápido.
    with pytest.raises(ValueError):
        template_vars(hostile) if hostile.startswith("{{") else parse_path(hostile)


def test_violation_order_and_dedup() -> None:
    a = Violation(rule="G0-05", flow="f@1.0.0", node_id="b", message="x")
    b = Violation(rule="G0-03", flow="f@1.0.0", node_id="z", message="y")
    c = Violation(rule="G0-05", flow="f@1.0.0", node_id="a", message="x")
    assert sort_violations([a, b, c, a]) == [b, c, a]


def test_flow_schema_error_carries_sorted_violations() -> None:
    err = FlowSchemaError([Violation(rule="G0-09", message="b"), Violation(rule="G0-01", message="a")])
    assert [v.rule for v in err.violations] == ["G0-01", "G0-09"]
    assert "G0-01" in str(err)
