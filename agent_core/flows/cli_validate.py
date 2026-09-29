"""Lógica de `agentcore validate` (M1 §3.12). Sin I/O de consola: devuelve el código y el texto."""

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from agent_core.flows.registry import load_registry
from agent_core.flows.validate import validate_registry
from agent_core.flows.violations import Violation, sort_violations

# Violaciones que se imprimen; el resto se resume ("y N más"). El código de salida cuenta todas.
MAX_PRINTED = 500


def format_violation(v: Violation) -> str:
    return f"{v.rule} {v.flow or '-'} {v.node_id or '-'} {v.path or '-'}: {v.message}"


def format_report(violations: Sequence[Violation], *, as_json: bool) -> str:
    """Informe determinista de una lista ya ordenada; imprime a lo sumo `MAX_PRINTED` violaciones."""
    counts = dict(sorted(Counter(v.rule for v in violations).items()))
    shown = violations[:MAX_PRINTED]
    omitted = len(violations) - len(shown)
    if as_json:
        payload: dict[str, object] = {
            "format": 1,
            "ok": not violations,
            "violations": [v.model_dump() for v in shown],
            "counts": counts,
        }
        if omitted:
            payload["omitted"] = omitted
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [format_violation(v) for v in shown]
    if omitted:
        lines.append(f"y {omitted} más")
    if violations:
        lines.append(f"{len(violations)} violaciones: " + ", ".join(f"{r}={n}" for r, n in counts.items()))
    else:
        lines.append("sin violaciones")
    return "\n".join(lines)


def run_validate(root: Path, *, as_json: bool) -> tuple[int, str]:
    """Códigos: 0 sin violaciones, 1 con violaciones, 2 raíz inexistente o ilegible. No lanza."""
    if not root.is_dir():
        return 2, "no existe el directorio del registro"
    try:
        reg, load_violations = load_registry(root)
        violations = sort_violations([*load_violations, *validate_registry(reg)])
    except OSError:
        return 2, "no se pudo leer el directorio del registro"
    return (1 if violations else 0), format_report(violations, as_json=as_json)
