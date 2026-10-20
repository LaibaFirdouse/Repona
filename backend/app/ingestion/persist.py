"""Write one assembled generation into PostgreSQL without moving the active pointer."""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.ingestion.assemble import GenerationDraft
from app.storage.identity import without_nul
from app.ingestion.versions import (
    CHUNKING_VERSION,
    EMBEDDING_INPUT_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)
from app.models.catalog import (
    Chunk,
    DetectedTechnology,
    Directory,
    File,
    GenerationChunk,
    GenerationDirectory,
    GenerationFile,
    IndexGeneration,
    SourceRevision,
    Symbol,
)
from app.models.repository import Repository
from app.storage.identity import source_revision_id


class GenerationWriteError(RuntimeError):
    pass


def ensure_source_revision(
    db: Session,
    repository_id: str,
    commit_sha: str,
    observed_branch: str | None,
    snapshot_path: str | None,
) -> SourceRevision:
    revision_id = str(source_revision_id(repository_id, commit_sha))
    existing = db.get(SourceRevision, revision_id)
    if existing is not None:
        if snapshot_path and existing.snapshot_state != "ready":
            existing.snapshot_path = snapshot_path
            existing.snapshot_state = "ready"
        return existing
    revision = SourceRevision(
        id=revision_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        observed_branch=observed_branch,
        snapshot_path=snapshot_path,
        snapshot_state="ready" if snapshot_path else "absent",
    )
    db.add(revision)
    db.flush()
    return revision


def insert_pending_generation(
    db: Session,
    repository_id: str,
    source_revision_id_value: str,
    generation_id: str,
    fingerprint: str,
    embedding_model: str,
    vector_dimensions: int,
    collection: str,
    build_attempt: int,
) -> IndexGeneration:
    generation = IndexGeneration(
        id=generation_id,
        repository_id=repository_id,
        source_revision_id=source_revision_id_value,
        parser_version=PARSER_VERSION,
        chunking_version=CHUNKING_VERSION,
        embedding_model=embedding_model,
        vector_dimensions=vector_dimensions,
        embedding_input_version=EMBEDDING_INPUT_VERSION,
        embedding_config_fingerprint=fingerprint,
        lexical_index_version=LEXICAL_INDEX_VERSION,
        inclusion_rules_version=INCLUSION_RULES_VERSION,
        build_attempt=build_attempt,
        qdrant_collection=collection,
        status="pending",
    )
    db.add(generation)
    db.flush()
    return generation


def _touch(on_progress, renewed: float) -> float:
    if on_progress is None or time.monotonic() - renewed < 30:
        return renewed
    on_progress()
    return time.monotonic()


_BATCH = 400


def _known_ids(db: Session, model, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(ids), _BATCH):
        found.update(
            row[0]
            for row in db.query(model.id).filter(model.id.in_(ids[start : start + _BATCH])).all()
        )
    return found


def _release(db: Session, pending: list) -> None:
    if not pending:
        return
    db.flush()
    for obj in pending:
        db.expunge(obj)
    pending.clear()


