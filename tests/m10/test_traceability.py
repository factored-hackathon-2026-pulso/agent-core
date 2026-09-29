"""Cada prueba T-M10-NN del spec tiene al menos una prueba que la nombra."""

from pathlib import Path


def test_every_spec_test_id_has_a_test() -> None:
    here = Path(__file__).parent
    others = [p for p in here.glob("test_*.py") if p.name != Path(__file__).name]
    text = "".join(p.read_text(encoding="utf-8") for p in others).replace("t_m10_", "T-M10-")
    missing = [f"T-M10-{n:02d}" for n in range(1, 9) if f"T-M10-{n:02d}" not in text]
    assert missing == []
