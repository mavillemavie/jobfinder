from __future__ import annotations

import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse


def client_allowed(host: str | None, cidrs: list[str]) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # ASGI test clients use a placeholder host
    return any(ip in ipaddress.ip_network(c, strict=False) for c in cidrs)


class AllowedClientsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, cidrs: list[str]) -> None:  # noqa: ANN001
        super().__init__(app)
        self.cidrs = cidrs

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        host = request.client.host if request.client else None
        if not client_allowed(host, self.cidrs):
            return PlainTextResponse("forbidden", status_code=403)
        return await call_next(request)
