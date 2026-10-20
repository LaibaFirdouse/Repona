import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import logs as logs_route
from app.core.log_buffer import LogBuffer, LogBufferHandler, get_log_buffer
from app.middleware.request_logging import RequestLoggingMiddleware


class LogBufferTests(unittest.TestCase):
    def test_entries_older_than_retention_are_dropped(self) -> None:
        buffer = LogBuffer(max_entries=100, retention_hours=24)
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="stale",
            args=(),
            exc_info=None,
        )
        record.created = old.timestamp()
        buffer.append(record)
        self.assertEqual(buffer.count(), 0)

    def test_fetch_respects_min_level(self) -> None:
        buffer = LogBuffer(max_entries=100, retention_hours=24)
        for level, message in ((logging.DEBUG, "dbg"), (logging.INFO, "inf"), (logging.ERROR, "err")):
            record = logging.LogRecord(
                name="test",
                level=level,
                pathname=__file__,
                lineno=1,
                msg=message,
                args=(),
                exc_info=None,
            )
            buffer.append(record)
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
