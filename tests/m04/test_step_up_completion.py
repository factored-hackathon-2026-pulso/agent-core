"""ADR 0010: un turno con una credencial elevada del mismo principal sube el nivel del principal del run.

El índice §5 asigna `principal` a M4; M9 ya validó firma y clave (`principal_mismatch`), así que aquí solo se
refresca `auth`, nunca la identidad.
"""

from datetime import timedelta

from agent_core.domain import AuthLevel
from testing.builders import NOW, principal
from tests.m04.harness import World
from tests.m04.helpers import cmd


def _with_auth(level: str, minutes: int = 0):  # type: ignore[no-untyped-def]
    base = principal()
    return base.model_copy(update={"auth": base.auth.model_copy(update={
        "level": AuthLevel(level), "at": NOW + timedelta(minutes=minutes), "simulated": level == "step_up"})})


def test_un_turno_elevado_actualiza_el_nivel_del_principal_del_run() -> None:
    w = World()
    w.open_run(active=True)
    assert w.saved().principal.auth.level is AuthLevel.session
    w.understand.push(cmd("continue"))
    w.principal = _with_auth("step_up", 5)
    w.turn("mi código es 000000")
    saved = w.saved().principal
    assert saved.auth.level is AuthLevel.step_up and saved.auth.simulated
    assert saved.key == principal().key


def test_solo_cambia_auth_no_la_identidad_ni_los_roles() -> None:
    w = World()
    w.open_run(active=True)
    original = w.saved().principal
    w.understand.push(cmd("continue"))
    presented = _with_auth("step_up", 5).model_copy(update={"roles": ["otro"], "scopes": ["admin"]})
    w.principal = presented
    w.turn("hola")
    saved = w.saved().principal
    assert (saved.roles, saved.scopes, saved.attrs) == (original.roles, original.scopes, original.attrs)


def test_un_principal_de_otra_clave_no_se_toma() -> None:
    """Defensa en profundidad: la puerta de M9 ya lo rechaza, pero M4 no debe cambiar de identidad."""
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.principal = replace_id(_with_auth("step_up", 5), "cust-999")
    w.turn("hola")
    assert w.saved().principal.id == "cust-001"
    assert w.saved().principal.auth.level is AuthLevel.session


def test_bajar_de_nivel_tambien_se_refleja() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"), cmd("continue"))
    w.principal = _with_auth("step_up", 5)
    w.turn("uno")
    w.principal = _with_auth("session", 10)
    w.turn("dos")
    assert w.saved().principal.auth.level is AuthLevel.session


def replace_id(p, new_id):  # type: ignore[no-untyped-def]
    return p.model_copy(update={"id": new_id})


def _step_up_tool_world() -> World:
    from tests.m02.harness import tool_def

    w = World()
    definition = tool_def("obtener_dato", "read", level="step_up")
    w.tools.register(definition, handler=lambda a: {"ok": True})
    return w


def test_el_step_up_se_completa_con_la_credencial_elevada_del_turno_siguiente() -> None:
    """ADR 0010 de punta a punta: pide step-up, y el turno con credencial elevada reintenta el nodo."""
    w = _step_up_tool_world()
    w.open_run()
    w.understand.push(cmd("start_flow", flow="procesar"), cmd("continue"))
    first = w.turn("consulta")
    assert first.awaiting == "step_up" and w.saved().active_flow is not None
    w.principal = _with_auth("step_up", 5)
    second = w.turn("mi código simulado")
    assert second.awaiting != "step_up" and second.status != "escalated"
    assert w.saved().principal.auth.level is AuthLevel.step_up
    assert "escalated" not in w.event_types()


def test_sin_elevar_el_step_up_se_repite_y_al_agotarse_escala() -> None:
    w = _step_up_tool_world()
    w.open_run()
    w.understand.push(cmd("start_flow", flow="procesar"), cmd("continue"), cmd("continue"))
    w.turn("consulta")
    w.turn("otra vez")
    last = w.turn("y otra")
    assert last.status == "escalated"
