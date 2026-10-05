"""Ejecución de las comprobaciones de `/readyz`: concurrentes, con plazo y sin filtrar detalle."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field

Check = tuple[str, Callable[[], bool]]


@dataclass(frozen=True)
class ReadinessReport:
    """`checks`: nombre -> `ok` o `fail`. `failed`: las requeridas que fallan; `degraded`: las opcionales."""

    checks: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.failed


def _safe(check: Callable[[], bool]) -> bool:
    """Una comprobación que lanza falla: su mensaje puede traer hosts o credenciales y se descarta."""
    try:
        return bool(check())
    except Exception:
        return False


def run_checks(checks: tuple[Check, ...], optional: frozenset[str], timeout_s: float) -> ReadinessReport:
    """Corre todas a la vez; una que no termina en `timeout_s` cuenta como fallida (su hilo se abandona: cada
    comprobación real lleva su propio plazo de conexión)."""
    if not checks:
        return ReadinessReport()
    pool = ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="readyz")
    try:
        futures = {name: pool.submit(_safe, check) for name, check in checks}
        wait(futures.values(), timeout=timeout_s)
        report = ReadinessReport()
        for name, future in futures.items():
            ok = future.done() and future.result()
            report.checks[name] = "ok" if ok else "fail"
            if not ok:
                (report.degraded if name in optional else report.failed).append(name)
        return report
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
