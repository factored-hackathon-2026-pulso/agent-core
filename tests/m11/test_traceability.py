"""Cada prueba T-M11-NN del spec existe en tests/m11.

T-M11-01 (replay `fixture` con los seis caminos del flow de demo) corre con el motor compuesto en
`tests/composition/test_replay_fixtures.py`;
aquí solo se exige su forma con `StubEngine` (`test_replayer.py`).
"""

from pathlib import Path

REQUIRED = {f"T-M11-{n:02d}" for n in range(1, 11)}


def test_every_spec_test_id_is_referenced_by_a_test() -> None:
    others = [p for p in Path("tests/m11").glob("test_*.py") if p.name != Path(__file__).name]
    text = " ".join(p.read_text(encoding="utf-8") for p in others).lower()
    found = {i for i in REQUIRED if i.lower().replace("-", "_") in text or i.lower() in text}
    assert found == REQUIRED, sorted(REQUIRED - found)

