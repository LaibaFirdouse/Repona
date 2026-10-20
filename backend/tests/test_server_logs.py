import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import logs as logs_route
from app.core.log_buffer import LogBuffer, LogBufferHandler, get_log_buffer
from app.middleware.request_logging import RequestLoggingMiddleware


def _record(message: str, *, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class LogBufferTests(unittest.TestCase):
    def test_only_latest_entries_are_kept(self) -> None:
        buffer = LogBuffer(max_entries=3)
        for index in range(5):
            buffer.append(_record(f"line-{index}"))
        self.assertEqual(buffer.count(), 3)
        rows = buffer.fetch(limit=10, min_level="DEBUG")
        self.assertEqual([row["message"] for row in rows], ["line-2", "line-3", "line-4"])

    def test_fetch_respects_min_level(self) -> None:
        buffer = LogBuffer(max_entries=100)
        for level, message in ((logging.DEBUG, "dbg"), (logging.INFO, "inf"), (logging.ERROR, "err")):
            buffer.append(_record(message, level=level))
        rows = buffer.fetch(limit=10, min_level="INFO")
        messages = [row["message"] for row in rows]
        self.assertEqual(messages, ["inf", "err"])


class LogsRouteTests(unittest.TestCase):
    def test_logs_endpoint_returns_buffered_entries(self) -> None:
        buffer = get_log_buffer()
        handler = LogBufferHandler(buffer)
        logger = logging.getLogger("test.server_logs_route")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("hello-from-test")
            app = FastAPI()
            app.include_router(logs_route.router, prefix="/api/v1")
            settings = SimpleNamespace(
                server_logs_enabled=True,
                server_logs_token="",
                api_v1_prefix="/api/v1",
            )
            with patch("app.api.v1.routes.logs.settings", settings):
                client = TestClient(app)
                response = client.get("/api/v1/logs?limit=50&level=INFO")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertGreaterEqual(payload["buffered_total"], 1)
            self.assertEqual(payload["buffer_max"], buffer.max_entries)
            self.assertTrue(any(entry["message"] == "hello-from-test" for entry in payload["entries"]))
        finally:
            logger.removeHandler(handler)

    def test_logs_endpoint_requires_token_when_configured(self) -> None:
        app = FastAPI()
        app.include_router(logs_route.router, prefix="/api/v1")
        settings = SimpleNamespace(server_logs_enabled=True, server_logs_token="secret")
        with patch("app.api.v1.routes.logs.settings", settings):
            client = TestClient(app)
            blocked = client.get("/api/v1/logs")
            self.assertEqual(blocked.status_code, 401)
            ok = client.get("/api/v1/logs", headers={"X-Server-Logs-Token": "secret"})
            self.assertEqual(ok.status_code, 200)


class RequestLoggingMiddlewareTests(unittest.TestCase):
    def test_request_line_is_logged(self) -> None:
        buffer = get_log_buffer()
        handler = LogBufferHandler(buffer)
        request_logger = logging.getLogger("app.request")
        request_logger.addHandler(handler)
        request_logger.setLevel(logging.INFO)

        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)

        @app.get("/api/v1/demo")
        def demo() -> dict[str, str]:
            return {"ok": "1"}

        settings = SimpleNamespace(
            server_logs_enabled=True,
            api_v1_prefix="/api/v1",
            rate_limit_trust_proxy=False,
        )
        try:
            with patch("app.middleware.request_logging.settings", settings):
                client = TestClient(app)
                self.assertEqual(client.get("/api/v1/demo").status_code, 200)
            rows = buffer.fetch(limit=20, min_level="INFO", logger_prefix="app.request")
            self.assertTrue(any("path=/api/v1/demo" in row["message"] for row in rows))
        finally:
            request_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
