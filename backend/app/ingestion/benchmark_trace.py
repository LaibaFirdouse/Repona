"""Optional step timing for ingestion benchmarks (ContextVar, off by default)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Iterator

_steps: ContextVar[list[dict[str, Any]] | None] = ContextVar("ingestion_benchmark_steps", default=None)


def active_steps() -> list[dict[str, Any]] | None:
    return _steps.get()


def record_step(name: str, started: float, **extra: Any) -> None:
    steps = _steps.get()
    if steps is None:
        return
    row: dict[str, Any] = {"step": name, "ms": round((perf_counter() - started) * 1000, 2)}
    row.update(extra)
    steps.append(row)


@contextmanager
def ingestion_benchmark_trace() -> Iterator[list[dict[str, Any]]]:
    bucket: list[dict[str, Any]] = []
    token = _steps.set(bucket)
    try:
        yield bucket
    finally:
        _steps.reset(token)
