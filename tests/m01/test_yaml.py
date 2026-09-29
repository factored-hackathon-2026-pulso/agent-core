from decimal import Decimal

import pytest

from agent_core.flows.yaml_loader import MAX_BYTES, MAX_DEPTH, MAX_NODES, YamlError, load_yaml


# T-M1-29
def test_yaml_12_booleans_and_keys_as_text() -> None:
    data = load_yaml("next: {yes: a, no: b, true: c, 1: d}\nflag: true\nword: yes\non: off\n")
    assert data == {
        "next": {"yes": "a", "no": "b", "true": "c", "1": "d"},
        "flag": True,
        "word": "yes",
        "on": "off",
    }


def test_numbers_are_int_or_decimal() -> None:
    data = load_yaml("a: 500.00\nb: 012\nc: 1e3\nd: -7\ne: .5\n")
    assert data == {"a": Decimal("500.00"), "b": 12, "c": Decimal("1e3"), "d": -7, "e": Decimal(".5")}
    assert isinstance(data, dict)
    assert not any(isinstance(v, float) for v in data.values())


def test_dates_versions_and_nan_stay_strings() -> None:
    data = load_yaml("d: 2026-09-28\nn: .nan\ni: .inf\nv: 1.0.0\nt: PT5M\n")
    assert data == {"d": "2026-09-28", "n": ".nan", "i": ".inf", "v": "1.0.0", "t": "PT5M"}


def test_nulls() -> None:
    assert load_yaml("a: null\nb: ~\nc:\n") == {"a": None, "b": None, "c": None}


@pytest.mark.parametrize(
    "text",
    [
        "a: 1\na: 2\n",
        "a: &x 1\nb: *x\n",
        "a: !!python/object/apply:os.system [echo]\n",
        "a: !!str 1\n",
        "a: 1\n---\nb: 2\n",
        "? [a]\n: 1\n",
        "a: [1, 2\n",
    ],
)
def test_rejected(text: str) -> None:
    with pytest.raises(YamlError):
        load_yaml(text)


def test_too_big() -> None:
    with pytest.raises(YamlError):
        load_yaml("a: " + "x" * MAX_BYTES)


# Hardening: the registry directory is untrusted input.
def test_size_cap_counts_bytes_not_characters() -> None:
    # 2-byte characters: fewer characters than MAX_BYTES but more bytes.
    with pytest.raises(YamlError):
        load_yaml("a: " + "\u00e9" * (MAX_BYTES // 2))
    with pytest.raises(YamlError):
        load_yaml(b"a: " + b"x" * MAX_BYTES)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[" * 10000 + "]" * 10000, id="flow-seq"),
        pytest.param("a: " + "[" * 10000 + "]" * 10000, id="flow-seq-in-map"),
        pytest.param("{a: " * 10000 + "1" + "}" * 10000, id="flow-map"),
        pytest.param(
            "".join("  " * i + "a:" + chr(10) for i in range(2000)) + "  " * 2000 + "1" + chr(10),
            id="block-map",
        ),
        pytest.param("- " * 5000 + "x" + chr(10), id="block-seq"),
    ],
)
def test_deep_nesting_is_rejected(text: str) -> None:
    with pytest.raises(YamlError):
        load_yaml(text)


def test_nesting_at_limit_is_accepted() -> None:
    depth = MAX_DEPTH
    assert load_yaml("[" * depth + "]" * depth) is not None
    with pytest.raises(YamlError):
        load_yaml("[" * (depth + 1) + "]" * (depth + 1))


def test_too_many_nodes() -> None:
    with pytest.raises(YamlError):
        load_yaml("[" + ",".join(["1"] * (MAX_NODES + 1)) + "]")


@pytest.mark.parametrize(
    "text",
    ["a: 1e999999999\n", "a: 1e-999999999\n", "a: " + "9" * 5000 + "\n", "a: 1." + "0" * 5000 + "\n"],
)
def test_huge_numbers_are_rejected(text: str) -> None:
    with pytest.raises(YamlError):
        load_yaml(text)


def test_invalid_utf8_is_yaml_error() -> None:
    with pytest.raises(YamlError):
        load_yaml(b"a: \xff\xfe\x00\n")
    with pytest.raises(YamlError):
        load_yaml(b"a: \xc3\x28\n")


def test_utf16_bytes_are_rejected() -> None:
    with pytest.raises(YamlError):
        load_yaml("a: 1\n".encode("utf-16"))


def test_utf8_bom_is_tolerated() -> None:
    assert load_yaml(b"\xef\xbb\xbfa: 1\n") == {"a": 1}


def test_control_characters_are_yaml_error() -> None:
    with pytest.raises(YamlError):
        load_yaml("a: \x00\n")


def test_empty_and_non_mapping_documents_are_returned_as_is() -> None:
    # The loader is generic; callers (M1 file checks) decide that a flow/agent file must be a mapping.
    assert load_yaml("") is None
    assert load_yaml("# only a comment\n") is None
    assert load_yaml("- a\n- b\n") == ["a", "b"]
    assert load_yaml("just text\n") == "just text"
    assert load_yaml("42\n") == 42


def test_merge_key_is_a_plain_key() -> None:
    assert load_yaml("base: {x: 1}\nb: {<<: {x: 1}}\n") == {"base": {"x": 1}, "b": {"<<": {"x": 1}}}


def test_lone_surrogate_str_is_yaml_error() -> None:
    with pytest.raises(YamlError):
        load_yaml("a: " + chr(0xD800))
