"""Query-time and background embedding for generation chunks."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.assemble import ChunkRecord, FileRecord, GenerationDraft, SymbolRecord
from app.ingestion.embedding_reuse import embed_chunks_with_reuse
from app.ingestion.vector_store import ensure_collection, upsert_missing_points
from app.models.catalog import Chunk, File, GenerationChunk, IndexGeneration, Symbol
from app.storage.identity import lexical_input, without_nul


class LazyVectorError(RuntimeError):
    pass


def chunks_missing_vectors(db: Session, generation_id: str, chunk_ids: list[str]) -> list[str]:
    if not chunk_ids:
        return []
    rows = (
        db.query(GenerationChunk.chunk_id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            GenerationChunk.chunk_id.in_(chunk_ids),
            GenerationChunk.vector_status != "ready",
        )
        .all()
    )
    return [row[0] for row in rows]


def count_vector_ready(db: Session, generation_id: str) -> int:
    return (
        db.query(GenerationChunk.chunk_id)
        .filter(GenerationChunk.index_generation_id == generation_id, GenerationChunk.vector_status == "ready")
        .count()
    )


def mark_vectors_ready(db: Session, generation_id: str, chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    (
        db.query(GenerationChunk)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            GenerationChunk.chunk_id.in_(chunk_ids),
        )
        .update({GenerationChunk.vector_status: "ready"}, synchronize_session=False)
    )
    db.flush()


def chunk_records_for_ids(
    db: Session,
    repository_id: str,
    generation_id: str,
    chunk_ids: list[str],
    snapshot_root: Path,
) -> list[ChunkRecord]:
    rows = (
        db.query(Chunk, GenerationChunk, File)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .join(File, File.id == Chunk.file_id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            GenerationChunk.repository_id == repository_id,
            Chunk.id.in_(chunk_ids or [""]),
        )
        .all()
    )
    records: list[ChunkRecord] = []
    payload_cache: dict[str, bytes] = {}
    for chunk, link, file in rows:
        path = snapshot_root / file.relative_path
        if file.relative_path not in payload_cache:
            if not path.is_file():
                raise LazyVectorError(f"snapshot missing {file.relative_path}")
            payload_cache[file.relative_path] = path.read_bytes()
        payload = payload_cache[file.relative_path]
        span = payload[chunk.start_byte : chunk.end_byte]
        records.append(
            ChunkRecord(
                id=chunk.id,
                file_id=chunk.file_id,
                symbol_id=chunk.symbol_id,
                kind=chunk.kind,
                ordinal=chunk.ordinal,
                start_byte=chunk.start_byte,
                end_byte=chunk.end_byte,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                span_hash=chunk.span_hash,
                context_prefix=link.context_prefix,
                embedding_input_hash=link.embedding_input_hash,
                lexical_text=lexical_input(link.context_prefix, span).decode("utf-8", errors="replace"),
            )
        )
    return records


def _file_record_from_db(file: File) -> FileRecord:
    return FileRecord(
        id=file.id,
        directory_id=file.directory_id,
        logical_file_key=file.logical_file_key,
        relative_path=file.relative_path,
        language=file.language,
        extension=file.extension,
        line_count=file.line_count,
        byte_size=file.byte_size,
        content_hash=file.content_hash,
        git_blob_id=file.git_blob_id,
        parse_status="parsed",
        parse_message=None,
    )


def _generation_draft_for_records(db: Session, records: list[ChunkRecord]) -> GenerationDraft:
    if not records:
        return GenerationDraft()
    file_ids = {record.file_id for record in records}
    symbol_ids = {record.symbol_id for record in records if record.symbol_id}
    files = [_file_record_from_db(row) for row in db.query(File).filter(File.id.in_(file_ids)).all()]
    symbols: list[SymbolRecord] = []
    if symbol_ids:
        for row in db.query(Symbol).filter(Symbol.id.in_(symbol_ids)).all():
            symbols.append(
                SymbolRecord(
                    id=row.id,
                    file_id=row.file_id,
                    parent_symbol_id=row.parent_symbol_id,
                    identity_key=row.identity_key,
                    kind=row.kind,
                    name=row.name,
                    qualified_name=row.qualified_name,
                    signature=row.signature or "",
                    ordinal=row.ordinal,
                    language=row.language or "",
                    start_byte=row.start_byte,
                    end_byte=row.end_byte,
                    start_line=row.start_line,
                    end_line=row.end_line,
                    relative_path=row.relative_path,
                )
            )
    return GenerationDraft(chunks=records, files=files, symbols=symbols)


def ensure_chunk_vectors(
    db: Session,
    *,
    generation: IndexGeneration,
    repository_id: str,
    chunk_ids: list[str],
    snapshot_root: Path,
    qdrant_url: str,
    embed,
    stats: dict | None = None,
) -> list[str]:
    """Embed and upsert vectors for chunks that are not yet ready. Returns ids now ready."""
    missing = chunks_missing_vectors(db, generation.id, chunk_ids)
    if not missing:
        return []
    records = chunk_records_for_ids(db, repository_id, generation.id, missing, snapshot_root)
    if not records:
        return []
    ensure_collection(qdrant_url, generation.qdrant_collection, generation.vector_dimensions)
    draft = _generation_draft_for_records(db, records)
    vectors = embed_chunks_with_reuse(
        qdrant_url,
        generation.qdrant_collection,
        repository_id,
        generation.embedding_config_fingerprint,
        embed,
        records,
        lambda: None,
        stats=stats,
    )
    upserted = upsert_missing_points(
        qdrant_url,
        generation.qdrant_collection,
        repository_id,
        draft,
        generation.embedding_config_fingerprint,
        vectors,
        chunks=records,
    )
    if stats is not None:
        stats["lazy_points_upserted"] = stats.get("lazy_points_upserted", 0) + upserted
    ready = [record.id for record in records if record.id in vectors]
    mark_vectors_ready(db, generation.id, ready)
    return ready


def ensure_chunk_records_vectors(
    db: Session,
    *,
    generation: IndexGeneration,
    repository_id: str,
    records: list[ChunkRecord],
    qdrant_url: str,
    embed,
    stats: dict | None = None,
) -> list[str]:
    ids = [record.id for record in records]
    missing = chunks_missing_vectors(db, generation.id, ids)
    if not missing:
        return []
    subset = [record for record in records if record.id in missing]
    ensure_collection(qdrant_url, generation.qdrant_collection, generation.vector_dimensions)
    draft = _generation_draft_for_records(db, subset)
    vectors = embed_chunks_with_reuse(
        qdrant_url,
        generation.qdrant_collection,
        repository_id,
        generation.embedding_config_fingerprint,
        embed,
        subset,
        lambda: None,
        stats=stats,
    )
    upserted = upsert_missing_points(
        qdrant_url,
        generation.qdrant_collection,
        repository_id,
        draft,
        generation.embedding_config_fingerprint,
        vectors,
        chunks=subset,
    )
    if stats is not None:
        stats["lazy_points_upserted"] = stats.get("lazy_points_upserted", 0) + upserted
    ready = [record.id for record in subset if record.id in vectors]
    mark_vectors_ready(db, generation.id, ready)
    return ready
