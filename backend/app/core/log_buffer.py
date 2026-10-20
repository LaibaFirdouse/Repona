"""In-memory server log ring (fixed entry cap, no disk persistence)."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_LEVEL_NUM: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def level_number(name: str) -> int:
    return _LEVEL_NUM.get(name.upper(), logging.INFO)


@dataclass(frozen=True)
class StoredLogEntry:
    ts: str
    level: str
    logger: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class LogBuffer:
    def __init__(self, *, max_entries: int) -> None:
        self._max_entries = max(1, max_entries)
        self._lock = threading.Lock()
        self._entries: deque[StoredLogEntry] = deque(maxlen=self._max_entries)

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def append(self, record: logging.LogRecord) -> None:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        entry = StoredLogEntry(
            ts=ts.isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        with self._lock:
            self._entries.append(entry)

    def fetch(
        self,
        *,
        limit: int = 200,
        min_level: str = "DEBUG",
        logger_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        floor = level_number(min_level)
        limit = max(1, min(limit, self._max_entries))
        with self._lock:
            rows = list(self._entries)

        filtered: list[StoredLogEntry] = []
        for entry in rows:
            if level_number(entry.level) < floor:
                continue
            if logger_prefix and not entry.logger.startswith(logger_prefix):
                continue
            filtered.append(entry)

        tail = filtered[-limit:]
        return [item.as_dict() for item in tail]

    def count(self) -> int:
        with self._lock:
            return len(self._entries)


class LogBufferHandler(logging.Handler):
    def __init__(self, buffer: LogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(record)
        except Exception:
            self.handleError(record)


_buffer: LogBuffer | None = None


def get_log_buffer() -> LogBuffer:
    global _buffer
    if _buffer is None:
        from app.core.config import settings

        _buffer = LogBuffer(max_entries=settings.server_log_buffer_max)
    return _buffer