def write_draft(
    db: Session,
    repository_id: str,
    generation_id: str,
    fingerprint: str,
    draft: GenerationDraft,
    on_progress=None,
) -> None:
    """Insert the same rows as one-at-a-time writes, in batches so a large tree stays inside the lease."""
    renewed = time.monotonic()
    known_directories = _known_ids(db, Directory, [item.id for item in draft.directories])
    pending: list = []
    for directory in draft.directories:
        renewed = _touch(on_progress, renewed)
        if directory.id in known_directories:
            continue
        pending.append(
            Directory(
                id=directory.id,
                repository_id=repository_id,
                parent_directory_id=directory.parent_directory_id,
                relative_path=directory.relative_path,
                name=directory.name,
                depth=directory.depth,
            )
        )
        db.add(pending[-1])
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    known_files = {
        row.id: (row.content_hash, row.relative_path)
        for row in _batched_files(db, [item.id for item in draft.files])
    }
    for file in draft.files:
        renewed = _touch(on_progress, renewed)
        existing = known_files.get(file.id)
        if existing is None:
            pending.append(
                File(
                    id=file.id,
                    repository_id=repository_id,
                    directory_id=file.directory_id,
                    logical_file_key=file.logical_file_key,
                    relative_path=file.relative_path,
                    language=file.language,
                    extension=file.extension,
                    line_count=file.line_count,
                    byte_size=file.byte_size,
                    content_hash=file.content_hash,
                    git_blob_id=file.git_blob_id,
                )
            )
            db.add(pending[-1])
        elif existing != (file.content_hash, file.relative_path):
            raise GenerationWriteError(f"file id {file.id} does not match the stored bytes")
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    for file in draft.files:
        renewed = _touch(on_progress, renewed)
        pending.append(
            GenerationFile(
                repository_id=repository_id,
                index_generation_id=generation_id,
                file_id=file.id,
                logical_file_key=file.logical_file_key,
                parse_status=file.parse_status,
                parse_message=file.parse_message,
            )
        )
        db.add(pending[-1])
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    for directory in draft.directories:
        renewed = _touch(on_progress, renewed)
        pending.append(
            db.merge(
                GenerationDirectory(
                    repository_id=repository_id,
                    index_generation_id=generation_id,
                    directory_id=directory.id,
                )
            )
        )
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    known_symbols = {
        row.id: (row.start_byte, row.end_byte, row.qualified_name)
        for row in _batched_symbols(db, [item.id for item in draft.symbols])
    }
    for symbol in draft.symbols:
        renewed = _touch(on_progress, renewed)
        existing = known_symbols.get(symbol.id)
        if existing is None:
            pending.append(
                Symbol(
                    id=symbol.id,
                    repository_id=repository_id,
                    file_id=symbol.file_id,
                    parser_version=PARSER_VERSION,
                    parent_symbol_id=symbol.parent_symbol_id,
                    identity_key=symbol.identity_key,
                    kind=symbol.kind,
                    name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    signature=symbol.signature,
                    ordinal=symbol.ordinal,
                    language=symbol.language,
                    start_byte=symbol.start_byte,
                    end_byte=symbol.end_byte,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                )
            )
            db.add(pending[-1])
        elif existing != (symbol.start_byte, symbol.end_byte, symbol.qualified_name):
            raise GenerationWriteError(f"symbol id {symbol.id} does not match this parse")
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    known_chunks = {row.id: row.span_hash for row in _batched_chunks(db, [item.id for item in draft.chunks])}
    for chunk in draft.chunks:
        renewed = _touch(on_progress, renewed)
        existing = known_chunks.get(chunk.id)
        if existing is None:
            pending.append(
                Chunk(
                    id=chunk.id,
                    repository_id=repository_id,
                    file_id=chunk.file_id,
                    symbol_id=chunk.symbol_id,
                    parser_version=PARSER_VERSION,
                    chunking_version=CHUNKING_VERSION,
                    parent_chunk_id=None,
                    kind=chunk.kind,
                    ordinal=chunk.ordinal,
                    start_byte=chunk.start_byte,
                    end_byte=chunk.end_byte,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    span_hash=chunk.span_hash,
                )
            )
            db.add(pending[-1])
        elif existing != chunk.span_hash:
            raise GenerationWriteError(f"chunk id {chunk.id} does not match this span")
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    renewed = _bulk_insert_generation_chunks(
        db,
        repository_id,
        generation_id,
        fingerprint,
        draft.chunks,
        on_progress=on_progress,
        renewed=renewed,
    )
    for technology in draft.technologies:
        pending.append(
            DetectedTechnology(
                repository_id=repository_id,
                index_generation_id=generation_id,
                name=without_nul(technology.name),
                evidence_file_id=technology.evidence_file_id,
                evidence_text=without_nul(technology.evidence_text),
            )
        )
        db.add(pending[-1])
    _release(db, pending)


def append_generation_chunks(
    db: Session,
    repository_id: str,
    generation_id: str,
    fingerprint: str,
    draft: GenerationDraft,
    on_progress=None,
) -> None:
    """Insert chunk rows added after the initial draft write (lazy or deferred semantic indexing)."""
    renewed = time.monotonic()
    known_chunks = {row.id: row.span_hash for row in _batched_chunks(db, [item.id for item in draft.chunks])}
    pending: list = []
    for chunk in draft.chunks:
        renewed = _touch(on_progress, renewed)
        existing = known_chunks.get(chunk.id)
        if existing is None:
            pending.append(
                Chunk(
                    id=chunk.id,
                    repository_id=repository_id,
                    file_id=chunk.file_id,
                    symbol_id=chunk.symbol_id,
                    parser_version=PARSER_VERSION,
                    chunking_version=CHUNKING_VERSION,
                    parent_chunk_id=None,
                    kind=chunk.kind,
                    ordinal=chunk.ordinal,
                    start_byte=chunk.start_byte,
                    end_byte=chunk.end_byte,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    span_hash=chunk.span_hash,
                )
            )
            db.add(pending[-1])
        elif existing != chunk.span_hash:
            raise GenerationWriteError(f"chunk id {chunk.id} does not match this span")
        if len(pending) >= _BATCH:
            _release(db, pending)
    _release(db, pending)
    _bulk_insert_generation_chunks(
        db,
        repository_id,
        generation_id,
        fingerprint,
        draft.chunks,
        on_progress=on_progress,
        renewed=renewed,
    )


