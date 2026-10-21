"""Fetch additional spans when the model requests more evidence."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.catalog import IndexGeneration
from app.retrieval.chunk_selection import implementation_chunk_choices
from app.retrieval.fusion import ChunkChoice
from app.retrieval.overview_boost import overview_chunk_choices
from app.retrieval.query_terms import build_lexical_queries
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
            implementation_chunk_choices(db, generation, limit=limit),
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
        impl = implementation_chunk_choices(db, generation, limit=limit)
        if impl:
            return impl
    if allow_overview:
        return overview_chunk_choices(db, generation, limit=limit)
    return []
