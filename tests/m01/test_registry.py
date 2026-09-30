import shutil
from pathlib import Path
from typing import Any

import pytest

from agent_core.domain import EntityKind, Flow, RefSpec, Template
from agent_core.flows import registry as registry_mod
from agent_core.flows.registry import load_registry
from agent_core.flows.schema import MAX_ERRORS
from agent_core.flows.validate import validate_flow, validate_registry
from agent_core.flows.violations import Violation
from agent_core.flows.yaml_loader import MAX_BYTES

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
TOOL_OK = "version: 1.0.0\nrisk_class: read\nmin_auth_level: session\nidempotent: true\n"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "reg"
    shutil.copytree(FIXTURE, root)
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _found(violations: list[Violation]) -> list[tuple[str, str | None]]:
    return [(v.rule, v.path) for v in violations]


# T-M1-15
def test_fixture_loads_clean_and_disputa_cargo_is_valid() -> None:
    reg, violations = load_registry(FIXTURE)
    assert violations == []
    flow = reg.resolve(EntityKind.flow, RefSpec.parse("disputa-cargo"))
    assert isinstance(flow, Flow)
    assert validate_flow(flow, reg) == []
    assert validate_registry(reg) == []


def test_template_reads_derived_from_text() -> None:
    reg, _ = load_registry(FIXTURE)
    tpl = reg.resolve(EntityKind.template, RefSpec.parse("t/pqr_radicado"))
    assert isinstance(tpl, Template)
    assert tpl.reads == frozenset({"facts.pqr_verificada.value.id"})
    assert reg.source(EntityKind.template, "t/pqr_radicado", "1.0.0") == "templates/t/pqr_radicado@1.0.0.yaml"


# T-M1-36
def test_declared_reads_must_match(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "templates/t/x@1.0.0.yaml",
           'id: t/x\nversion: 1.0.0\nlocales: {es: "{{ facts.a.value }}", pt: "{{ facts.a.value }}"}\n'
           "reads: []\n")
    _, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "templates/t/x@1.0.0.yaml")]


def test_malformed_template_variable(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "templates/t/x@1.0.0.yaml", 'id: t/x\nversion: 1.0.0\nlocales: {es: "{{ USD }}"}\n')
    _, violations = load_registry(root)
    assert [v.rule for v in violations] == ["G0-01"]


def test_prompt_with_variables_is_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "model_profiles/perfil@1.0.0.yaml",
           "id: perfil\nversion: 1.0.0\nendpoint_alias: demo\nmodel: m\ntemperature: 0\nmax_tokens: 100\n"
           "price: {input_per_mtok: 1, output_per_mtok: 2, source: sintetico, as_of: '2026-09-28'}\n")
    _write(root, "prompts/p/x@1.0.0.yaml",
           'id: p/x\nversion: 1.0.0\nlocales: {es: "Hola {{ slots.a }}"}\nmodel_profile: perfil@1\n')
    _, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "prompts/p/x@1.0.0.yaml")]


def test_filename_must_match_content(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/otro@1.0.0.yaml", "id: distinto\n" + TOOL_OK)
    reg, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "tools/otro@1.0.0.yaml")]
    assert reg.versions(EntityKind.tool, "distinto") == []


