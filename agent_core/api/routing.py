"""Rutas que leen el body con `domain.loads` (regla dura 4): los decimales llegan como `Decimal`."""

import json
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from agent_core.domain import dumps, loads


class _DecimalRequest(Request):
    async def json(self) -> Any:
        raw = await self.body()
        try:
            return loads(raw)
        except json.JSONDecodeError:
            raise
        except ValueError as exc:  # NaN, claves duplicadas, anidación excesiva: mismo 422 que un JSON roto
            raise json.JSONDecodeError(str(exc), raw.decode("utf-8", "replace"), 0) from None


class DecimalJsonRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            return await handler(_DecimalRequest(request.scope, request.receive))

        return route_handler


class JsonResponse(Response):
    """JSON con `Decimal` como número exacto (`dumps` de M0); nunca `float`."""

    media_type = "application/json"

    def __init__(self, content: object, status_code: int = 200) -> None:
        super().__init__(dumps(content), status_code=status_code)
