"""Tope de peticiones simultáneas (brief A3): pasadas las N, 503 con `Retry-After`; las sondas no cuentan."""

import asyncio
from typing import Any

import pytest

from agent_core.api.inflight import InflightLimit


async def _call(app: Any, path: str = "/v1/x") -> tuple[int, dict[bytes, bytes]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app({"type": "http", "path": path, "headers": [], "state": {}}, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], dict(start["headers"])


def _gate_app() -> tuple[Any, asyncio.Event, asyncio.Event]:
    entered, release = asyncio.Event(), asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        if scope["path"].startswith("/v1"):
            entered.set()
            await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app, entered, release


def test_the_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        InflightLimit(lambda *a: None, 0)  # type: ignore[arg-type,return-value]


def test_over_the_limit_answers_503_with_retry_after_and_never_reaches_the_app() -> None:
    asyncio.run(_test_over_the_limit_answers_503_with_retry_after_and_never_reaches_the_app())


async def _test_over_the_limit_answers_503_with_retry_after_and_never_reaches_the_app() -> None:
    app, entered, release = _gate_app()
    limited = InflightLimit(app, 1)
    first = asyncio.create_task(_call(limited))
    await entered.wait()

    status, headers = await _call(limited)
    release.set()

    assert status == 503 and headers[b"retry-after"] == b"1"
    assert (await first)[0] == 200


def test_the_slot_is_released_when_the_request_finishes_even_if_the_app_fails() -> None:
    asyncio.run(_test_the_slot_is_released_when_the_request_finishes_even_if_the_app_fails())


async def _test_the_slot_is_released_when_the_request_finishes_even_if_the_app_fails() -> None:
    async def boom(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("x")

    limited = InflightLimit(boom, 1)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await _call(limited)

    async def ok(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    assert (await _call(InflightLimit(ok, 1)))[0] == 200


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/version"])
def test_probes_are_never_limited_nor_counted(path: str) -> None:
    asyncio.run(_test_probes_are_never_limited_nor_counted(path))


async def _test_probes_are_never_limited_nor_counted(path: str) -> None:
    app, entered, release = _gate_app()
    limited = InflightLimit(app, 1)
    first = asyncio.create_task(_call(limited))
    await entered.wait()

    status, _ = await _call(limited, path)
    release.set()
    await first

    assert status == 200
