"""Tope de tamaño del body (M9 §3.1): nunca se lee más de `max_bytes`, con o sin `Content-Length`."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_core.api.problems import problem
from agent_core.domain import ProblemCode

DEFAULT_MAX_BODY_BYTES = 1_048_576  # 1 MiB: un borrador del registry admite entidades de hasta 256 KiB


class BodyLimit:
    """ASGI: un `Content-Length` mayor que el tope se rechaza sin leer el body. Sin él (chunked), el body se
    lee aquí hasta el tope y se entrega entero a la app; si se pasa, `413` sin llegar a la ruta. (FastAPI
    convierte en `400` cualquier excepción al leer el body, por eso no se corta desde `receive`.)"""

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes debe ser positivo")
        self._app = app
        self._max = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None:
            if declared > self._max:
                await self._reject(scope, receive, send)
                return
            await self._app(scope, receive, send)  # el servidor no entrega más de lo declarado
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # el cliente se fue: que la app lo vea igual
                await self._app(scope, _replay(chunks, message, receive), send)
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self._max:
                await self._reject(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        await self._app(scope, _replay(chunks, None, receive), send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        trace_id = str(scope.get("state", {}).get("trace_id", ""))
        await problem(ProblemCode.payload_too_large, "", trace_id)(scope, receive, send)


def _replay(chunks: list[bytes], tail: Message | None, rest: Receive) -> Receive:
    """Un `receive` que entrega el body ya leído en un solo mensaje y, después, lo que quedaba (p. ej. el
    `http.disconnect`), y luego espera en el `receive` original."""
    pending: list[Message] = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]
    if tail is not None:
        pending = [tail]

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return await rest()

    return receive


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
