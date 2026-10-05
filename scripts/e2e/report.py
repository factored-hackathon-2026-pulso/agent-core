"""Métricas y depuración de las pruebas E2E, calculadas sobre la tabla `audit_events` (solo lectura).

    uv run python scripts/e2e/report.py                      # resumen por agente de todos los runs
    uv run python scripts/e2e/report.py --agent copiloto-asesor
    uv run python scripts/e2e/report.py --run <run_id>       # línea de tiempo de un run, para depurar
    uv run python scripts/e2e/report.py --json               # el mismo resumen, en JSON

Usa AGENTCORE_REGISTRY_DSN (o --dsn). Nunca imprime contenido de la conversación: la auditoría no lo guarda
(solo huellas, ids, enums y contadores). Son las métricas del ADR 0020 derivables de los eventos."""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

Event = Mapping[str, Any]


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _new() -> dict[str, Any]:
    return {
        "runs": 0, "runs_open": 0, "outcomes": Counter(), "turns": 0, "turn_ms": [], "degraded_turns": 0,
        "injection_flagged": 0, "commands": Counter(), "below_threshold": 0, "responses": Counter(),
        "fallbacks": 0, "validator_failures": Counter(), "tools": Counter(), "agent_steps": Counter(),
        "agent_failures": Counter(), "llm_calls": 0, "tokens": 0, "cost_usd": Decimal("0"),
        "escalations": Counter(), "transfers": 0}


def _accumulate(m: dict[str, Any], e: Event) -> None:
    payload, kind = e["payload"], e["type"]
    if kind == "run_closed":
        m["outcomes"][str(payload["outcome"])] += 1
    elif kind == "turn_started":
        guards = payload.get("guards") or {}
        m["injection_flagged"] += bool((guards.get("injection") or {}).get("flagged"))
    elif kind == "turn_completed":
        m["turns"] += 1
        m["turn_ms"].append(int(payload["duration_ms"]))
        m["degraded_turns"] += bool(payload.get("degraded"))
    elif kind == "command_emitted":
        m["commands"][str(payload["command"])] += 1
    elif kind == "decision_made":
        flags = payload.get("above_threshold") or {}
        m["below_threshold"] += not all(flags.values())
    elif kind == "response_emitted":
        m["responses"][str(payload["kind"])] += 1
        m["fallbacks"] += bool(payload.get("fallback_used"))
        for failure in (payload.get("validator") or {}).get("failures", []):
            m["validator_failures"][str(failure)] += 1
        llm = payload.get("llm")
        if llm:
            m["llm_calls"] += int(llm["calls"])
            m["tokens"] += int(llm["tokens_in"]) + int(llm["tokens_out"])
            m["cost_usd"] += Decimal(str(llm["cost_usd"]))
    elif kind == "tool_called":
        m["tools"][f'{payload["tool"]["id"]}:{payload["status"]}'] += 1
    elif kind == "agent_step":
        m["agent_steps"][str(payload["kind"])] += 1
        if payload.get("error_kind"):
            m["agent_failures"][str(payload["error_kind"])] += 1
    elif kind == "escalated":
        m["escalations"][str(payload.get("reason_code", "?"))] += 1
    elif kind == "run_transferred":
        m["transfers"] += 1


def compute(events: Iterable[Event]) -> dict[str, dict[str, Any]]:
    """`{agente: métricas}`. Un run es del agente de su `run_started`; una transferencia crea otro run."""
    by_run: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_run[event["run_id"]].append(event)
    agents: dict[str, dict[str, Any]] = {}
    for run_events in by_run.values():
        started = next((e for e in run_events if e["type"] == "run_started"), None)
        if started is None:
            continue
        m = agents.setdefault(started["payload"]["agent"]["id"], _new())
        m["runs"] += 1
        m["runs_open"] += not any(e["type"] == "run_closed" for e in run_events)
        for e in run_events:
            _accumulate(m, e)
    return agents


