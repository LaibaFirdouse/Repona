"""Rank loaded spans for packing and map planner intent to retrieval budgets."""

from __future__ import annotations

from app.core.config import settings
from app.retrieval.chunk_selection import is_meta_config_path, wants_code_trace
from app.retrieval.conversation_context import QaTurn
from app.retrieval.query_router import RetrievalRequest

_CHUNK_KIND_RANK = {
    "symbol_body": 0,
    "symbol_part": 1,
    "non_code": 2,
    "import_block": 3,
    "file_overview": 4,
}


def is_documentation_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/")
    lower = path.lower()
    if is_meta_config_path(path):
        return True
    return lower.endswith(".md") or lower.endswith(".markdown") or lower.endswith(".rst")


def _span_sort_key(span: dict, *, overview: bool, terms: list[str]) -> tuple:
    path = str(span.get("relative_path") or "")
    kind = str(span.get("chunk_kind") or "")
    kind_rank = _CHUNK_KIND_RANK.get(kind, 9)
    doc_rank = 0 if overview or not is_documentation_path(path) else 1
    text = str(span.get("text") or "").lower()
    term_hits = 0
    for term in terms:
        token = term.strip().lower()
        if len(token) >= 3 and token in text:
            term_hits += 1
    raw_bytes = int(span.get("raw_bytes") or 0)
    return (doc_rank, kind_rank, -term_hits, -raw_bytes, path)


def sort_loaded_spans(spans: list[dict], *, overview: bool, terms: list[str]) -> list[dict]:
    """Implementation-first order for packing; documentation paths sink when not overview."""
    if not spans:
        return []
    return sorted(spans, key=lambda span: _span_sort_key(span, overview=overview, terms=terms))


def intent_span_bonus(
    retrieval: RetrievalRequest,
    *,
    overview_question: bool,
    code_first: bool,
    prefer_impl: bool,
) -> int:
    if code_first or prefer_impl:
        return settings.answer_overview_span_bonus + settings.answer_workflow_span_bonus
    if retrieval.needs_source_bodies and retrieval.intent in {"execution_trace", "debugging"}:
        return settings.answer_overview_span_bonus
    if overview_question or retrieval.intent in {"architecture_question", "conceptual_search"}:
        return settings.answer_overview_span_bonus
    return 0


def intent_per_file_cap(
    retrieval: RetrievalRequest,
    *,
    code_first: bool,
    prefer_impl: bool,
) -> int:
    if code_first or prefer_impl:
        return settings.answer_impl_chunks_per_file
    if retrieval.needs_source_bodies and retrieval.intent in {"execution_trace", "debugging"}:
        return max(3, min(settings.answer_impl_chunks_per_file, 4))
    if retrieval.needs_source_bodies and retrieval.intent == "architecture_question":
        return 3
    return 2


def resolve_span_limit(
    retrieval: RetrievalRequest,
    *,
    overview_question: bool,
    code_first: bool,
    prefer_impl: bool,
) -> tuple[int, int]:
    """Return (span_limit, max_chunks_per_file) from settings and planner intent."""
    limit = settings.retrieval_span_limit + intent_span_bonus(
        retrieval,
        overview_question=overview_question,
        code_first=code_first,
        prefer_impl=prefer_impl,
    )
    per_file = intent_per_file_cap(retrieval, code_first=code_first, prefer_impl=prefer_impl)
    return limit, per_file


def retrieval_prefers_implementation(
    question: str,
    history: list[QaTurn],
    *,
    retrieval: RetrievalRequest,
    preview_mostly_non_body: bool,
    code_first: bool,
    substantive: bool,
    overview_question: bool = False,
) -> bool:
    """When to run implementation boosts, sibling expansion, and thin-pack reload."""
    if code_first or substantive:
        return True
    if not retrieval.needs_source_bodies:
        return False
    if overview_question:
        return False
    if retrieval.intent in {"execution_trace", "debugging"}:
        return True
    if preview_mostly_non_body:
        return True
    return wants_code_trace(question, history)


def pack_overview_mode(retrieval: RetrievalRequest, *, overview_question: bool) -> bool:
    """True when documentation-heavy spans should sort ahead (product/overview answers)."""
    if overview_question:
        return True
    if retrieval.intent == "conceptual_search" and not retrieval.needs_source_bodies:
        return True
    return False
