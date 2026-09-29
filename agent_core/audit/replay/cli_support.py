"""Ayudas del CLI de replay: formato de salida y códigos (decisión 17). No importa el motor."""

from agent_core.audit.replay.report import ReplayReport

EXIT = {"match": 0, "diverged": 1, "chain_broken": 2}
USAGE_ERROR = 3


def render_report(report: ReplayReport, as_json: bool) -> str:
    if as_json:
        return report.model_dump_json()
    line = f"{report.verdict} · {report.mode} · {report.run_id}@{report.release}"
    if report.first_divergence is not None:
        line += f" · primera divergencia en seq {report.first_divergence.event_seq}"
    if report.chain_broken_at is not None:
        line += f" · cadena rota en seq {report.chain_broken_at}"
    return line
