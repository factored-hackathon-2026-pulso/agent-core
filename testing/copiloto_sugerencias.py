"""Mundo de PRUEBA del agente `copiloto-sugerencias` (ADR 0026): motor real, modelo y clasificador GUIONADOS.

Sin red, sin LLM real y sin claves. El modelo guionado produce, para cada caso de `casos/sinteticos.yaml`, la
salida ESPERADA del caso (lo que el agente debería proponer): el `eval_suite` comprueba entonces que el resto
del sistema (flow, política, validación de M8, contrato de salida) la deja pasar intacta y que rechaza lo que
no debe. Lo que NO prueba es la calidad de ningún modelo real: ninguno produjo estas sugerencias.

`tamper_model` y `tamper_signal` rompen el modelo guionado a propósito (pruebas de mutación del eval).
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent_core.composition import EngineScenarioHarness, EvalStorage
from agent_core.decision import DecisionProvider, RawPrediction
from agent_core.decision.calibration.artifact import DirectoryCalibrationSource
from agent_core.domain import EntityRef, JsonValue, Locale
from agent_core.flows import load_registry, load_yaml, pin_release
from agent_core.ports import GenerationResult, LLMGateway
from agent_core.registry import EvalSuite, EvalTarget, SnapshotRegistry
from agent_core.views import FieldClassifier, FieldRule
from testing.engine_world import CATALOG, SyntheticAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.provider import ScriptedProvider
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.transcript import InMemoryTranscript

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "copiloto-sugerencias"
RELEASE = "sugerencias-demo"
AGENT = "copiloto-sugerencias"

Case = dict[str, Any]
Tamper = Callable[[Case, dict[str, JsonValue]], dict[str, JsonValue]]


def load_cases(root: Path = FIXTURE) -> dict[str, Case]:
    data: Any = load_yaml((root / "casos" / "sinteticos.yaml").read_bytes())
    return {str(c["id"]): c for c in data["casos"]}


def load_suite(root: Path = FIXTURE) -> EvalSuite:
    return EvalSuite.model_validate(
        load_yaml((root / "eval_suites" / "copiloto-sugerencias-suite@1.0.0.yaml").read_bytes())
    )


def eval_target(root: Path = FIXTURE) -> EvalTarget:
    """La release `sugerencias-demo` del directorio, fijada como lo haría el registry."""
    reg, violations = load_registry(root)
    assert not violations, violations
    pinned = pin_release(reg, RELEASE)
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))


def signal_of(case: Case) -> str:
    """La señal que el clasificador guionado emite: la que lleva a la salida esperada del caso."""
    kinds = [item["type"] for item in case["expected"]]
    if not kinds:
        return "sin_sugerencia"
    return "escalar" if "escalate" in kinds else "sugerir"


def raw_model_output(case: Case, inputs: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """La salida ESPERADA del caso con la forma que el modelo debe devolver (`SUGGESTIONS_SCHEMA`): un
    `escalate` solo lleva `motive_draft` (el motivo y la evidencia son del flow) y las citas apuntan a los
    `fact_id` reales que M8 entrega, que un caso no puede conocer de antemano."""
    facts = inputs.get("citable_facts")
    citable: list[JsonValue] = list(facts.values()) if isinstance(facts, dict) else []
    items: list[JsonValue] = []
    for expected in case["expected"]:
        item: dict[str, JsonValue] = {k: v for k, v in expected.items() if k not in ("executable",)}
        if expected["type"] == "escalate":
            item = {"type": "escalate", "motive_draft": expected["motive_draft"]}
        elif expected["type"] == "reply":
            item["citations"] = citable[: len(expected["citations"])]
        items.append(item)
    return {"suggestions": items}


class ExpectedSuggestionsGateway:
    """`LLMGateway` guionado de UN caso: devuelve la salida esperada, o la que `tamper` deforme."""

    def __init__(
        self, case: Case, tamper: Tamper | None = None, seen: list[dict[str, JsonValue]] | None = None
    ) -> None:
        self._case, self._tamper, self._seen = case, tamper, seen
        self.calls = 0

    def generate(
        self,
        prompt: EntityRef,
        inputs_model_view: dict[str, JsonValue],
        locale: Locale,
        schema: dict[str, JsonValue] | None = None,
    ) -> GenerationResult:
        self.calls += 1
        if self._seen is not None:
            self._seen.append(deepcopy(inputs_model_view))  # lo que recibió el "modelo", para auditarlo
        output = raw_model_output(self._case, inputs_model_view)
        if self._tamper is not None:
            output = self._tamper(self._case, output)
        return GenerationResult(
            output=output, tokens_in=300, tokens_out=80, cost_usd=Decimal("0.002"), model="modelo-sintetico-1"
        )


def field_classifier() -> FieldClassifier:
    """El catálogo de la demo más lo que devuelve `leer_productos`: números e ids son `public` (inventados);
    sin clasificar, M7 tokeniza todo campo y la cifra del borrador no tendría respaldo."""
    public = FieldRule(field_class="public")
    names = (
        "product",
        "credit_limit",
        "minimum_payment",
        "due_date",
        "currency",
        "movement_id",
        "merchant",
        "posted_at",
        "case_id",
        "topic",
    )
    return FieldClassifier(
        {**CATALOG, **dict.fromkeys(names, public), "balance_due": FieldRule(field_class="financial")}
    )


def build_harness(
    root: Path = FIXTURE,
    *,
    tamper_model: Tamper | None = None,
    tamper_signal: Callable[[Case, str], str] | None = None,
    seen: list[dict[str, JsonValue]] | None = None,
) -> EngineScenarioHarness:
    """El motor real con el clasificador (`jev`) y el modelo de sugerencias guionados por caso. `seen`
    recoge lo que recibió el modelo (vista `model`) en cada llamada."""
    clock, ids = FakeClock(), FakeIds()
    cases = load_cases(root)

    def providers(scenario_id: str) -> Mapping[str, DecisionProvider]:
        jev = ScriptedProvider("jev", clock=clock)
        case = cases.get(scenario_id)
        if case is not None:
            signal = signal_of(case)
            if tamper_signal is not None:
                signal = tamper_signal(case, signal)
            jev.push(RawPrediction(value={"senal": signal}, p_raw={"senal": 0.95}, tokens=20))
        return {"jev": jev}

    def gateway_for(scenario_id: str) -> LLMGateway:
        return ExpectedSuggestionsGateway(cases[scenario_id], tamper_model, seen)

    def storage() -> EvalStorage:  # un store por ejecución: cada escenario arranca limpio
        store = InMemoryStore()
        return EvalStorage(
            uow_factory=store.uow, audit=InMemoryAuditSink(store), transcript=InMemoryTranscript()
        )

    return EngineScenarioHarness(
        clock=clock,
        ids=ids,
        keys=FakeKeyProvider.default(),
        gateway=ExpectedSuggestionsGateway({"expected": []}),
        providers=providers,
        calibrations=DirectoryCalibrationSource(root / "calibrations"),
        authz=SyntheticAuthz(),
        storage=storage,
        classifier=field_classifier(),
        gateway_for=gateway_for,
    )
