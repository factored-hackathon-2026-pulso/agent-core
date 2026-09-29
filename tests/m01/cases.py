"""Flows y entidades sintéticos para las pruebas de M1. Cada prueba muta una copia de `base()`."""

from copy import deepcopy
from typing import Any

from agent_core.domain import (
    Agent,
    DecisionModelDef,
    Flow,
    ModelProfile,
    Policy,
    Prompt,
    RegistryEntity,
    Template,
    ToolDef,
)
from agent_core.flows.registry import AuthoringRegistry

_TEXTS = {
    "t/pedir": "¿Qué necesitas?",
    "t/resumen": "Voy a registrar tu solicitud.",
    "t/hecho": "Listo: {{ facts.verif.value.id }}",
    "t/hecho2": "Listo: {{ facts.verif2.value.id }}",
    "t/seguro": "No pudimos completar la solicitud.",
    "t/lee_res": "Resultado: {{ facts.res.value.id }}",
    "t/lee_estado": "Estado: {{ facts.estado.value.x }}",
    "t/lee_calc": "Monto: {{ facts.monto_calc.value }}",
    "t/lee_c": "Valor: {{ facts.c.value }}",
    "t/lee_datos": "Datos: {{ facts.datos.value.id }}",
    "t/lee_dec": "Elegido: {{ decisions.d.campo }}",
    "t/aclarar": "¿Puedes aclararlo?",
    "t/abstencion": "No puedo ayudarte con eso.",
    "t/traspaso": "Te paso con un asesor.",
    "t/acuse": "Lo anoto para después.",
    "t/oferta": "¿Seguimos con lo pendiente?",
    "t/idioma_no_soportado": "Atiendo en español y portugués.",
    "t/mensaje_largo": "El mensaje es muy largo.",
}


def _template(tid: str, text: str, locales: tuple[str, ...] = ("es", "pt")) -> Template:
    return Template.model_validate({"id": tid, "version": "1.0.0", "locales": {loc: text for loc in locales}})


def _tool(tid: str, risk: str, **extra: Any) -> ToolDef:
    return ToolDef.model_validate(
        {"id": tid, "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
         "idempotent": risk in ("read", "compute"), **extra}
    )


def _model(mid: str, calibrated: list[str], enum: list[str] | None = None) -> DecisionModelDef:
    return DecisionModelDef.model_validate(
        {"id": mid, "version": "1.0.0",
         "output_schema": {"type": "object", "properties": {"campo": {"enum": enum or ["a", "b"]}}},
         "calibrated_fields": calibrated, "providers": [{"provider": "rule"}],
         "calibration": {"method": "none"}}
    )


def _prompt(pid: str, profile: str) -> Prompt:
    return Prompt.model_validate(
        {"id": pid, "version": "1.0.0", "locales": {"es": "Responde.", "pt": "Responda."},
         "model_profile": profile}
    )


ENTITIES: list[RegistryEntity] = [
    *(_template(tid, text) for tid, text in _TEXTS.items()),
    _template("t/solo_es", "Solo español.", ("es",)),
    _tool("leer", "read"),
    _tool("leer_escritura", "read"),
    _tool("calc", "compute"),
    _tool("escribir", "write_reversible", readback_by="idempotency_key"),
    _model("modelo", ["campo"]),
    _model("modelo_nc", []),
    _model("modelo_lc", ["campo"], ["a", "low_confidence"]),
    Policy.model_validate(
        {"id": "pol", "version": "1.0.0", "owner": "riesgo",
         "expr": {">": [{"var": "facts.datos.value.n"}, 500]}, "rationale": "sintética"}
    ),
    ModelProfile.model_validate(
        {"id": "perfil", "version": "1.0.0", "endpoint_alias": "demo", "model": "modelo-sintetico",
         "temperature": "0", "max_tokens": 400,
         "price": {"input_per_mtok": "1", "output_per_mtok": "2", "source": "sintético",
                   "as_of": "2026-09-28"}}
    ),
    _prompt("p/gen", "perfil@1"),
    _prompt("p/sinperfil", "perfil_x@1"),
]


