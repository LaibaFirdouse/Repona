"""Fixed-window rate limits keyed by client IP."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        rule = _rule_for_path(request.url.path)
        if rule is None:
            return await call_next(request)

        limit, window = rule
        client = client_ip(request)
        key = (client, request.url.path)
        now = time.monotonic()
        retry_after = self._consume(key, limit, window, now)
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": "too many requests from this address"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    def _consume(self, key: tuple[str, str], limit: int, window: float, now: float) -> int | None:
        cutoff = now - window
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(1, int(bucket[0] + window - now))
            bucket.append(now)
            return None


def client_ip(request: Request) -> str:
    if settings.rate_limit_trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "unknown"
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def _rule_for_path(path: str) -> tuple[int, float] | None:
    prefix = settings.api_v1_prefix.rstrip("/")
    ask = f"{prefix}/ask"
    repository = f"{prefix}/repository"
    if path == ask:
        return settings.rate_limit_ask_per_minute, 60.0
    if path == repository:
        return settings.rate_limit_index_per_hour, 3600.0
    return None
