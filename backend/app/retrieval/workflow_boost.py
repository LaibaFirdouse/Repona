"""Lexical boosts for workflow / implementation questions (terms come from the question only)."""

from __future__ import annotations

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.catalog import Chunk, GenerationChunk, IndexGeneration
from app.core.config import settings
from app.retrieval.chunk_selection import loaded_span_is_substantive, upgrade_chunk_choice
from app.retrieval.fusion import ChunkChoice
from app.retrieval.query_terms import build_lexical_queries, extract_search_terms


def workflow_lexical_queries(question: str) -> list[str]:
    """Search terms derived from the user's question—no repo-specific keyword list."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add(raw: str) -> None:
        text = raw.strip()
        if len(text) < 2:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(text)

    for term in extract_search_terms(question):
        add(term)
    for phrase in build_lexical_queries(question):
        add(phrase)
    return ordered[:10]


def lexical_workflow_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    queries: list[str],
    *,
    limit: int = 16,
) -> list[ChunkChoice]:
    if not queries:
        return []
    seen: set[str] = set()
    choices: list[ChunkChoice] = []

    def add_from_chunk(chunk: Chunk) -> None:
        if chunk.id in seen or len(choices) >= limit:
            return
        upgraded = upgrade_chunk_choice(db, generation.id, chunk)
        if upgraded.id in seen:
            return
        seen.add(upgraded.id)
        anchor = upgraded.symbol_id or upgraded.file_id
        choices.append(ChunkChoice(upgraded.id, anchor, upgraded.kind, upgraded.ordinal))

    for query_text in queries:
        if len(choices) >= limit:
            break
        if not query_text.strip():
            continue
        query = func.plainto_tsquery("simple", query_text)
        rows = (
            db.query(Chunk)
            .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
            .filter(GenerationChunk.index_generation_id == generation.id, GenerationChunk.search_vector.op("@@")(query))
            .order_by(desc(func.ts_rank(GenerationChunk.search_vector, query)), Chunk.start_line, Chunk.id)
            .limit(10)
            .all()
        )
        for chunk in rows:
            add_from_chunk(chunk)
            if len(choices) >= limit:
                break
    return choices


def loaded_needs_more_bodies(
    loaded: list[dict],
    *,
    min_bodies: int = 4,
    min_bytes: int | None = None,
) -> bool:
    """True when the pack is mostly headers/imports or too few substantial bodies."""
    if not loaded:
        return True
    floor = min_bytes if min_bytes is not None else settings.answer_min_substantive_chunk_bytes
    substantial = [span for span in loaded if loaded_span_is_substantive(span, min_bytes=floor)]
    if len(substantial) < min_bodies:
        return True
    thin = sum(
        1
        for span in loaded
        if span.get("chunk_kind") in {"import_block", "file_overview"}
        or (
            span.get("chunk_kind") == "symbol_part"
            and int(span.get("raw_bytes") or 0) < floor
        )
        or int(span.get("raw_bytes") or 0) < min(120, floor)
    )
    return thin > len(loaded) // 2
