"""Retrieval boosts derived from what is actually indexed for a generation (Postgres catalog)."""

from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.catalog import Chunk, File, GenerationChunk, GenerationFile, IndexGeneration, Symbol
from app.retrieval.chunk_selection import is_meta_config_path, representative_chunk
from app.retrieval.fusion import ChunkChoice
from app.retrieval.query_terms import extract_search_terms, question_paths

_PATH_TERM_STOP = frozenset(
    {
        "make",
        "sure",
        "when",
        "what",
        "how",
        "does",
        "this",
        "that",
        "with",
        "from",
        "into",
        "about",
        "your",
        "their",
        "code",
        "file",
        "library",
        "application",
        "system",
        "repository",
        "message",
        "client",
        "server",
        "user",
    }
)


def _dedupe_paths(paths: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in paths:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
        if len(ordered) >= limit:
            break
    return ordered


def generation_has_path(db: Session, generation_id: str, path_hint: str) -> bool:
    hint = path_hint.strip().replace("\\", "/")
    if not hint:
        return False
    escaped = hint.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    row = (
        db.query(File.id)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation_id,
            or_(
                File.relative_path == hint,
                File.relative_path.like(escaped + "/%", escape="\\"),
                File.relative_path.like("%/" + escaped + "/%", escape="\\"),
                File.relative_path.like("%/" + escaped, escape="\\"),
            ),
        )
        .limit(1)
        .first()
    )
    return row is not None


def catalog_path_filters(
    db: Session,
    generation_id: str,
    question: str,
    *,
    search_question: str = "",
    limit: int = 8,
) -> list[str]:
    """Path substrings that exist in this generation and match the user's words."""
    hints: list[str] = []
    for path in question_paths(question) + question_paths(search_question):
        if generation_has_path(db, generation_id, path):
            hints.append(path)
    terms = extract_search_terms(search_question or question)
    for term in terms:
        token = term.strip().replace("%", "")
        # Short tokens and path segments like "make" match Makefile; require length or a path slash.
        if "/" in token:
            pass
        elif len(token) < 4 or token.lower() in _PATH_TERM_STOP:
            continue
        rows = (
            db.query(File.relative_path)
            .join(GenerationFile, GenerationFile.file_id == File.id)
            .filter(
                GenerationFile.index_generation_id == generation_id,
                File.relative_path.ilike(f"%{token}%"),
            )
            .order_by(File.relative_path, File.id)
            .limit(4)
            .all()
        )
        for row in rows:
            if not is_meta_config_path(row.relative_path):
                hints.append(row.relative_path)
    return _dedupe_paths(hints, limit)


def _files_matching_terms(
    db: Session,
    generation_id: str,
    terms: list[str],
    *,
    limit: int,
) -> list[File]:
    filters = []
    for term in terms:
        token = term.strip()
        if len(token) < 2:
            continue
        if "/" in token:
            filters.append(File.relative_path.ilike(f"%{token}%"))
        elif len(token) >= 3:
            filters.append(File.relative_path.ilike(f"%{token}%"))
    if not filters:
        return []
    rows = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation_id, or_(*filters))
        .order_by(File.relative_path, File.id)
        .limit(limit)
        .all()
    )
    return [row for row in rows if not is_meta_config_path(row.relative_path)]


def _files_richest_in_symbol_bodies(
    db: Session,
    generation_id: str,
    *,
    limit: int,
) -> list[File]:
    """Files in this generation with the most indexed symbol_body bytes."""
    rows = (
        db.query(
            File,
            func.coalesce(func.sum(Chunk.end_byte - Chunk.start_byte), 0).label("body_bytes"),
        )
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .join(Chunk, Chunk.file_id == File.id)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationFile.index_generation_id == generation_id,
            GenerationChunk.index_generation_id == generation_id,
            Chunk.kind == "symbol_body",
        )
        .group_by(File.id)
        .order_by(func.coalesce(func.sum(Chunk.end_byte - Chunk.start_byte), 0).desc(), File.relative_path)
        .limit(limit)
        .all()
    )
    if rows:
        return [row[0] for row in rows if not is_meta_config_path(row[0].relative_path)]
    rows = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .join(Chunk, Chunk.file_id == File.id)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationFile.index_generation_id == generation_id,
            GenerationChunk.index_generation_id == generation_id,
            Chunk.kind.in_(("symbol_part", "non_code", "file_overview")),
        )
        .group_by(File.id)
        .order_by(func.count(Chunk.id).desc(), File.relative_path)
        .limit(limit)
        .all()
    )
    return [row for row in rows if not is_meta_config_path(row.relative_path)]