def summarize(agents: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Las mismas métricas ya derivadas (tasas, percentiles) y serializables."""
    out: dict[str, dict[str, Any]] = {}
    for agent, m in sorted(agents.items()):
        closed = sum(m["outcomes"].values())
        responses = sum(m["responses"].values())

        def rate(n: int, d: int) -> float | None:
            return round(n / d, 4) if d else None

        out[agent] = {
            "runs": m["runs"], "runs_open": m["runs_open"], "turns": m["turns"],
            "outcomes": dict(m["outcomes"]),
            "resolved_rate": rate(m["outcomes"].get("resolved", 0), closed),
            "escalation_rate": rate(m["outcomes"].get("escalated", 0), closed),
            "abstained_rate": rate(m["outcomes"].get("abstained", 0), closed),
            "turn_ms_p50": _percentile(m["turn_ms"], 0.5), "turn_ms_p95": _percentile(m["turn_ms"], 0.95),
            "degraded_turns": m["degraded_turns"], "injection_flagged": m["injection_flagged"],
            "commands": dict(m["commands"]),
            "clarifications_per_run": round(m["commands"].get("clarify", 0) / m["runs"], 3),
            "decisions_below_threshold": m["below_threshold"],
            "responses": dict(m["responses"]),
            "template_fallback_rate": rate(m["fallbacks"], responses),
            "validator_failures": dict(m["validator_failures"]),
            "tools": dict(m["tools"]), "agent_steps": dict(m["agent_steps"]),
            "agent_failures": dict(m["agent_failures"]),
            "llm_calls": m["llm_calls"], "tokens": m["tokens"],
            "cost_usd": str(m["cost_usd"]),
            "cost_usd_per_run": str((m["cost_usd"] / m["runs"]).quantize(Decimal("0.000001"))),
            "transfers": m["transfers"], "escalations": dict(m["escalations"]),
        }
    return out


def _detail(e: Event) -> str:
    p = e["payload"]
    kind = e["type"]
    if kind == "node_entered":
        return f'{p.get("node_id")} ({p.get("node_type")}, {p.get("resume_kind")})'
    if kind == "command_emitted":
        return f'{p.get("command")} flow={p.get("flow")} above_threshold={p.get("above_threshold")}'
    if kind == "decision_made":
        return f'{(p.get("model") or {}).get("id")} via {p.get("provider_used")} {p.get("value")}'
    if kind == "tool_called":
        return f'{(p.get("tool") or {}).get("id")} -> {p.get("status")} {p.get("error") or ""}'
    if kind == "agent_step":
        return f'{p.get("kind")} {(p.get("tool") or {}).get("id") or ""} {p.get("error_kind") or ""}'
    if kind == "response_emitted":
        failures = (p.get("validator") or {}).get("failures")
        return f'{p.get("kind")} fallback={p.get("fallback_used")} validator_failures={failures}'
    if kind == "turn_completed":
        return f'{p.get("duration_ms")} ms awaiting={p.get("awaiting")} degraded={p.get("degraded")}'
    if kind == "run_closed":
        return f'outcome={p.get("outcome")}'
    return ""


def timeline(events: Iterable[Event]) -> list[str]:
    """Una línea por evento: el orden en que el motor decidió, sin contenido de la conversación."""
    return [f'{e["seq"]:>4} {e["ts"][11:19]} {e["type"]:<22} {_detail(e)}'.rstrip() for e in events]


def load(dsn: str, run_id: str | None = None) -> list[dict[str, Any]]:
    import psycopg  # solo aquí: `compute` y `timeline` no necesitan base

    query = "SELECT event_json FROM audit_events"
    params: tuple[str, ...] = ()
    if run_id:
        query, params = query + " WHERE run_id = %s", (run_id,)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(query + " ORDER BY run_id, seq", params)
        return [json.loads(row[0]) for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="report", description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--dsn", default=os.environ.get("AGENTCORE_REGISTRY_DSN"))
    parser.add_argument("--agent", default=None)
    parser.add_argument("--run", default=None, help="línea de tiempo de un run")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.dsn:
        print("falta --dsn o AGENTCORE_REGISTRY_DSN", file=sys.stderr)
        return 2
    events = load(args.dsn, args.run)
    if args.run:
        print("\n".join(timeline(events)))
        return 0
    summary = summarize(compute(events))
    if args.agent:
        summary = {k: v for k, v in summary.items() if k == args.agent}
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    for agent, metrics in summary.items():
        print(f"== {agent}")
        for key, value in metrics.items():
            print(f"   {key:<26} {value}")
    if not summary:
        print("sin runs: ¿conversaste ya con algún agente?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