def _bulk_insert_generation_chunks(
    db: Session,
    repository_id: str,
    generation_id: str,
    fingerprint: str,
    chunks: list,
    *,
    on_progress=None,
    renewed: float,
) -> float:
    for start in range(0, len(chunks), _BATCH):
        part = chunks[start : start + _BATCH]
        renewed = _touch(on_progress, renewed)
        rows = [
            {
                "repository_id": repository_id,
                "index_generation_id": generation_id,
                "chunk_id": chunk.id,
                "context_prefix": without_nul(chunk.context_prefix),
                "embedding_input_hash": chunk.embedding_input_hash,
                "embedding_config_fingerprint": fingerprint,
                "lexical": without_nul(chunk.lexical_text),
            }
            for chunk in part
        ]
        db.execute(
            text(
                """
                INSERT INTO generation_chunks (
                    repository_id,
                    index_generation_id,
                    chunk_id,
                    context_prefix,
                    embedding_input_hash,
                    embedding_config_fingerprint,
                    search_vector,
                    vector_status
                )
                VALUES (
                    :repository_id,
                    :index_generation_id,
                    :chunk_id,
                    :context_prefix,
                    :embedding_input_hash,
                    :embedding_config_fingerprint,
                    to_tsvector('simple', :lexical),
                    'pending'
                )
                ON CONFLICT (repository_id, index_generation_id, chunk_id) DO NOTHING
                """
            ),
            rows,
        )
    db.flush()
    return renewed


def _batched_files(db: Session, ids: list[str]):
    rows = []
    for start in range(0, len(ids), _BATCH):
        rows.extend(
            db.query(File.id, File.content_hash, File.relative_path)
            .filter(File.id.in_(ids[start : start + _BATCH]))
            .all()
        )
    return rows


def _batched_symbols(db: Session, ids: list[str]):
    rows = []
    for start in range(0, len(ids), _BATCH):
        rows.extend(
            db.query(Symbol.id, Symbol.start_byte, Symbol.end_byte, Symbol.qualified_name)
            .filter(Symbol.id.in_(ids[start : start + _BATCH]))
            .all()
        )
    return rows


def _batched_chunks(db: Session, ids: list[str]):
    rows = []
    for start in range(0, len(ids), _BATCH):
        rows.extend(
            db.query(Chunk.id, Chunk.span_hash).filter(Chunk.id.in_(ids[start : start + _BATCH])).all()
        )
    return rows


def activate_generation(db: Session, repository_id: str, generation_id: str, owner: str, epoch: int, now: datetime) -> None:
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    from app.ingestion.coordination import refresh_may_activate

    if not refresh_may_activate(
        repository.refresh_state,
        repository.refresh_generation_id,
        generation_id,
        repository.refresh_owner,
        owner,
        repository.refresh_epoch,
        epoch,
        repository.refresh_lease_expires_at,
        now,
    ):
        raise GenerationWriteError("refresh owner no longer holds this generation")
    generation = db.get(IndexGeneration, generation_id)
    if generation is None:
        raise GenerationWriteError("pending generation is missing")
    previous_id = repository.active_generation_id
    if previous_id and previous_id != generation_id:
        previous = db.get(IndexGeneration, previous_id)
        if previous is not None:
            previous.status = "retired"
            previous.retired_at = datetime.utcnow()
    generation.status = "ready"
    generation.finished_at = datetime.utcnow()
    generation.graph_epoch = epoch
    repository.active_generation_id = generation_id
    repository.refresh_state = "idle"
    repository.refresh_generation_id = None
    repository.refresh_owner = None
    repository.refresh_lease_expires_at = None
    repository.refresh_heartbeat_at = None
