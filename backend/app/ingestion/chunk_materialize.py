"""Lazy chunk materialisation from the authoritative snapshot."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.assemble import PROGRESSIVE_DEFER_KINDS, ChunkRecord, FileRecord, GenerationDraft, _chunk_records, _symbol_records
from app.ingestion.parse import parse_file
from app.core.config import settings
from app.ingestion.lazy_vectors import ensure_chunk_records_vectors
from app.ingestion.persist import append_generation_chunks
from app.models.catalog import Chunk, File, GenerationChunk, IndexGeneration, Symbol
from app.storage.identity import without_nul


class ChunkMaterializeError(RuntimeError):
    pass


def materialize_symbol_chunks(
    db: Session,
    *,
    repository_id: str,
    generation_id: str,
    fingerprint: str,
    parser_version: str,
    symbol_ids: list[str],
    snapshot_root: Path,
    cache: dict[str, list[ChunkRecord]] | None = None,
    generation: IndexGeneration | None = None,
    qdrant_url: str | None = None,
    embed=None,
    vector_stats: dict | None = None,
) -> list[ChunkRecord]:
    """Create symbol_body/part rows for symbols that were deferred during progressive indexing."""
    if not symbol_ids:
        return []
    cache = {} if cache is None else cache
    created: list[ChunkRecord] = []
    rows = (
        db.query(Symbol, File)
        .join(File, File.id == Symbol.file_id)
        .filter(Symbol.id.in_(symbol_ids), Symbol.parser_version == parser_version)
        .all()
    )
    by_symbol = {symbol.id: (symbol, file) for symbol, file in rows}
    for symbol_id in symbol_ids:
        if symbol_id in cache:
            created.extend(cache[symbol_id])
            continue
        pair = by_symbol.get(symbol_id)
        if pair is None:
            continue
        symbol, file = pair
        path = snapshot_root / file.relative_path
        if not path.is_file():
            raise ChunkMaterializeError(f"snapshot missing {file.relative_path}")
        payload = path.read_bytes()
        parsed = parse_file(file.relative_path, payload)
        symbols = _symbol_records(repository_id, file, parsed, payload)
        symbol_map = {item.id: item for item in symbols}
        source_symbol = symbol_map.get(symbol.id)
        if source_symbol is None:
            continue
        record = _file_record_from_db(file)
        chunks = _chunk_records(
            file=record,
            symbols=symbols,
            parsed=parsed,
            payload=payload,
            fingerprint=fingerprint,
            only_symbol_id=symbol.id,
            chunk_kinds=PROGRESSIVE_DEFER_KINDS,
        )
        symbol_chunks = chunks
        cache[symbol_id] = symbol_chunks
        created.extend(symbol_chunks)
    if not created:
        return []
    draft = GenerationDraft(chunks=created)
    append_generation_chunks(db, repository_id, generation_id, fingerprint, draft)
    db.flush()
    if generation is not None and qdrant_url and embed is not None and settings.lazy_embed_on_query:
        ensure_chunk_records_vectors(
            db,
            generation=generation,
            repository_id=repository_id,
            records=created,
            qdrant_url=qdrant_url,
            embed=embed,
            stats=vector_stats,
        )
    return created


def missing_symbol_chunk_ids(db: Session, generation_id: str, symbol_ids: list[str]) -> list[str]:
    if not symbol_ids:
        return []
    present = {
        row[0]
        for row in db.query(Chunk.symbol_id)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            Chunk.symbol_id.in_(symbol_ids),
            Chunk.kind.in_(("symbol_body", "symbol_part")),
        )
        .all()
    }
    return [item for item in symbol_ids if item not in present]


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
