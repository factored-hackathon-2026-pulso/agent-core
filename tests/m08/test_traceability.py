"""Cada prueba T-M8-NN del spec tiene al menos una prueba que la nombra."""

from pathlib import Path


def test_every_spec_test_id_has_a_test() -> None:
    here = Path(__file__).parent
    others = [p for p in here.glob("test_*.py") if p.name != Path(__file__).name]
    text = "".join(p.read_text(encoding="utf-8") for p in others).lower()
    missing = [
        f"T-M8-{n:02d}" for n in range(1, 12) if f"t-m8-{n:02d}" not in text and f"t_m8_{n:02d}" not in text
    ]
    assert missing == []
