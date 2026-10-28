"""Opt-in evidence-pipeline diagnostics for local debugging (no full source in logs)."""

from __future__ import annotations

from typing import Any


def evidence_debug_enabled(trace: dict | None) -> bool:
    if trace is None:
        return False
    return bool(trace.get("evidence_debug") or trace.get("record_steps"))


def _meta(span: Any) -> dict[str, Any]:
    if isinstance(span, dict):
        return {
            "chunk_id": span.get("chunk_id"),
            "relative_path": span.get("relative_path"),
            "symbol_id": span.get("symbol_id"),
            "qualified_name": span.get("qualified_name"),
            "chunk_kind": span.get("chunk_kind") or span.get("kind"),
            "ordinal": span.get("ordinal"),
            "start_line": span.get("start_line"),
            "end_line": span.get("end_line"),
            "raw_bytes": span.get("raw_bytes"),
            "score": span.get("score"),
            "retrieval_rank": span.get("retrieval_rank"),
        }
    return {
        "chunk_id": getattr(span, "chunk_id", None),
        "anchor_id": getattr(span, "anchor_id", None),
        "chunk_kind": getattr(span, "kind", None),
        "ordinal": getattr(span, "ordinal", None),
    }


def record_stage(trace: dict | None, stage: str, payload: dict[str, Any]) -> None:
    if not evidence_debug_enabled(trace):
        return
    bucket = trace.setdefault("evidence_pipeline", {})
    bucket[stage] = payload


def record_operation(
    trace: dict | None,
    *,
    operation: str,
    before: Any,
    after: Any,
    reason: str,
) -> None:
    if not evidence_debug_enabled(trace):
        return
    ops = trace.setdefault("evidence_pipeline", {}).setdefault("operations", [])
    ops.append(
        {
            "operation": operation,
            "reason": reason,
            "before": _meta(before) if before is not None else None,
            "after": _meta(after) if after is not None else None,
        }
    )


def summarize_choices(choices: list[Any], *, limit: int = 48) -> list[dict[str, Any]]:
    return [_meta(item) for item in choices[:limit]]


def summarize_loaded(spans: list[dict], *, limit: int = 48) -> list[dict[str, Any]]:
    return [_meta(span) for span in spans[:limit]]
