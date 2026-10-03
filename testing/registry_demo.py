"""Demo del registry con claves y dobles de PRUEBA (nunca producción).

    uv run python -m testing.registry_demo credentials   # imprime JWS de una persona y del bot constructor
"""

import json
import sys
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Literal

from agent_core.composition import EngineScenarioHarness, EvalStorage
from agent_core.decision import DecisionProvider, RawPrediction
from agent_core.decision.calibration.artifact import InMemoryCalibrationSource
from agent_core.domain import GatewayError
from agent_core.ports import IdentityVerifier
from agent_core.registry import EvalSuite
from agent_core.views import FieldClassifier
from testing.engine_world import CATALOG, CitingGateway, SyntheticAuthz, demo_calibration
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestStaffIssuer
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.provider import ScriptedProvider, Step
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript

_ISSUER = TestStaffIssuer(FakeClock(), ttl=timedelta(days=3650))


def demo_verifier() -> IdentityVerifier:
    return _ISSUER.verifier()


def issue(actor: Literal["human", "admin", "bot"]) -> str:
    """`human` es el supervisor (constructor y aprobador); `admin` suma `admin`; `bot` es el constructor."""
    match actor:
        case "human":
            return _ISSUER.supervisor()
        case "admin":
            return _ISSUER.admin()
        case "bot":
            return _ISSUER.constructor_bot()


def _script_resuelto(jev: ScriptedProvider, classifier: ScriptedProvider) -> None:
    """Mismo guion que `testing/replay/scenarios.py::resuelto`: continuar, casar el cargo y confirmar."""
    jev.push(RawPrediction(value={"command": "continue"}, p_raw={"command": 0.95}, tokens=20))
    classifier.push(RawPrediction(value={"match": "unica", "transaction": "tx-1"}, p_raw={"match": 0.9},
                                  tokens=10))


DEMO_SCRIPTS: dict[str, Callable[[ScriptedProvider, ScriptedProvider], None]] = {"resuelto": _script_resuelto}


def demo_suite() -> EvalSuite:
    return EvalSuite.model_validate({
        "id": "disputas-suite", "version": "1.0.0", "agent_id": "atencion", "repetitions": 1,
        "scenarios": [{
            "id": "resuelto", "principal": {"id": "cust-001", "attrs": {"country": "CO"}},
            "steps": [{"op": "start"},
                      {"op": "turn", "text": "no reconozco un cargo de ciento veinte dólares"},
                      {"op": "confirm", "answer": "yes"}],
            "seed": {"tools": {
                "buscar_transacciones": [{"result": [
                    {"transaction_id": "tx-1", "amount": "120.50", "currency": "USD"},
                    {"transaction_id": "tx-2", "amount": "30.00", "currency": "USD"}]}],
                "seleccionar": [{"result": {"transaction_id": "tx-1", "amount": "120.50",
                                            "currency": "USD"}}],
                "convertir_moneda": [{"result": "120.50"}],
                "radicar_pqr": [{"result": {"status": "Open", "id": "pqr-demo-1"}}],
                "obtener_pqr": [{"result": {"status": "Open", "id": "pqr-demo-1"}}]}},
            "expect": {"outcome": "resolved", "actions_verified": ["radicar_pqr"], "escalated": False}}]})


def build_harness(gateway_error: GatewayError | None = None, provider_failure: Step | None = None,
                  persistent: bool = False) -> EngineScenarioHarness:
    """`persistent`: todas las ejecuciones comparten la base de evaluación, como en `serve`."""
    clock, ids = FakeClock(), FakeIds()

    class _Gateway(CitingGateway):
        def generate(self, *a: Any, **k: Any) -> Any:
            if gateway_error is not None:
                raise gateway_error
            return super().generate(*a, **k)

    def providers(scenario_id: str) -> Mapping[str, DecisionProvider]:
        jev, classifier = ScriptedProvider("jev", clock=clock), ScriptedProvider("classifier", clock=clock)
        script = DEMO_SCRIPTS.get(scenario_id)
        if provider_failure is not None:
            jev.push(provider_failure)  # la primera llamada de JEV falla
        elif script is not None:
            script(jev, classifier)
        return {"jev": jev, "classifier": classifier}

    shared = InMemoryStore()

    def storage() -> EvalStorage:
        store = shared if persistent else InMemoryStore()
        return EvalStorage(uow_factory=store.uow, audit=InMemoryAuditSink(store),
                           transcript=InMemoryTranscript())

    return EngineScenarioHarness(
        clock=clock, ids=ids, keys=FakeKeyProvider.default(), gateway=_Gateway(), providers=providers,
        calibrations=InMemoryCalibrationSource({"cal-demo": demo_calibration()}), authz=SyntheticAuthz(),
        storage=storage, classifier=FieldClassifier(CATALOG))


if __name__ == "__main__":
    if sys.argv[1:] == ["credentials"]:
        print(json.dumps({"_note": "PRUEBA", "supervisor": issue("human"), "admin": issue("admin"),
                          "bot": issue("bot")}, indent=2))
