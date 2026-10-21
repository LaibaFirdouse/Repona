"""Materialize deferred symbol bodies before search so lexical hits can reach implementation chunks."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ingestion.chunk_materialize import materialize_symbol_chunks, missing_symbol_chunk_ids
from app.ingestion.lazy_vectors import _file_scope_filters
from app.models.catalog import Chunk, File, GenerationChunk, GenerationFile, IndexGeneration, Symbol
from app.retrieval.fusion import LaneHit
from app.retrieval.query_router import RetrievalRequest


def file_ids_for_chunk_hits(db: Session, generation_id: str, hits: list[LaneHit]) -> list[str]:
    chunk_ids = [hit.chunk_id for hit in hits if hit.chunk_id]
    if not chunk_ids:
        return []
    rows = (
        db.query(Chunk.file_id)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            Chunk.id.in_(chunk_ids),
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def _scoped_file_ids(
    db: Session,
    generation_id: str,
    path_filters: list[str],
    identifiers: list[str],
    limit: int,
) -> list[str]:
    filters = _file_scope_filters(path_filters, identifiers)
    if not filters:
        return []
    rows = (
        db.query(File.id)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation_id, or_(*filters))
        .order_by(File.relative_path, File.id)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def symbol_ids_for_files(
    db: Session,
    *,
    repository_id: str,
    parser_version: str,
    file_ids: list[str],
    limit: int,
) -> list[str]:
    if not file_ids or limit < 1:
        return []
    rows = (
        db.query(Symbol.id)
        .filter(
            Symbol.repository_id == repository_id,
            Symbol.parser_version == parser_version,
            Symbol.file_id.in_(file_ids),
            Symbol.kind != "module",
        )
        .order_by(Symbol.start_byte, Symbol.id)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def collect_hydrate_symbol_ids(
    db: Session,
    *,
    generation: IndexGeneration,
    repository_id: str,
    retrieval: RetrievalRequest,
    name_hits: list[LaneHit],
    text_hits: list[LaneHit],
) -> list[str]:
    """Symbols to materialize from preview hits and planner path scope (#2 + #6)."""
    budget = settings.lazy_embed_max_chunks
    file_ids = file_ids_for_chunk_hits(db, generation.id, text_hits + name_hits)
    file_ids.extend(
        _scoped_file_ids(
            db,
            generation.id,
            retrieval.path_filters,
            retrieval.identifiers,
            limit=budget * 4,
        )
    )
    file_ids = list(dict.fromkeys(file_ids))
    symbol_ids = symbol_ids_for_files(
        db,
        repository_id=repository_id,
        parser_version=generation.parser_version,
        file_ids=file_ids,
        limit=budget * 4,
    )
    for hit in name_hits:
        if hit.chunk_id is None and hit.anchor_id not in symbol_ids:
            symbol_ids.append(hit.anchor_id)
    return list(dict.fromkeys(symbol_ids))[: budget * 4]


def hydrate_deferred_from_preview(
    db: Session,
    *,
    generation: IndexGeneration,
    repository_id: str,
    retrieval: RetrievalRequest,
    name_hits: list[LaneHit],
    text_hits: list[LaneHit],
    snapshot_root: Path,
    qdrant_url: str,
    embed,
    stats: dict | None = None,
) -> dict:
    """Insert deferred symbol_body rows for candidate files/symbols. Returns trace fields."""
    report: dict = {"candidate_symbols": 0, "materialized_symbols": 0, "candidate_files": 0}
    if not retrieval.needs_source_bodies or not snapshot_root.is_dir():
        return report
    symbol_ids = collect_hydrate_symbol_ids(
        db,
        generation=generation,
        repository_id=repository_id,
        retrieval=retrieval,
        name_hits=name_hits,
        text_hits=text_hits,
    )
    report["candidate_symbols"] = len(symbol_ids)
    report["candidate_files"] = len(
        file_ids_for_chunk_hits(db, generation.id, text_hits + name_hits)
    )
    if not symbol_ids:
        return report
    budget = settings.lazy_embed_max_chunks
    existing = {
        row[0]
        for row in db.query(Symbol.id).filter(Symbol.id.in_(symbol_ids[: budget * 4])).all()
    }
    candidates = [item for item in symbol_ids if item in existing][:budget]
    missing = missing_symbol_chunk_ids(db, generation.id, candidates)
    if not missing:
        return report
    materialize_symbol_chunks(
        db,
        repository_id=repository_id,
        generation_id=generation.id,
        fingerprint=generation.embedding_config_fingerprint,
        parser_version=generation.parser_version,
        symbol_ids=missing,
        snapshot_root=snapshot_root,
        generation=generation,
        qdrant_url=qdrant_url,
        embed=embed if settings.lazy_embed_on_query else None,
        vector_stats=stats,
    )
    report["materialized_symbols"] = len(missing)
    return report
