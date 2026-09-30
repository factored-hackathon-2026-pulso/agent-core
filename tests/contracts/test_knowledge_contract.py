"""Contrato de `KnowledgeSource` (ADR 0015, m12 §2): lo que toda implementación debe cumplir, la del servicio de
la unidad 7 incluida. T-M12-06: `FileKnowledgeSource` sobre un árbol `knowledge/` pasa la suite.

Dos reglas de seguridad:

- el servicio filtra con la vista: una página fuera de ella no se devuelve (ni se lista), y no se distingue
  de una ausente;
- el snapshot es parte de la identidad: una página de otro snapshot no se devuelve.

Cada regla tiene su sanidad negativa: un doble permisivo debe hacer fallar el chequeo."""

from pathlib import Path

import pytest

from agent_core.domain import KnowledgeView, PageRecord
from agent_core.ports import KnowledgeSource
from testing.fakes.knowledge import FileKnowledgeSource, InMemoryKnowledgeSource
from tests.m12.helpers import LeakySource, standard_records

SNAPSHOT = "kb-base@1.0.0"
TREE = Path(__file__).parent.parent / "fixtures" / "knowledge"

CUSTOMER = KnowledgeView(audiences=frozenset({"public"}), approved_only=True)
ADVISOR = KnowledgeView(audiences=frozenset({"public", "internal"}), approved_only=False)
EVERYTHING = KnowledgeView(audiences=frozenset({"public", "internal", "agent_only"}), approved_only=False)
NOTHING = KnowledgeView(audiences=frozenset(), approved_only=False)


def check_declares_its_capabilities(source: KnowledgeSource) -> None:
    caps = source.capabilities()
    assert isinstance(caps, frozenset) and {"read", "index"} <= caps


def check_reads_a_visible_page(source: KnowledgeSource) -> None:
    record = source.read("faq/cargos.md", SNAPSHOT, CUSTOMER)
    assert isinstance(record, PageRecord)
    assert record.meta.path == "faq/cargos.md" and record.meta.snapshot == SNAPSHOT
    assert record.meta.audience == "public" and record.meta.status == "approved"
    assert "Plazo de respuesta" in record.content


def check_a_missing_page_is_none(source: KnowledgeSource) -> None:
    assert source.read("faq/no-existe.md", SNAPSHOT, EVERYTHING) is None


def check_another_snapshot_is_none(source: KnowledgeSource) -> None:
    assert source.read("faq/cargos.md", "kb-otro@9.9.9", EVERYTHING) is None


def check_the_view_filters_audience_and_status(source: KnowledgeSource) -> None:
    assert source.read("proc/reversos.md", SNAPSHOT, CUSTOMER) is None
    assert source.read("guia/agente.md", SNAPSHOT, CUSTOMER) is None
    assert source.read("faq/borrador.md", SNAPSHOT, CUSTOMER) is None
    assert source.read("proc/reversos.md", SNAPSHOT, ADVISOR) is not None
    assert source.read("guia/agente.md", SNAPSHOT, ADVISOR) is None
    assert source.read("guia/agente.md", SNAPSHOT, EVERYTHING) is not None
    assert source.read("faq/borrador.md", SNAPSHOT, EVERYTHING) is not None
    assert source.read("faq/cargos.md", SNAPSHOT, NOTHING) is None


def check_index_lists_only_visible_pages_in_a_stable_order(source: KnowledgeSource) -> None:
    customer = [m.path for m in source.index(SNAPSHOT, CUSTOMER)]
    everything = [m.path for m in source.index(SNAPSHOT, EVERYTHING)]
    assert customer == sorted(customer) and everything == sorted(everything)
    assert "faq/cargos.md" in customer and "proc/reversos.md" not in customer
    assert "guia/agente.md" not in customer and "faq/borrador.md" not in customer
    assert {"proc/reversos.md", "guia/agente.md", "faq/borrador.md"} <= set(everything)
    assert source.index("kb-otro@9.9.9", EVERYTHING) == []
    assert all(m.snapshot == SNAPSHOT for m in source.index(SNAPSHOT, EVERYTHING))


def check_translations_are_linked_in_the_index(source: KnowledgeSource) -> None:
    by_path = {m.path: m for m in source.index(SNAPSHOT, CUSTOMER)}
    assert by_path["faq/cargos.pt.md"].translation_of == "faq/cargos.md"
    assert by_path["faq/cargos.pt.md"].lang == "pt" and by_path["faq/cargos.md"].lang == "es"


def check_paths_that_escape_the_snapshot_are_none(source: KnowledgeSource) -> None:
    for path in ("../kb-base@1.0.0/faq/cargos.md", "/etc/passwd", "faq/../faq/cargos.md", "faq//cargos.md", ""):
        assert source.read(path, SNAPSHOT, EVERYTHING) is None, path


