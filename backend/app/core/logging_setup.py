"""Configure process-wide logging to stdout and the in-memory server log buffer."""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    from app.core.config import settings
    from app.core.log_buffer import LogBufferHandler, get_log_buffer

    level = getattr(logging, settings.server_log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    root = logging.getLogger()
    if settings.server_logs_enabled:
        if not any(getattr(handler, "_repona_server_log", False) for handler in root.handlers):
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            stream_handler.setLevel(level)
            stream_handler._repona_server_log = True  # type: ignore[attr-defined]

            buffer_handler = LogBufferHandler(get_log_buffer())
            buffer_handler.setFormatter(formatter)
            buffer_handler.setLevel(level)
            buffer_handler._repona_server_log = True  # type: ignore[attr-defined]

            root.addHandler(stream_handler)
            root.addHandler(buffer_handler)

        root.setLevel(level)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).setLevel(level)

    logging.getLogger("app.startup").info(
        "server_logging enabled=%s retention_hours=%s buffer_max=%s level=%s",
        settings.server_logs_enabled,
        settings.server_log_retention_hours,
        settings.server_log_buffer_max,
        settings.server_log_level,
    )
    _CONFIGURED = True
