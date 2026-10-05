"""Registry en `serve` (spec 2026-09-30-serve-registry-api): servicio con evaluación real."""

from agent_core.adapters.llm import HttpLLMGateway
from agent_core.composition.evaluation import EngineScenarioHarness, EvalStorage
from agent_core.composition.registry import UowRunReleases
from agent_core.composition.serve_ports import RegistryApiPorts, ServePorts
from agent_core.registry import LocalSandbox, RegistryService, ScenarioEvaluator

__all__ = ["RegistryApiPorts", "build_registry_service_for_serve"]


def build_registry_service_for_serve(ports: ServePorts) -> RegistryService:
    """Evaluación real: el motor compuesto con el gateway, JEV, la calibración y la autorización de `serve`;
    las tools van al sandbox local. Un escenario a la vez: el harness comparte reloj e ids."""
    api = ports.registry_api
    if api is None:
        raise ValueError("serve no tiene la API del registry habilitada")
    harness = EngineScenarioHarness(
        clock=ports.clock, ids=ports.ids, keys=ports.keys, gateway=ports.gateway,
        providers=lambda _scenario_id: ports.providers, calibrations=ports.calibrations, authz=ports.authz,
        storage=lambda: EvalStorage(uow_factory=api.eval_uow_factory, audit=api.eval_audit,
                                    transcript=ports.transcript),
        classifier=ports.classifier,
        # Each target (candidate/base) is evaluated against ITS entities, not the live registry of `serve`.
        bind_gateway=ports.gateway.bound_to if isinstance(ports.gateway, HttpLLMGateway) else None)
    evaluator = ScenarioEvaluator(harness, LocalSandbox(ports.ids), max_workers=1)
    runs = UowRunReleases(ports.uow_factory)
    return RegistryService(api.store, evaluator, ports.clock, ports.ids, runs=runs)
