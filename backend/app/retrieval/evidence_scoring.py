"""Rank loaded spans for packing and map planner intent to retrieval budgets."""

from __future__ import annotations

import re

from app.core.config import settings
from app.retrieval.chunk_selection import is_meta_config_path, wants_code_trace
from app.retrieval.conversation_context import QaTurn
from app.retrieval.packet import is_weak_implementation_span
from app.retrieval.query_router import RetrievalRequest
from app.retrieval.query_terms import question_paths, question_qualified_names

_CHUNK_KIND_RANK = {
    "symbol_body": 0,
    "symbol_part": 1,
    "non_code": 2,
    "import_block": 3,
    "file_overview": 4,
}

_IMPORT_EXPORT_QUESTION = re.compile(
    r"\b(import|imports|export|exports|from .+ import|re-?export|module exports?)\b",
    re.I,
)
_DOC_QUESTION = re.compile(
    r"\b(readme|documentation|docs?\b|getting started|install(?:ation)?|setup guide)\b",
    re.I,
)
# Leading underscore is significant (_answer_events); bare [a-z]… misses those names.
_SNAKE_IDENTIFIER = re.compile(r"\b_?[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def question_snake_identifiers(question: str) -> set[str]:
    """Snake_case identifiers from the question, including leading-underscore names."""
    return set(_SNAKE_IDENTIFIER.findall(question or ""))


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


def coalesce_symbol_parts_in_source_order(spans: list[dict]) -> list[dict]:
    """Keep each symbol's body/parts contiguous and ordered by source position."""
    if not spans:
        return []
    by_symbol: dict[str, list[dict]] = {}
    for span in spans:
        symbol_id = str(span.get("symbol_id") or "")
        kind = str(span.get("chunk_kind") or "")
        if symbol_id and kind in {"symbol_body", "symbol_part"}:
            by_symbol.setdefault(symbol_id, []).append(span)
    emitted: set[str] = set()
    result: list[dict] = []
    for span in spans:
        symbol_id = str(span.get("symbol_id") or "")
        kind = str(span.get("chunk_kind") or "")
        if symbol_id and kind in {"symbol_body", "symbol_part"}:
            if symbol_id in emitted:
                continue
            group = sorted(
                by_symbol[symbol_id],
                key=lambda item: (
                    int(item.get("source_start_byte") or 0),
                    int(item.get("start_line") or 0),
                    str(item.get("chunk_id") or ""),
                ),
            )
            result.extend(group)
            emitted.add(symbol_id)
            continue
        result.append(span)
    return result


def sort_loaded_spans(spans: list[dict], *, overview: bool, terms: list[str]) -> list[dict]:
    """Implementation-first order for packing; documentation paths sink when not overview.

    After ranking, coalesce each symbol's parts into source order so oversized
    functions keep entry → mid → return instead of largest-middle-first.
    """
    if not spans:
        return []
    ordered = sorted(spans, key=lambda span: _span_sort_key(span, overview=overview, terms=terms))
    return coalesce_symbol_parts_in_source_order(ordered)


def question_allows_imports_as_primary(question: str) -> bool:
    return bool(_IMPORT_EXPORT_QUESTION.search(question.strip()))


def question_allows_docs_as_primary(question: str) -> bool:
    return bool(_DOC_QUESTION.search(question.strip()))


def prioritize_question_targets(
    spans: list[dict],
    question: str,
    *,
    prefer_implementation: bool,
) -> list[dict]:
    """Reorder loaded spans so the question's named files/symbols lead the pack.

    Does not drop README/imports globally—only sinks them for implementation
    questions when stronger code evidence is present.
    Explicit question-target spans (tagged during named-symbol load) always lead.
    """
    if not spans:
        return []
    named = set(question_qualified_names(question))
    snake_tokens = question_snake_identifiers(question)
    type_prefixes: tuple[str, ...] = tuple(
        f"{token}." for token in named if "." not in token and token[:1].isupper()
    )
    # Class→member boost only when the question did not already name specific snake_case symbols.
    # Otherwise AnswerService.* floods the pack and exact callees (fuse_anchors) are dropped.
    if not snake_tokens:
        for token in re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", question):
            prefix = f"{token}."
            if prefix not in type_prefixes:
                type_prefixes = (*type_prefixes, prefix)
            named.add(token)
    paths = [path.lower() for path in question_paths(question)]
    allow_imports = question_allows_imports_as_primary(question)
    allow_docs = question_allows_docs_as_primary(question) and not prefer_implementation

    def rank(span: dict) -> tuple:
        path = str(span.get("relative_path") or "")
        path_l = path.replace("\\", "/").lower()
        qname = str(span.get("qualified_name") or "")
        leaf = qname.rsplit(".", 1)[-1] if qname else ""
        kind = str(span.get("chunk_kind") or "")
        # Bare class/type names (AnswerService) must not outrank Class.member bodies—
        # otherwise multipart class shells consume the pack before methods pack.
        snake_or_qualified = bool(qname) and (
            leaf in snake_tokens or ("." in qname and qname in named)
        )
        member_hit = bool(qname) and any(qname.startswith(prefix) for prefix in type_prefixes)
        bare_type_hit = bool(qname) and "." not in qname and qname in named
        if span.get("question_target") or snake_or_qualified:
            named_hit = 0
        elif member_hit:
            named_hit = 1
        elif bare_type_hit:
            named_hit = 2
        else:
            named_hit = 3
        path_hit = 0 if any(hint in path_l for hint in paths) else 1
        # Documentation questions: README/docs lead. Implementation questions: sink weak spans.
        if allow_docs and is_documentation_path(path):
            doc_rank = 0
        elif allow_docs:
            doc_rank = 1
        else:
            doc_rank = 0
        weak = is_weak_implementation_span(span)
        if prefer_implementation and weak and not allow_imports and not allow_docs:
            weak_rank = 2
        elif prefer_implementation and kind in {"import_block", "file_overview"} and not allow_imports:
            weak_rank = 2
        else:
            weak_rank = 1 if weak else 0
        kind_rank = _CHUNK_KIND_RANK.get(kind, 9)
        member_rank = named_hit
        return (named_hit, member_rank, path_hit, doc_rank, weak_rank, kind_rank)

    return coalesce_symbol_parts_in_source_order(sorted(spans, key=rank))


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
