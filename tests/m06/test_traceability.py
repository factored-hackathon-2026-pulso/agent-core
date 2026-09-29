"""Trazabilidad T-M6-01…10 (M6 §7): cada caso del spec tiene al menos una prueba."""

import re
from pathlib import Path

SPEC_IDS = {f"T-M6-{n:02d}" for n in range(1, 11)}


def test_every_spec_case_is_covered_by_a_test() -> None:
    text = "\n".join(p.read_text(encoding="utf-8") for p in Path("tests/m06").glob("test_*.py")
                     if p.name != "test_traceability.py")
    covered = set(re.findall(r"T-M6-\d{2}", text)) | {
        f"T-M6-{int(n):02d}" for n in re.findall(r"test_t_m6_(\d{2})", text)
    }
    assert SPEC_IDS <= covered, sorted(SPEC_IDS - covered)
