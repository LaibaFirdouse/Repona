"""HTTP access logging for the API (method, path, status, duration)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.middleware.rate_limit import client_ip

logger = logging.getLogger("app.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.server_logs_enabled:
            return await call_next(request)

        path = request.url.path
        if path == f"{settings.api_v1_prefix.rstrip('/')}/health":
            return await call_next(request)

        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "request method=%s path=%s status=%s duration_ms=%.1f client=%s",
                request.method,
                path,
                status,
                duration_ms,
                client_ip(request),
            )
