"""`EngineRuntime` (m04 C1): tokeniza el texto del usuario, renderiza para el principal y sella el vault."""

from agent_core.domain import Message
from agent_core.views import TokenVault
from testing.builders import run_state
from tests.composition.world import EngineWorld

CEDULA = "1023456789"


def _open(w: EngineWorld, **over: object):  # type: ignore[no-untyped-def]
    state = run_state(release=w.release.id, agent="atencion@1.0.0", **over)  # type: ignore[arg-type]
    return state, w.runtimes.open(state, w.principal, None)


def test_model_text_cambia_la_pii_por_tokens_del_vault_del_run() -> None:
    w = EngineWorld()
    _, runtime = _open(w)
    out = runtime.model_text(f"mi cédula es {CEDULA}")
    assert CEDULA not in out and out.startswith("mi cédula es ⟦doc:1⟧")
    assert len(runtime.step.vault) == 1


def test_render_devuelve_el_valor_real_al_principal_autorizado() -> None:
    w = EngineWorld()
    _, runtime = _open(w)
    token = runtime.model_text(CEDULA)
    shown = runtime.render(Message(kind="generated", text=f"Tu documento {token}", locale="es"))
    assert shown.text == f"Tu documento {CEDULA}" and shown.kind == "generated" and shown.locale == "es"


def test_sealed_token_map_solo_cuando_el_turno_agrego_tokens() -> None:
    w = EngineWorld()
    _, runtime = _open(w)
    assert runtime.sealed_token_map() is None
    runtime.model_text(f"documento {CEDULA}")
    assert runtime.sealed_token_map() is not None


def test_el_vault_del_run_se_reabre_desde_el_token_map_guardado() -> None:
    w = EngineWorld()
    _, first = _open(w)
    token = first.model_text(CEDULA)
    blob = first.sealed_token_map()
    assert blob is not None
    _, second = _open(w, token_map=blob)
    assert isinstance(second.step.vault, TokenVault) and second.step.vault.resolve(token) == CEDULA
    assert second.sealed_token_map() is None  # nada nuevo que sellar
    assert second.model_text(f"otra vez {CEDULA}") == f"otra vez {token}"  # mismo valor, mismo token


def test_el_step_lleva_el_locale_y_los_puertos_reales_de_m5_y_m8() -> None:
    from agent_core.composition import DecisionAdapter, ResponderAdapter

    w = EngineWorld()
    _, runtime = _open(w, locale="pt")
    assert runtime.step.locale == "pt" and runtime.step.degraded is False
    assert isinstance(runtime.step.decisions, DecisionAdapter)
    assert isinstance(runtime.step.responder, ResponderAdapter)
    assert runtime.step.agent.id == "atencion" and runtime.step.release == w.release