def registry(*extra: RegistryEntity) -> AuthoringRegistry:
    return AuthoringRegistry.from_entities([*ENTITIES, *extra])


def base() -> dict[str, Any]:
    """Flow conversacional válido: collect → lectura → confirm → escritura → verify → respond → end."""
    return deepcopy(
        {
            "id": "base",
            "version": "1.0.0",
            "priority": 10,
            "nodes": [
                {"id": "pedir", "type": "collect", "config": {"slot": "desc", "prompt_ref": "t/pedir"},
                 "next": {"ok": "buscar", "max_attempts": "esc"}},
                {"id": "buscar", "type": "tool",
                 "config": {"tool": "leer@1", "args": {"q": "slots.desc"}, "save_as": "datos"},
                 "next": {"ok": "confirmar", "error": "esc", "timeout": "esc", "denied": "esc"}},
                {"id": "confirmar", "type": "confirm",
                 "config": {"action": {"tool": "escribir@1",
                                       "args": {"q": "slots.desc", "ref": "facts.datos.value.id"}},
                            "summary_template": "t/resumen"},
                 "next": {"yes": "escribir", "no": "fin_cancelado", "unclear": "confirmar",
                          "max_attempts": "esc"}},
                {"id": "escribir", "type": "tool", "config": {"action_from": "confirmar", "save_as": "res"},
                 "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}},
                {"id": "verificar", "type": "verify",
                 "config": {"readback": "leer_escritura@1", "by": "idempotency_key",
                            "predicate": {"==": [{"var": "readback.status"}, "ok"]}, "save_as": "verif"},
                 "next": {"verified": "ok_msg", "failed": "esc"}},
                {"id": "ok_msg", "type": "respond",
                 "config": {"template_ref": "t/hecho", "claims": ["confirmar"]},
                 "next": {"next": "fin"}},
                {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
                {"id": "fin_cancelado", "type": "end", "config": {"outcome": "cancelled"}},
                {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
            ],
        }
    )


def task_base() -> dict[str, Any]:
    """Flow de modo task válido, sin nodos que esperan."""
    return deepcopy(
        {
            "id": "tarea",
            "version": "1.0.0",
            "priority": 10,
            "nodes": [
                {"id": "buscar", "type": "tool",
                 "config": {"tool": "leer@1", "args": {"q": "hola"}, "save_as": "datos"},
                 "next": {"ok": "fin_ok", "error": "fin_fallo", "timeout": "fin_fallo",
                          "denied": "fin_fallo"}},
                {"id": "fin_ok", "type": "end",
                 "config": {"outcome": "completed", "output_map": {"dato": "facts.datos.value.id"}}},
                {"id": "fin_fallo", "type": "end", "config": {"outcome": "failed"}},
            ],
        }
    )


def node(d: dict[str, Any], node_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(n for n in d["nodes"] if n["id"] == node_id)
    return found


def flow(d: dict[str, Any]) -> Flow:
    return Flow.model_validate(d)


AGENT: dict[str, Any] = {
    "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "base@1",
    "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
    "supported_locales": ["es", "pt"], "default_locale": "es", "tools_allowed": ["leer@1", "escribir@1"],
    "budgets": {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
                "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000},
    "templates": {"clarify": "t/aclarar", "abstain": "t/abstencion", "handoff": "t/traspaso",
                  "pending_ack": "t/acuse", "pending_offer": "t/oferta",
                  "unsupported_language": "t/idioma_no_soportado", "input_too_large": "t/mensaje_largo"},
    "max_clarifications": 2, "on_clarify_exhausted": "escalate", "default_target_queue": "general",
}


def agent(**over: Any) -> Agent:
    return Agent.model_validate(deepcopy(AGENT) | over)
