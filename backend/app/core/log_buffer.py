"""In-memory server log buffer with TTL pruning (no disk persistence)."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    def __init__(self, *, max_entries: int, retention_hours: int) -> None:
        self._max_entries = max(100, max_entries)
        self._retention = timedelta(hours=max(1, retention_hours))
        self._lock = threading.Lock()
        self._entries: deque[tuple[datetime, StoredLogEntry]] = deque()

    @property
    def retention_hours(self) -> int:
        return int(self._retention.total_seconds() // 3600)

    def append(self, record: logging.LogRecord) -> None:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        entry = StoredLogEntry(
            ts=ts.isoformat(),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        with self._lock:
            self._entries.append((ts, entry))
            self._prune_locked(ts)

    def _prune_locked(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = now - self._retention
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()
        while len(self._entries) > self._max_entries:
            self._entries.popleft()

    def fetch(
        self,
        *,
        limit: int = 200,
        min_level: str = "DEBUG",
        logger_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        floor = level_number(min_level)
        limit = max(1, min(limit, 1000))
        with self._lock:
            self._prune_locked()
            rows = [entry for _, entry in self._entries]

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
            self._prune_locked()
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

        _buffer = LogBuffer(
            max_entries=settings.server_log_buffer_max,
            retention_hours=settings.server_log_retention_hours,
        )
    return _buffer
