"""Cada prueba T-M11-NN del spec existe en tests/m11.

T-M11-01 (replay `fixture` con los seis caminos del flow de demo) queda PENDIENTE de la Task 14: necesita
el motor integrado (M4/M2/M5/M6/M8) y los fixtures por camino. Se cubre allí, no aquí.
"""

from pathlib import Path

import pytest

REQUIRED = {f"T-M11-{n:02d}" for n in range(2, 11)}


def test_every_spec_test_id_is_referenced_by_a_test() -> None:
    others = [p for p in Path("tests/m11").glob("test_*.py") if p.name != Path(__file__).name]
    text = " ".join(p.read_text(encoding="utf-8") for p in others).lower()
    found = {i for i in REQUIRED if i.lower().replace("-", "_") in text or i.lower() in text}
    assert found == REQUIRED, sorted(REQUIRED - found)


@pytest.mark.skip(reason="Task 14: T-M11-01 necesita el motor integrado y los fixtures por camino")
def test_t_m11_01_replay_fixture_of_the_six_demo_paths() -> None:
    raise NotImplementedError