def check_the_full_content_is_never_shown(source: KnowledgeSource) -> None:
    record = source.read("faq/cargos.md", SNAPSHOT, CUSTOMER)
    assert record is not None
    assert "Plazo de respuesta" not in repr(record) and "Plazo de respuesta" not in record.model_dump_json()


def check_reading_is_deterministic(source: KnowledgeSource) -> None:
    first = source.read("faq/cargos.md", SNAPSHOT, CUSTOMER)
    assert first == source.read("faq/cargos.md", SNAPSHOT, CUSTOMER)
    assert source.index(SNAPSHOT, ADVISOR) == source.index(SNAPSHOT, ADVISOR)


CHECKS = [
    check_declares_its_capabilities, check_reads_a_visible_page, check_a_missing_page_is_none,
    check_another_snapshot_is_none, check_the_view_filters_audience_and_status,
    check_index_lists_only_visible_pages_in_a_stable_order, check_translations_are_linked_in_the_index,
    check_paths_that_escape_the_snapshot_are_none, check_the_full_content_is_never_shown,
    check_reading_is_deterministic,
]


@pytest.fixture(params=["file", "memory"])
def source(request: pytest.FixtureRequest) -> KnowledgeSource:
    if request.param == "file":
        return FileKnowledgeSource(TREE)
    return InMemoryKnowledgeSource(standard_records())


@pytest.mark.parametrize("check", CHECKS, ids=lambda fn: fn.__name__)
def test_the_real_and_the_in_memory_doubles_pass_the_contract(check, source: KnowledgeSource) -> None:  # type: ignore[no-untyped-def]
    check(source)


# T-M12-06
def test_t_m12_06_file_knowledge_source_over_the_knowledge_tree_passes_the_contract() -> None:
    src = FileKnowledgeSource(TREE)
    for check in CHECKS:
        check(src)


# --- sanidad negativa: un doble permisivo debe hacer fallar el chequeo ------------------------------------------


def test_a_leaky_source_fails_the_view_check() -> None:
    leaky = LeakySource(standard_records())
    with pytest.raises(AssertionError):
        check_the_view_filters_audience_and_status(leaky)
    with pytest.raises(AssertionError):
        check_index_lists_only_visible_pages_in_a_stable_order(leaky)


def test_a_source_that_ignores_the_snapshot_fails_the_snapshot_check() -> None:
    class AnySnapshot(InMemoryKnowledgeSource):
        def read(self, path, snapshot, view):  # type: ignore[no-untyped-def]
            return super().read(path, SNAPSHOT, view)

    with pytest.raises(AssertionError):
        check_another_snapshot_is_none(AnySnapshot(standard_records()))


# --- detalles propios del adaptador de archivos -------------------------------------------------------------------


def test_file_source_reads_the_frontmatter_as_page_metadata() -> None:
    src = FileKnowledgeSource(TREE)
    record = src.read("faq/externa.md", SNAPSHOT, CUSTOMER)
    assert record is not None
    assert record.meta.source_refs == ["https://ejemplo.test/articulo-sintetico"]
    assert record.meta.type == "faq" and record.meta.approved_by == "revisor-demo"
    expired = src.read("faq/vencida.md", SNAPSHOT, CUSTOMER)
    assert expired is not None and str(expired.meta.valid_to) == "2026-06-30"  # la vigencia la juzga M12


def test_file_source_ignores_a_page_with_a_broken_frontmatter(tmp_path: Path) -> None:
    root = tmp_path / "kb-base@1.0.0"
    root.mkdir(parents=True)
    (root / "rota.md").write_text("---\ntype: faq\naudience: pública\n---\ntexto\n", encoding="utf-8")
    (root / "sin-frontmatter.md").write_text("solo texto\n", encoding="utf-8")
    src = FileKnowledgeSource(tmp_path)
    assert src.read("rota.md", SNAPSHOT, EVERYTHING) is None  # falla cerrado: sin metadatos válidos no se sirve
    assert src.read("sin-frontmatter.md", SNAPSHOT, EVERYTHING) is None
    assert src.index(SNAPSHOT, EVERYTHING) == []


def test_file_source_does_not_follow_symlinks_out_of_the_tree(tmp_path: Path) -> None:
    outside = tmp_path / "fuera.md"
    outside.write_text("---\ntype: faq\naudience: public\nstatus: draft\nlang: es\n---\nsecreto\n", encoding="utf-8")
    root = tmp_path / "tree" / "kb-base@1.0.0"
    root.mkdir(parents=True)
    try:
        (root / "enlace.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("el sistema no permite enlaces simbólicos")
    src = FileKnowledgeSource(tmp_path / "tree")
    assert src.read("enlace.md", SNAPSHOT, EVERYTHING) is None
