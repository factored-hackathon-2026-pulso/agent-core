"""`registry-e2e` atiende en es y pt: cada texto trae las dos versiones y `pt` no es el español copiado."""

from pathlib import Path

import yaml

REGISTRY = Path(__file__).parents[1] / "fixtures" / "registry-e2e"


def _entities() -> list[tuple[str, dict[str, object]]]:
    return [(path.relative_to(REGISTRY).as_posix(), yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(REGISTRY.rglob("*.yaml"))]


def test_every_agent_supports_spanish_and_portuguese() -> None:
    agents = [(name, data) for name, data in _entities() if name.startswith("agents/")]
    assert agents
    for name, data in agents:
        assert data["supported_locales"] == ["es", "pt"], name


def test_every_template_and_prompt_has_a_translated_portuguese_text() -> None:
    texts = [(name, data["locales"]) for name, data in _entities() if "locales" in data]
    assert texts
    for name, locales in texts:
        assert set(locales) == {"es", "pt"}, name
        assert locales["pt"].strip() != locales["es"].strip(), f"{name}: el texto pt es el español copiado"
