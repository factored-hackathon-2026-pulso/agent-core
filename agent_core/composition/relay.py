"""`agentcore relay`: publica el outbox en el bus de eventos (ADR 0023).

`--once` hace una pasada y sale (para pruebas y tareas programadas). Sin él corre en bucle como servicio: un
candado asesor de Postgres deja a un solo relay activo (los demás esperan y toman el relevo si cae) y SIGTERM
termina la pasada en curso antes de salir, como pide ECS al parar una tarea."""

import argparse
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import boto3
import psycopg

from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.adapters.sns_publisher import SnsEventPublisher
from agent_core.ports import EventPublisher, Outbox
from agent_core.relay import OutboxRelay

DSN_ENV = "AGENTCORE_REGISTRY_DSN"
TOPIC_ENV = "AGENTCORE_EVENTS_TOPIC_ARN"
LOCK_KEY = 6_161_616_161  # clave del candado asesor del relay (entero de 64 bits con signo)

Leader = Callable[[], Any]  # contexto que entrega `True` si esta instancia es la líder


def add_relay_parser(sub: Any) -> None:
    relay = sub.add_parser("relay", help="publica el outbox en el bus de eventos (SNS)")
    relay.add_argument("--once", action="store_true", help="una sola pasada y sale")
    relay.add_argument("--interval", type=float, default=2.0, help="segundos entre pasadas (por defecto 2)")
    relay.add_argument("--batch", type=int, default=100, help="mensajes por lote (por defecto 100)")
    relay.add_argument("--dsn", default=None, help=f"DSN de Postgres (o {DSN_ENV}); no se imprime nunca")


@contextmanager
def advisory_leader(dsn: str) -> Iterator[bool]:
    """`True` si esta conexión tomó el candado; lo conserva mientras dure el contexto."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        row = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()
        yield bool(row and row[0])


def run_relay(args: argparse.Namespace, env: Mapping[str, str], *, publisher: EventPublisher | None = None,
              outbox: Outbox | None = None, leader: Leader | None = None,
              stop: threading.Event | None = None) -> int:
    dsn = args.dsn or env.get(DSN_ENV)
    topic = env.get(TOPIC_ENV, "").strip()
    if args.batch < 1 or args.interval < 0:
        print("--batch debe ser positivo y --interval no negativo", file=sys.stderr)
        return 2
    if outbox is None and not dsn:
        print(f"agentcore relay necesita --dsn (o {DSN_ENV})", file=sys.stderr)
        return 2
    if publisher is None and not topic:
        print(f"agentcore relay necesita {TOPIC_ENV}", file=sys.stderr)
        return 2
    pub = publisher or SnsEventPublisher(boto3.client("sns"), topic)
    box = outbox or PostgresStore(dsn or "").outbox()
    relay = OutboxRelay(box, pub, batch=args.batch)
    try:
        if args.once:
            return _report(relay)
        return _serve(relay, args.interval, leader or (lambda: advisory_leader(dsn or "")),
                      stop or _sigterm_event())
    except psycopg.Error as error:  # el mensaje de psycopg puede traer el host o la contraseña
        print(f"relay: falla de Postgres ({type(error).__name__})", file=sys.stderr)
        return 1


def _report(relay: OutboxRelay) -> int:
    report = relay.run_once()
    print(f"published={report.published} failed={report.failed} unknown={report.unknown}")
    return 1 if report.failed else 0


def _sigterm_event() -> threading.Event:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def _serve(relay: OutboxRelay, interval: float, leader: Leader, stop: threading.Event) -> int:
    while not stop.is_set():
        with leader() as is_leader:
            while is_leader and not stop.is_set():
                report = relay.run_once()
                if report.published or report.failed or report.unknown:
                    print(f"published={report.published} failed={report.failed} unknown={report.unknown}",
                          flush=True)
                stop.wait(interval)
        if not stop.is_set():
            stop.wait(max(interval, 1.0))  # no líder: espera y vuelve a intentar el candado
    return 0
