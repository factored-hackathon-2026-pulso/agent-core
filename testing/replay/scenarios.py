"""Los seis caminos del flow de demo (M11 §3.5, T-M11-01) y la sesión con transferencia de la fase 7
(ADR 0021). Solo datos sintéticos.

Cada escenario guía un `EngineWorld` con puertos externos guionados; `record_scenario` lo graba."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from agent_core.audit import Fixture, build_fixture
from agent_core.domain import EngineEvent
from agent_core.ports import ToolStatus
from agent_core.turn import TurnTelemetry
from testing.engine_world import REGISTRY_DEMO, TRANSFER_DEMO, EngineWorld, transfer_world
from testing.fakes.tools import Scripted

TEXTO = "no reconozco un cargo de ciento veinte dólares en una tienda"
Scenario = Callable[[EngineWorld], None]


def _hasta_confirmar(w: EngineWorld, **over: str) -> None:
    w.start(**over)
    w.understands("continue")
    w.matches()
    w.turn(TEXTO, **over)


def resuelto(w: EngineWorld) -> None:
    """Disputa completa: confirma, radica, verifica y responde con `respond(generate)` de M8."""
    _hasta_confirmar(w)
    w.confirm("yes")


def cancelado(w: EngineWorld) -> None:
    """El usuario responde que no en la confirmación: el flow termina `cancelled`."""
    _hasta_confirmar(w)
    w.confirm("no")


def escalado_por_monto(w: EngineWorld) -> None:
    """La conversión da más de 500 USD: la política `escalamiento-disputa-monto` escala al equipo."""
    w.script_tool("convertir_moneda", Scripted(ToolStatus.ok, result=Decimal("900.00")))
    w.start()
    w.understands("continue")
    w.matches()
    w.turn(TEXTO)


def uncertain_verify(w: EngineWorld) -> None:
    """La escritura responde `uncertain` con efecto aplicado: `verify` lo confirma por lectura posterior."""
    w.script_tool("radicar_pqr", Scripted(ToolStatus.uncertain, result={"status": "Open", "id": "pqr-demo-1"},
                                          effect=True))
    _hasta_confirmar(w)
    w.confirm("yes")


def step_up(w: EngineWorld) -> None:
    """La escritura exige un nivel mayor al de la sesión: pide step-up y el turno elevado lo completa.

    El turno siguiente llega con una credencial `step_up` del mismo principal; M4 refresca `principal.auth`
    (ADR 0010) y el nodo `tool` se reintenta hasta radicar, verificar y resolver."""
    _hasta_confirmar(w, auth="session")
    w.confirm("yes", auth="session")
    w.understands("continue")
    w.turn("aquí está mi código simulado", auth="step_up")


def interrupcion(w: EngineWorld) -> None:
    """Una señal de fraude interrumpe el flow y escala con prioridad crítica."""
    w.start()
    w.understands("interrupt", interrupt="fraude")
    w.turn("creo que me están robando la tarjeta")


def transferencia(w: EngineWorld) -> None:
    """Recepción lee el directorio, elige `disputas` y le transfiere; `disputas` responde en el mismo
    turno (ADR 0021)."""
    w.start()
    w.understands("continue")
    w.routes("disputas")
    w.understands("start_flow", flow="disputa-cargo")
    w.turn(TEXTO)


SCENARIOS: dict[str, Scenario] = {
    "resuelto": resuelto,
    "cancelado": cancelado,
    "escalado_por_monto": escalado_por_monto,
    "uncertain_verify": uncertain_verify,
    "step_up": step_up,
    "interrupcion": interrupcion,
    "transferencia": transferencia,
}
# The authoring registry of each scenario that does not run on `registry-demo`.
SCENARIO_REGISTRY: dict[str, Path] = {"transferencia": TRANSFER_DEMO}
_WORLDS: dict[str, Callable[..., EngineWorld]] = {"transferencia": transfer_world}


def record_scenario(name: str, registry_root: Path | None = None, *,
                    telemetry: TurnTelemetry | None = None) -> Fixture:
    """Records `name` over `registry_root` (by default, the scenario's own registry). The fixture's `events`
    are the entry run's chain; `linked`, the chains of the runs it transferred to, in creation order.
    `telemetry` only observes: the fixture is byte-identical with it or without it (T-M11-15)."""
    root = registry_root or SCENARIO_REGISTRY.get(name, REGISTRY_DEMO)
    world = _WORLDS.get(name, EngineWorld)(registry_root=root, record=True, telemetry=telemetry)
    SCENARIOS[name](world)
    assert world.driver.run_id is not None and world.recording_tools and world.recording_llm
    origin = world.driver.run_id
    linked: list[EngineEvent] = []
    if world.driver.session_id is not None:
        with world.store.uow() as uow:
            runs = uow.list_runs_by_session(world.driver.session_id)
        later = [r.run_id for r in runs if r.run_id != origin]
        linked = [e for run_id in later for e in world.audit.read(run_id)]
    return build_fixture(name, origin, world.release.id, world.audit.read(origin), world.driver.ops,
                         world.recording_tools, world.recording_llm, linked=linked)
