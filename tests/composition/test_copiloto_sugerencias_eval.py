"""El agente `copiloto-sugerencias` (ADR 0026) corre en el motor real y su `eval_suite` PUEDE FALLAR.

QUÉ PRUEBA. El agente es datos (`tests/fixtures/copiloto-sugerencias`): se carga, se valida con M1, se fija
como release y se ejecuta con el motor real, el clasificador y el modelo GUIONADOS (sin red, sin claves). La
salida del modelo guionado es la ESPERADA de cada caso; la suite comprueba que el flow, la política y la
validación de M8 la dejan pasar y que rechazan lo que no debe.

QUÉ NO. La calidad de ningún modelo real: ninguno produjo estas sugerencias. Los umbrales, el prompt y la
política de escalamiento son PROVISIONALES. La autorización del arnés es la permisiva de las pruebas: «otro
cliente fuera de la delegación» no se puede evaluar aquí.

LAS MUTACIONES: cada prueba rompe una pieza (el modelo, el clasificador o los datos del agente) y exige que
suite lo detecte. Si la suite pasara con la pieza rota, la prueba fallaría.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_core.domain import Agent, EntityRef, JsonValue, RunClosed
from agent_core.flows import load_registry, validate_registry
from agent_core.registry import (
    EvalRequest,
    EvalSuite,
    LocalSandbox,
    ScenarioEvaluator,
    Yardstick,
    suite_problems,
)
from agent_core.registry.evaluation.report import EvalReport
from testing.copiloto_sugerencias import (
    AGENT,
    FIXTURE,
    Case,
    build_harness,
    eval_target,
    load_cases,
    load_suite,
)
from testing.fakes.ids import FakeIds

SCENARIOS = {
    "sin-sugerencia-saludo",
    "reply-con-cifra-respaldada",
    "tool-de-lectura",
    "escalar-por-fraude",
    "pii-en-el-texto-del-cliente",
    "lista-vacia-sin-turnos",
}


def evaluate(root: Path = FIXTURE, **harness: Any) -> EvalReport:
    suite = load_suite(root)
    request = EvalRequest(candidate=eval_target(root), new=Yardstick(metrics=[], suite=suite))
    return ScenarioEvaluator(build_harness(root, **harness), LocalSandbox(FakeIds())).run(request)


def failed_scenarios(report: EvalReport) -> set[str]:
    return {r.scenario_id for r in report.results if not r.score.passed}


# ---------------------------------------------------------------- el agente y la suite son datos válidos
def test_the_agent_directory_is_a_valid_registry_with_a_releasable_agent() -> None:
    reg, violations = load_registry(FIXTURE)
    assert violations == [] and validate_registry(reg) == []
    assert [d.id for d in reg.releases()] == ["sugerencias-demo"]


def test_the_suite_loads_covers_the_cases_and_has_no_problems() -> None:
    suite = load_suite()
    agent = eval_target().registry.get(EntityRef(id=AGENT, version="1.0.0"), Agent)
    assert {s.id for s in suite.scenarios} == SCENARIOS and suite_problems(agent, suite) == []


def test_every_scenario_input_is_the_input_of_its_case() -> None:
    """Dos copias del mismo dato no deben desviarse."""
    cases = load_cases()
    for scenario in load_suite().scripted():
        assert scenario.steps[0].input == cases[scenario.id]["input"], scenario.id


def test_the_suite_asserts_on_something_for_every_scenario() -> None:
    for scenario in load_suite().scripted():
        expect = scenario.expect
        assert expect.outcome is not None and (expect.suggestion_count is not None or expect.suggestions)


# ---------------------------------------------------------------- la suite pasa con todo sano
def test_the_suite_passes_with_the_scripted_model_and_every_run_completes() -> None:
    report = evaluate()
    assert report.verdict == "pass", report.detail
    assert len(report.results) == 12 and failed_scenarios(report) == set()  # 6 escenarios x 2 repeticiones
    assert {r.score.outcome for r in report.results} == {"completed"}
    assert all(r.score.escalated is False for r in report.results)  # el copiloto recomienda, no escala


def test_two_runs_of_the_same_scenario_produce_the_same_suggestions() -> None:
    harness = build_harness()
    target, suite = eval_target(), load_suite()
    scenario = next(s for s in suite.scripted() if s.id == "escalar-por-fraude")
    sandbox = LocalSandbox(FakeIds())
    first = harness.run_with_suggestions(
        target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target))
    )
    second = harness.run_with_suggestions(
        target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target))
    )
    assert first.suggestions == second.suggestions and first.suggestions
    assert [e.type for e in first.events] == [e.type for e in second.events]


def test_the_escalation_comes_from_the_rule_and_the_evidence_from_the_slots_not_from_the_model() -> None:
    seen: list[dict[str, JsonValue]] = []
    harness = build_harness(seen=seen)
    target, suite = eval_target(), load_suite()
    scenario = next(s for s in suite.scripted() if s.id == "escalar-por-fraude")
    sandbox = LocalSandbox(FakeIds())
    run = harness.run_with_suggestions(
        target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target))
    )
    (escalate,) = run.suggestions
    assert escalate.type == "escalate" and escalate.reason_code == "rule:sugerir-escalamiento"  # type: ignore[union-attr]
    assert escalate.evidence == ["sla_estado: vencido", "prioridad: alta"]  # type: ignore[union-attr]
    (seen_by_the_model,) = seen
    assert seen_by_the_model["escalation"] == {"required": True, "reason_code": "rule:sugerir-escalamiento"}
    assert "prioridad: alta" not in str(seen_by_the_model)  # the evidence is not sent to the model


def test_the_model_only_gets_the_model_view_pii_is_tokens_and_the_customer_text_is_wrapped() -> None:
    seen: list[dict[str, JsonValue]] = []
    case = load_cases()["pii-en-el-texto-del-cliente"]
    evaluate(seen=seen)
    shown = str(seen)
    assert seen and all(secret not in shown for secret in case["must_not_leak"])
    assert "⟦" in shown and "<datos_no_confiables" in shown


def test_no_node_of_the_flow_reads_the_assistant_session_id_so_it_is_traceability_not_memory() -> None:
    reg, _ = load_registry(FIXTURE)
    flow = next(e for e in reg.all(__import__("agent_core.domain", fromlist=["EntityKind"]).EntityKind.flow))
    dumped = flow.model_dump_json()  # type: ignore[attr-defined]
    assert "assistant_session_id" not in dumped
    agent = eval_target().registry.get(EntityRef(id=AGENT, version="1.0.0"), Agent)
    assert agent.input_schema is not None and "assistant_session_id" in agent.input_schema  # it is accepted


# ---------------------------------------------------------------- mutaciones: la suite DEBE fallar
def only(scenario: str, changed: Callable[[Case, dict[str, JsonValue]], dict[str, JsonValue]]) -> Any:
    """Un `tamper_model` que solo deforma la salida del escenario dado."""

    def tamper(case: Case, output: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return changed(case, output) if case["id"] == scenario else output

    return tamper


def with_items(output: dict[str, JsonValue], *items: JsonValue) -> dict[str, JsonValue]:
    current = output["suggestions"]
    assert isinstance(current, list)
    return {"suggestions": [*current, *items]}


UNSUPPORTED_FIGURE = {
    "type": "reply",
    "text": "El saldo es de 999.99 USD.",
    "citations": [],
    "language": "es",
}
MODEL_MUTATIONS: list[tuple[str, str, Callable[[Case, dict[str, JsonValue]], dict[str, JsonValue]]]] = [
    (
        "cifra sin respaldo (inventada)",
        "reply-con-cifra-respaldada",
        lambda c, o: {"suggestions": [{**o["suggestions"][0], "text": "El saldo es de 999.99 USD."}]},
    ),  # type: ignore[index]
    (
        "borrador que pierde la cifra",
        "reply-con-cifra-respaldada",
        lambda c, o: {"suggestions": [{**o["suggestions"][0], "text": "Tu saldo está en tus productos."}]},
    ),  # type: ignore[index]
    ("el modelo no devuelve nada", "reply-con-cifra-respaldada", lambda c, o: {"suggestions": []}),
    (
        "el modelo se escala solo a una consulta simple",
        "tool-de-lectura",
        lambda c, o: with_items(o, {"type": "escalate", "motive_draft": "Escalar."}),
    ),
    (
        "tool fuera del catálogo",
        "tool-de-lectura",
        lambda c, o: {"suggestions": [{**o["suggestions"][0], "tool": "borrar_cuenta@1"}]},
    ),  # type: ignore[index]
    (
        "argumento que nombra al cliente",
        "tool-de-lectura",
        lambda c, o: {
            "suggestions": [{**o["suggestions"][0], "args": {"limite": 1, "customer_id": "cust-001"}}]
        },
    ),  # type: ignore[index]
    (
        "una action (no hay actions_allowed)",
        "tool-de-lectura",
        lambda c, o: with_items(
            o, {"type": "action", "tool": "radicar_pqr@1", "args": {}, "summary": "Radicar."}
        ),
    ),
    (
        "la tarjeta del cliente vuelve en el borrador",
        "pii-en-el-texto-del-cliente",
        lambda c, o: {
            "suggestions": [{**o["suggestions"][0], "text": "Recibí tu tarjeta 4111 1111 1111 1111."}]
        },
    ),  # type: ignore[index]
    (
        "el correo del cliente vuelve en el borrador",
        "pii-en-el-texto-del-cliente",
        lambda c, o: {
            "suggestions": [{**o["suggestions"][0], "text": "Te escribo a ana.prueba@example.test."}]
        },
    ),  # type: ignore[index]
    (
        "el motivo de escalar pierde la señal",
        "escalar-por-fraude",
        lambda c, o: {"suggestions": [{"type": "escalate", "motive_draft": "Revisar el caso."}]},
    ),
    (
        "el modelo no redacta la escalación que el flow decidió",
        "escalar-por-fraude",
        lambda c, o: {"suggestions": []},
    ),
]


@pytest.mark.parametrize(
    ("what", "scenario", "changed"), MODEL_MUTATIONS, ids=[m[0] for m in MODEL_MUTATIONS]
)
def test_a_broken_model_output_is_caught(what: str, scenario: str, changed: Any) -> None:
    del what
    report = evaluate(tamper_model=only(scenario, changed))
    assert report.verdict == "fail"
    assert failed_scenarios(report) == {scenario}  # la mutación rompe el escenario que toca y ningún otro


SIGNAL_MUTATIONS = [
    ("un mensaje con cifra se clasifica como sin sugerencia", "reply-con-cifra-respaldada", "sin_sugerencia"),
    ("un fraude vencido no se detecta como escalación", "escalar-por-fraude", "sugerir"),
    ("un saludo se clasifica como escalación", "sin-sugerencia-saludo", "escalar"),
    ("sin turnos se pide una sugerencia", "lista-vacia-sin-turnos", "sugerir"),
]


@pytest.mark.parametrize(
    ("what", "scenario", "signal"), SIGNAL_MUTATIONS, ids=[m[0] for m in SIGNAL_MUTATIONS]
)
def test_a_wrong_classifier_signal_is_caught(what: str, scenario: str, signal: str) -> None:
    del what
    report = evaluate(tamper_signal=lambda case, current: signal if case["id"] == scenario else current)
    assert report.verdict == "fail" and scenario in failed_scenarios(report)


def test_a_false_escalation_signal_on_a_simple_query_is_contained_by_the_rule() -> None:
    """El clasificador dice «escalar» ante una consulta simple (SLA respondido, prioridad normal):
    la política no lo confirma y el flow sigue por el camino normal. Es el valor de que escalar salga de una
    `rule` y no del modelo (ADR 0026 §5): la suite sigue pasando y no aparece ninguna escalación."""
    report = evaluate(
        tamper_signal=lambda case, current: "escalar" if case["id"] == "tool-de-lectura" else current
    )
    assert report.verdict == "pass", report.detail


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "copiloto-sugerencias"
    shutil.copytree(FIXTURE, target)
    return target


def _edit(root: Path, relative: str, old: str, new: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert old in text, (relative, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


FLOW = "flows/sugerir@1.0.0.yaml"
POLICY = "policies/sugerir-escalamiento@1.0.0.yaml"
DATA_MUTATIONS = [
    (
        "la política nunca recomienda escalar",
        POLICY,
        '{"==": [{var: slots.sla_estado}, vencido]}',
        '{"==": [{var: slots.sla_estado}, nunca]}',
        {"escalar-por-fraude"},
    ),
    (
        "el flow pierde las lecturas que puede recomendar",
        FLOW,
        "[leer_movimientos@1, leer_productos@1, leer_pqr_cliente@1]\n      actions_allowed: []",
        "[leer_productos@1]\n      actions_allowed: []",
        {"tool-de-lectura"},
    ),
    (
        "el flow ya no lee los productos: sin hecho que citar",
        FLOW,
        "slots.espera_del_cliente_segundos, facts.productos.value]",
        "slots.espera_del_cliente_segundos]",
        {"reply-con-cifra-respaldada"},
    ),
    (
        "el flow cambia el motivo de la escalación",
        FLOW,
        'reason_code: "rule:sugerir-escalamiento"',
        'reason_code: "rule:otro-motivo"',
        {"escalar-por-fraude"},
    ),
    (
        "el flow pierde la evidencia de la escalación",
        FLOW,
        "evidence_from: [slots.sla_estado, slots.prioridad]",
        "evidence_from: [slots.prioridad]",
        {"escalar-por-fraude"},
    ),
    (
        "sin sugerencia termina como fallo y no como resultado válido",
        FLOW,
        "{id: fin_vacio, type: end, config: {outcome: completed}}",
        "{id: fin_vacio, type: end, config: {outcome: failed}}",
        {"sin-sugerencia-saludo", "lista-vacia-sin-turnos"},
    ),
]


@pytest.mark.parametrize(
    ("what", "relative", "old", "new", "breaks"), DATA_MUTATIONS, ids=[m[0] for m in DATA_MUTATIONS]
)
def test_breaking_the_agent_data_is_caught(
    tmp_path: Path, what: str, relative: str, old: str, new: str, breaks: set[str]
) -> None:
    del what
    root = _copy_fixture(tmp_path)
    _edit(root, relative, old, new)
    report = evaluate(root)
    assert report.verdict == "fail"
    assert breaks <= failed_scenarios(report)


def test_a_data_mutation_that_breaks_the_flow_statically_is_rejected_by_m1(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    _edit(
        root, FLOW, "sugerir_escalando\n    type: suggest", "sugerir_escalando\n    type: suggest"
    )  # no-op control
    reg, violations = load_registry(root)
    assert violations == [] and validate_registry(reg) == []
    # the escalation hanging from the `false` branch of the rule is a G0-28 violation
    _edit(
        root,
        FLOW,
        "next: {true: sugerir_escalando, false: leer_productos}",
        "next: {true: leer_productos, false: sugerir_escalando}",
    )
    reg, violations = load_registry(root)
    assert "G0-28" in {v.rule for v in [*violations, *validate_registry(reg)]}


def test_a_run_that_escalates_after_suggesting_delivers_no_suggestions(tmp_path: Path) -> None:
    """ADR 0026: la lista es el resultado de un run que terminó `completed`; si el run escala, lo que el nodo
    alcanzó a producir (el evento sí queda) no se entrega."""
    root = _copy_fixture(tmp_path)
    _edit(
        root,
        FLOW,
        "{id: fin_ok, type: end, config: {outcome: completed}}",
        "{id: fin_ok, type: escalate, config: {reason_code: tool_failure}}",
    )
    target, suite = eval_target(root), load_suite(root)
    scenario = next(s for s in suite.scripted() if s.id == "reply-con-cifra-respaldada")
    sandbox = LocalSandbox(FakeIds())
    run = build_harness(root).run_with_suggestions(
        target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target))
    )
    produced = [e for e in run.events if e.type == "suggestions_produced"]
    assert len(produced) == 1 and produced[0].payload.reply == 1  # type: ignore[union-attr]
    assert any(e.type == "escalated" for e in run.events)
    assert run.suggestions == []


def test_the_control_of_the_mutation_harness_the_unmutated_copy_passes(tmp_path: Path) -> None:
    """Sin esta prueba, un fallo por una copia mal hecha pasaría por una detección."""
    assert evaluate(_copy_fixture(tmp_path)).verdict == "pass"


def test_every_run_closes_and_the_unmutated_events_carry_no_sensitive_value() -> None:
    from agent_core.domain import dumps

    case = load_cases()["pii-en-el-texto-del-cliente"]
    target, suite = eval_target(), load_suite()
    scenario = next(s for s in suite.scripted() if s.id == "pii-en-el-texto-del-cliente")
    sandbox = LocalSandbox(FakeIds())
    run = build_harness().run_with_suggestions(
        target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target))
    )
    assert any(isinstance(e, RunClosed) for e in run.events)
    blob = " ".join(dumps(e) for e in run.events) + " ".join(dumps(s) for s in run.suggestions)
    assert [secret for secret in case["must_not_leak"] if secret in blob] == []


def test_the_suite_object_is_the_one_on_disk() -> None:
    assert isinstance(load_suite(), EvalSuite) and load_suite().agent_id == AGENT