def test_unreadable_yaml_and_invalid_flow(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/roto@1.0.0.yaml", "id: roto\nversion: [1\n")
    _write(root, "flows/malo@1.0.0.yaml", "id: malo\nversion: 1.0.0\npriority: 1\nnodes:\n"
                                          "  - {id: k, type: inventado, config: {}}\n")
    _, violations = load_registry(root)
    assert sorted((v.rule, (v.path or "").split("#")[0]) for v in violations) == [
        ("G0-01", "flows/malo@1.0.0.yaml"), ("G0-01", "tools/roto@1.0.0.yaml")]


# Carga del directorio como entrada no confiable (M1 §3.11, §5)
def test_non_mapping_top_level_is_violation(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/lista@1.0.0.yaml", "- a\n- b\n")
    _, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "tools/lista@1.0.0.yaml")]


def test_bad_filenames_are_violations_and_do_not_abort(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/sinversion.yaml", "id: sinversion\n" + TOOL_OK)
    _write(root, "tools/con@1.0.0.yaml", "id: con\n" + TOOL_OK)  # nombre reservado de Windows en el id
    _write(root, "tools/x@1.0.0.txt", "id: x\n")  # otra extensión: se ignora
    _write(root, "tools/bueno@1.0.0.yaml", "id: bueno\n" + TOOL_OK)
    reg, violations = load_registry(root)
    paths = [v.path for v in violations]
    assert "tools/sinversion.yaml" in paths
    assert "tools/con@1.0.0.yaml" in paths
    assert all(p != "tools/x@1.0.0.txt" for p in paths)
    assert reg.versions(EntityKind.tool, "bueno") == ["1.0.0"]  # un archivo malo no aborta la carga
    assert reg.versions(EntityKind.tool, "buscar_transacciones") == ["1.0.0"]


def test_stray_colon_and_traversal_names_are_rejected(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    for name in ("a:x@1.0.0.yaml", "..@1.0.0.yaml", " a@1.0.0.yaml"):
        assert registry_mod.unsafe_name(name) is not None, name
    assert registry_mod.unsafe_name("ok@1.0.0.yaml") is None
    assert registry_mod.unsafe_name("NUL.txt") is not None
    assert registry_mod.unsafe_name("com1@1.0.0.yaml") is not None
    _, violations = load_registry(root)
    assert violations == []


def test_oversized_file_is_rejected_without_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _copy(tmp_path)
    path = root / "tools" / "grande@1.0.0.yaml"
    path.write_bytes(b"#" + b"a" * MAX_BYTES + b"\n")
    real_open = Path.open

    def guarded(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name.startswith("grande"):
            raise AssertionError("no debe abrirse un archivo sobre el tope")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    _, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "tools/grande@1.0.0.yaml")]
    assert "1 MiB" in violations[0].message


def test_non_flow_errors_are_bounded_and_deterministic(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    extra = "".join(f"k{i}: secreto-{i}\n" for i in range(600))
    _write(root, "tools/muchos@1.0.0.yaml", "id: muchos\nversion: 1.0.0\nrisk_class: read\n"
                                            "min_auth_level: session\nidempotent: true\n" + extra)
    _, first = load_registry(root)
    _, second = load_registry(root)
    assert first == second
    assert len(first) == MAX_ERRORS + 1
    assert sum("se omitieron 400 errores" in v.message for v in first) == 1
    assert all("secreto" not in v.message and "Extra inputs" not in v.message for v in first)


def test_pydantic_messages_are_spanish_and_do_not_echo_input(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/mala@1.0.0.yaml", "id: mala\nversion: 1.0.0\nrisk_class: valor-secreto-xyz\n")
    _write(root, "releases/mala.yaml", "id: mala\nagents: 7\nlanguage_detection: lang-es-pt@1\nextra: 1\n")
    _, violations = load_registry(root)
    assert len(violations) >= 3
    text = " ".join(v.message for v in violations)
    for english in ("Field required", "Extra inputs", "Input should", "valid "):
        assert english not in text
    assert "valor-secreto-xyz" not in text
    assert any(v.message == "campo obligatorio" for v in violations)
    assert any(v.message == "campo no permitido" for v in violations)
    assert all(v.message.startswith(("campo", "valor inválido")) for v in violations)


def test_error_path_escapes_slash_in_keys(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "tools/barra@1.0.0.yaml", "id: barra\nversion: 1.0.0\nrisk_class: read\n"
                                           "min_auth_level: session\nidempotent: true\n'a/b~c': 1\n")
    _, violations = load_registry(root)
    assert [v.path for v in violations] == ["tools/barra@1.0.0.yaml#/a~1b~0c"]


def test_entry_budget_counts_dirs_and_other_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reg"
    for i in range(8):
        (root / "tools" / f"d{i}").mkdir(parents=True)
        _write(root, f"policies/nota{i}.txt", "x")
    monkeypatch.setattr(registry_mod, "MAX_ENTRIES_TOTAL", 10)
    _, violations = load_registry(root)
    assert len(violations) == 1
    assert "entradas en total" in violations[0].message
    assert violations[0].path == "tools"


def test_symlinked_file_and_dir_are_not_followed(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "secreto@1.0.0.yaml").write_text("id: secreto\n" + TOOL_OK, encoding="utf-8")
    try:
        (root / "tools" / "enlace@1.0.0.yaml").symlink_to(outside / "secreto@1.0.0.yaml")
        (root / "policies" / "dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("los enlaces simbólicos no se pueden crear aquí")
    reg, violations = load_registry(root)
    assert sorted(_found(violations)) == [("G0-01", "policies/dir"), ("G0-01", "tools/enlace@1.0.0.yaml")]
    assert reg.versions(EntityKind.tool, "secreto") == []
    assert all(str(tmp_path) not in v.message and str(tmp_path) not in (v.path or "") for v in violations)


def test_symlinked_top_level_folder_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "x@1.0.0.yaml").write_text("id: x\n" + TOOL_OK, encoding="utf-8")
    root.mkdir()
    try:
        (root / "tools").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("los enlaces simbólicos no se pueden crear aquí")
    reg, violations = load_registry(root)
    assert _found(violations) == [("G0-01", "tools")]
    assert reg.all(EntityKind.tool) == []


def test_file_count_cap_reports_violation_and_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reg"
    for i in range(6):
        _write(root, f"tools/t{i}@1.0.0.yaml", f"id: t{i}\n" + TOOL_OK)
    monkeypatch.setattr(registry_mod, "MAX_FILES_PER_DIR", 3)
    reg, violations = load_registry(root)
    assert reg.all(EntityKind.tool) == []  # una carpeta desbordada no se carga
    assert _found(violations) == [("G0-01", "tools")]


def test_total_file_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reg"
    for i in range(3):
        _write(root, f"tools/t{i}@1.0.0.yaml", f"id: t{i}\n" + TOOL_OK)
        _write(root, f"policies/p{i}@1.0.0.yaml", f"id: p{i}\nversion: 1.0.0\n")
    monkeypatch.setattr(registry_mod, "MAX_FILES_TOTAL", 4)
    reg, violations = load_registry(root)
    assert sum("total" in v.message for v in violations) == 1
    assert len(reg.all(EntityKind.tool)) < 3


def test_long_names_are_clipped_in_messages(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    long = "a" * 100
    _write(root, f"tools/{long}@1.0.0.yaml", "id: b\n" + TOOL_OK)
    _, violations = load_registry(root)
    assert len(violations) == 1
    assert len(violations[0].message) < 400
    assert long not in violations[0].message


def test_release_problems_are_violations(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "releases/otra.yaml", "id: distinta\nagents: []\n")
    _write(root, "releases/lista.yaml", "- 1\n")
    _, violations = load_registry(root)
    assert sorted({(v.path or "").split("#")[0] for v in violations}) == [
        "releases/lista.yaml", "releases/otra.yaml"]


def test_violations_are_deterministic_and_use_forward_slashes(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "templates/t/z@1.0.0.yaml", "id: q\nversion: 1.0.0\nlocales: {es: a}\n")
    _write(root, "tools/a@1.0.0.yaml", "id: b\n" + TOOL_OK)
    first = load_registry(root)[1]
    second = load_registry(root)[1]
    assert first == second
    assert all("\\" not in (v.path or "") for v in first)
    assert [v.path for v in first] == sorted(v.path or "" for v in first)


def test_invalid_release_alias_is_a_spanish_g0_01_at_load_time(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    _write(root, "releases/demo.yaml",
           "id: demo\nagents:\n  - {agent: 'atencion@^1', aliases: [prod, 'PROD X']}\n"
           "flows: ['disputa-cargo@^1']\nlanguage_detection: 'lang-es-pt@1'\n")
    reg, violations = load_registry(root)
    assert [(v.rule, v.path, v.message) for v in violations] == [
        ("G0-01", "releases/demo.yaml#/agents/0/aliases",
         "alias de agente inválido (formato del selector de agente)"),
    ]
    assert reg.release("demo") is None
    assert "PROD X" not in violations[0].message
