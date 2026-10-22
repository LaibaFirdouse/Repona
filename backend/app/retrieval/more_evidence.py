"""Fetch additional spans when the model requests more evidence."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.catalog import IndexGeneration
from app.retrieval.chunk_selection import implementation_chunk_choices
from app.retrieval.fusion import ChunkChoice
from app.retrieval.overview_boost import overview_chunk_choices
from app.retrieval.chunk_selection import is_meta_config_path
from app.retrieval.query_terms import build_lexical_queries, extract_search_terms
from app.retrieval.workflow_boost import lexical_workflow_chunk_choices


def _merge_choices(primary: list[ChunkChoice], extra: list[ChunkChoice], limit: int) -> list[ChunkChoice]:
    seen = {item.chunk_id for item in primary}
    merged = list(primary)
    for item in extra:
        if item.chunk_id in seen or len(merged) >= limit:
            continue
        seen.add(item.chunk_id)
        merged.append(item)
    return merged[:limit]


def fetch_more_evidence_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    question: str,
    more_evidence: dict,
    prefer_implementation: bool,
    allow_overview: bool,
    limit: int,
) -> list[ChunkChoice]:
    """Code-oriented fetch first; overview only when appropriate (#8)."""
    lexical = [str(item).strip() for item in (more_evidence.get("lexical_queries") or []) if str(item).strip()]
    paths = [str(item).strip() for item in (more_evidence.get("path_contains") or []) if str(item).strip()]
    if not lexical:
        lexical = build_lexical_queries(question)

    choices: list[ChunkChoice] = []
    if prefer_implementation:
        choices = _merge_choices(
            choices,
            lexical_workflow_chunk_choices(db, generation, lexical, limit=limit),
            limit,
        )
        choices = _merge_choices(
            choices,
            implementation_chunk_choices(db, generation, question=question, limit=limit),
            limit,
        )
    if paths:
        choices = _merge_choices(
            choices,
            overview_chunk_choices(
                db,
                generation,
                extra_lexical=lexical,
                path_contains=paths,
                limit=min(limit, 16),
            ),
            limit,
        )
    if allow_overview or not choices:
        choices = _merge_choices(
            choices,
            overview_chunk_choices(
                db,
                generation,
                extra_lexical=lexical,
                path_contains=paths,
                limit=limit,
            ),
            limit,
        )
    return choices


def more_evidence_for_audit_recovery(question: str, loaded: list[dict]) -> dict:
    """Synthetic need_evidence payload when the model only returned retrieval-meta prose."""
    lexical = build_lexical_queries(question)[:4]
    for term in extract_search_terms(question):
        if term not in lexical and len(term) >= 3:
            lexical.append(term)
    path_contains: list[str] = []
    seen_paths: set[str] = set()
    for span in loaded:
        raw = str(span.get("relative_path") or "").replace("\\", "/")
        if not raw or is_meta_config_path(raw):
            continue
        if raw not in seen_paths:
            seen_paths.add(raw)
            path_contains.append(raw)
        name = raw.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if stem and stem.lower() not in seen_paths and len(stem) >= 3:
            seen_paths.add(stem.lower())
            path_contains.append(stem)
        if len(path_contains) >= 6:
            break
    return {
        "note": "Replacing a retrieval-meta reply with fuller source excerpts for this question.",
        "lexical_queries": lexical[:4],
        "path_contains": path_contains[:6],
    }


def fetch_audit_recovery_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    question: str,
    loaded: list[dict],
    prefer_implementation: bool,
    allow_overview: bool,
    limit: int,
    intent: str = "conceptual_search",
    trace_anchors_enabled: bool = True,
    graph_session=None,
    graph_hops: int = 0,
) -> list[ChunkChoice]:
    """Extra chunks after audit-only model output on a thin pack."""
    more = more_evidence_for_audit_recovery(question, loaded)
    choices = fetch_more_evidence_choices(
        db,
        generation,
        question=question,
        more_evidence=more,
        prefer_implementation=prefer_implementation,
        allow_overview=allow_overview,
        limit=limit,
    )
    if trace_anchors_enabled:
        from app.retrieval.trace_anchors import catalog_trace_anchor_choices, question_wants_trace_anchors

        if question_wants_trace_anchors(question, intent):
            anchors = catalog_trace_anchor_choices(
                db,
                generation,
                question,
                limit=min(8, limit),
                graph_session=graph_session,
                graph_hops=graph_hops,
            )
            choices = _merge_choices(anchors, choices, limit)
    if len(choices) < max(4, limit // 3):
        choices = _merge_choices(
            choices,
            empty_pack_fallback_choices(
                db,
                generation,
                question=question,
                prefer_implementation=prefer_implementation,
                allow_overview=allow_overview,
                limit=limit,
            ),
            limit,
        )
    return choices[:limit]


def empty_pack_fallback_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    question: str,
    prefer_implementation: bool,
    allow_overview: bool,
    limit: int,
) -> list[ChunkChoice]:
    """Tiered fallback when primary retrieval produced no packable spans (#9)."""
    if prefer_implementation or not allow_overview:
        queries = build_lexical_queries(question)
        hits = lexical_workflow_chunk_choices(db, generation, queries, limit=limit)
        if hits:
            return hits
        impl = implementation_chunk_choices(db, generation, question=question, limit=limit)
        if impl:
            return impl
    if allow_overview:
        return overview_chunk_choices(db, generation, limit=limit)
    return []
