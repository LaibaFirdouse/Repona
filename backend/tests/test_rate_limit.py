import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.middleware.rate_limit import RateLimitMiddleware, client_ip


class ClientIpTests(unittest.TestCase):
    def test_forwarded_header_is_used_when_proxy_is_trusted(self) -> None:
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.1")],
            "client": ("127.0.0.1", 1234),
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "server": ("test", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
        request = Request(scope)
        settings = SimpleNamespace(rate_limit_trust_proxy=True)
        with patch("app.middleware.rate_limit.settings", settings):
            self.assertEqual(client_ip(request), "203.0.113.10")

    def test_socket_address_is_used_without_trusted_proxy(self) -> None:
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.10")],
            "client": ("198.51.100.4", 1234),
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "server": ("test", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
        request = Request(scope)
        settings = SimpleNamespace(rate_limit_trust_proxy=False)
        with patch("app.middleware.rate_limit.settings", settings):
            self.assertEqual(client_ip(request), "198.51.100.4")


class RateLimitMiddlewareTests(unittest.TestCase):
    def test_ask_requests_are_limited_per_ip(self) -> None:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.post("/api/v1/ask")
        def ask() -> dict[str, str]:
            return {"status": "ok"}

        settings = SimpleNamespace(
            rate_limit_enabled=True,
            rate_limit_trust_proxy=False,
            rate_limit_ask_per_minute=2,
            rate_limit_index_per_hour=4,
            api_v1_prefix="/api/v1",
        )
        with patch("app.middleware.rate_limit.settings", settings):
            client = TestClient(app)
            self.assertEqual(client.post("/api/v1/ask").status_code, 200)
            self.assertEqual(client.post("/api/v1/ask").status_code, 200)
            blocked = client.post("/api/v1/ask")
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)


if __name__ == "__main__":
    unittest.main()