def _symbols_matching_terms(
    db: Session,
    generation: IndexGeneration,
    terms: list[str],
    *,
    limit: int,
) -> list[Symbol]:
    name_filters = []
    for term in terms:
        token = term.strip()
        if len(token) < 3 or "/" in token:
            continue
        name_filters.append(Symbol.name.ilike(f"%{token}%"))
        name_filters.append(Symbol.qualified_name.ilike(f"%{token}%"))
    if not name_filters:
        return []
    return (
        db.query(Symbol)
        .join(GenerationFile, GenerationFile.file_id == Symbol.file_id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            Symbol.parser_version == generation.parser_version,
            or_(*name_filters),
        )
        .order_by(Symbol.qualified_name, Symbol.id)
        .limit(limit)
        .all()
    )


def chunk_choices_for_files(
    db: Session,
    generation_id: str,
    files: list[File],
    *,
    limit: int,
    bodies_per_file: int | None = None,
) -> list[ChunkChoice]:
    if not files:
        return []
    per_file = bodies_per_file
    if per_file is None:
        per_file = max(2, min(settings.answer_impl_chunks_per_file, 4))
    seen: set[str] = set()
    choices: list[ChunkChoice] = []

    def add_chunk(chunk: Chunk, anchor: str | None = None) -> None:
        if chunk.id in seen or len(choices) >= limit:
            return
        seen.add(chunk.id)
        anchor_id = anchor or chunk.symbol_id or chunk.file_id
        choices.append(ChunkChoice(chunk.id, anchor_id, chunk.kind, chunk.ordinal))

    file_ids = [row.id for row in files]
    chunks = (
        db.query(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.file_id.in_(file_ids))
        .all()
    )
    by_file: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk.file_id, []).append(chunk)
    for file in files:
        pool = by_file.get(file.id, []) or []
        bodies = [item for item in pool if item.kind == "symbol_body"]
        bodies.sort(key=lambda item: (item.end_byte - item.start_byte, item.ordinal), reverse=True)
        if bodies:
            for body in bodies[:per_file]:
                add_chunk(body)
        else:
            best = representative_chunk(pool)
            if best is not None:
                add_chunk(best)
        if len(choices) >= limit:
            break
    return choices


def catalog_implementation_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    question: str,
    *,
    limit: int = 24,
) -> list[ChunkChoice]:
    """Implementation chunks from indexed files that match the question, then richest bodies in the index."""
    terms = extract_search_terms(question)
    files = _files_matching_terms(db, generation.id, terms, limit=limit * 2)
    choices: list[ChunkChoice] = []
    if files:
        choices = chunk_choices_for_files(db, generation.id, files, limit=limit)
    if len(choices) >= max(4, limit // 2):
        return choices[:limit]
    rich = _files_richest_in_symbol_bodies(db, generation.id, limit=limit * 2)
    rich = [item for item in rich if item.id not in {file.id for file in files}]
    if rich:
        choices = _merge_choices(
            choices,
            chunk_choices_for_files(db, generation.id, rich, limit=limit),
            limit,
        )
    if choices:
        return choices[:limit]
    symbols = _symbols_matching_terms(db, generation, terms, limit=limit)
    from app.retrieval.chunk_selection import pick_best_symbol_chunk

    for symbol in symbols:
        body = pick_best_symbol_chunk(db, generation.id, symbol.id)
        if body is None or body.chunk_id in {item.chunk_id for item in choices}:
            continue
        choices.append(body)
        if len(choices) >= limit:
            break
    return choices[:limit]


def _merge_choices(primary: list[ChunkChoice], extra: list[ChunkChoice], limit: int) -> list[ChunkChoice]:
    seen = {item.chunk_id for item in primary}
    merged = list(primary)
    for item in extra:
        if item.chunk_id in seen or len(merged) >= limit:
            continue
        seen.add(item.chunk_id)
        merged.append(item)
    return merged[:limit]


def indexed_path_spans_for_question(
    db: Session,
    generation_id: str,
    question: str,
    *,
    limit: int = 24,
) -> list[dict]:
    """Lightweight path list from the catalog when no span text could be packed (paths only, no invented files)."""
    paths = catalog_path_filters(db, generation_id, question, limit=limit)
    if not paths:
        rows = (
            db.query(File.relative_path)
            .join(GenerationFile, GenerationFile.file_id == File.id)
            .filter(GenerationFile.index_generation_id == generation_id)
            .order_by(File.relative_path, File.id)
            .limit(limit)
            .all()
        )
        paths = [row.relative_path for row in rows]
    return [
        {
            "chunk_id": f"path:{generation_id}:{index}",
            "relative_path": path,
            "qualified_name": "",
            "chunk_kind": "file",
            "start_line": None,
            "end_line": None,
            "text": path,
            "context_prefix": "",
            "raw_bytes": len(path.encode("utf-8")),
        }
        for index, path in enumerate(paths)
    ]
