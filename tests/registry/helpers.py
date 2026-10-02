"""Datos sintéticos compartidos por las pruebas del registry."""

from pathlib import Path
from typing import Any

from agent_core.domain import Principal
from agent_core.flows import PinnedRelease, load_registry, pin_release
from agent_core.registry.models import EntityDraft, VersionDocs
from testing.builders import NOW, principal

REGISTRY_DEMO = Path(__file__).parents[1] / "fixtures" / "registry-demo"
AGENT = "atencion"


def demo_pinned() -> PinnedRelease:
    reg, violations = load_registry(REGISTRY_DEMO)
    assert not violations
    return pin_release(reg, "demo")


def docs(text: str = "cambio de prueba") -> VersionDocs:
    return VersionDocs(description=text, rationale="mejorar la resolución", changelog=text)


STEP_UP = {"level": "step_up", "at": NOW}


def human(*roles: str, pid: str = "ana", level: str = "step_up") -> Principal:
    """Un supervisor por defecto: persona con `constructor` y `aprobador`, con autenticación reforzada."""
    return principal(type="builder", id=pid, roles=list(roles or ("constructor", "aprobador")),
                     attrs={"actor": "human"}, auth={"level": level, "at": NOW})


def admin(pid: str = "root") -> Principal:
    return human("constructor", "aprobador", "admin", pid=pid)


def bot(*roles: str) -> Principal:
    return principal(type="builder", id="constructor-bot", roles=list(roles or ("constructor",)), attrs={})


def prompt_draft(version: str = "1.1.0", text: str = "Confirma en una frase que la disputa quedó radicada.",
                 **over: Any) -> EntityDraft:
    content: dict[str, Any] = {
        "id": "p/resumen_radicado", "version": version,
        "locales": {"es": text, "pt": "Confirme em uma frase que a contestação foi registrada."},
        "model_profile": "perfil-generacion@1.0.0", **over}
    return EntityDraft(kind="prompt", content=content, docs=docs())


def suite_content(version: str = "1.0.0", **over: Any) -> dict[str, Any]:
    return {"id": "disputas-suite", "version": version, "agent_id": AGENT, "repetitions": 2,
            "scenarios": [{"id": "resuelto", "principal": {"id": "cust-001"},
                           "steps": [{"op": "start"}, {"op": "turn", "text": "no reconozco un cargo"},
                                     {"op": "confirm", "answer": "yes"}],
                           "expect": {"outcome": "resolved", "actions_verified": ["radicar_pqr"],
                                      "escalated": False}}], **over}


def suite_draft(version: str = "1.0.0", **over: Any) -> EntityDraft:
    return EntityDraft(kind="eval_suite", content=suite_content(version, **over), docs=docs("suite"))
