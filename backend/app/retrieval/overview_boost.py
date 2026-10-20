"""Extra chunks for purpose, overview, and architecture questions."""

from __future__ import annotations

import re

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from app.models.catalog import Chunk, File, GenerationChunk, GenerationFile, IndexGeneration
from app.retrieval.chunk_selection import representative_chunk
from app.retrieval.fusion import ChunkChoice

_OVERVIEW = re.compile(
    r"\b("
    r"purpose|what is|what'?s|what does|solve|about this|overview|goal|"
    r"why does|who is it for|problem|use case|describe this repo"
    r")\b",
    re.I,
)

_PATH_HINTS = (
    "readme",
    "go.mod",
    "main.go",
    "handler.go",
    "routes.go",
    "service.go",
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "setup.py",
)


def overview_question(question: str) -> bool:
    return bool(_OVERVIEW.search(question))


def overview_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    extra_lexical: list[str] | None = None,
    path_contains: list[str] | None = None,
    limit: int = 20,
) -> list[ChunkChoice]:
    """Prefer README, module metadata, and HTTP entrypoints with real chunk bodies."""
    seen: set[str] = set()
    choices: list[ChunkChoice] = []

    def add_chunk(chunk: Chunk) -> None:
        if chunk.id in seen or len(choices) >= limit:
            return
        seen.add(chunk.id)
        anchor = chunk.symbol_id or chunk.file_id
        choices.append(ChunkChoice(chunk.id, anchor, chunk.kind, chunk.ordinal))

    path_terms = list(_PATH_HINTS)
    for item in path_contains or []:
        token = item.strip().lower()
        if token and token not in path_terms:
            path_terms.append(token)

    if path_terms:
        filters = [File.relative_path.ilike(f"%{term}%") for term in path_terms]
        files = (
            db.query(File)
            .join(GenerationFile, GenerationFile.file_id == File.id)
            .filter(GenerationFile.index_generation_id == generation.id, or_(*filters))
            .order_by(File.relative_path, File.id)
            .limit(limit * 2)
            .all()
        )
        if files:
            file_ids = [row.id for row in files]
            chunks = (
                db.query(Chunk)
                .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
                .filter(GenerationChunk.index_generation_id == generation.id, Chunk.file_id.in_(file_ids))
                .all()
            )
            by_file: dict[str, list[Chunk]] = {}
            for chunk in chunks:
                by_file.setdefault(chunk.file_id, []).append(chunk)
            for file in files:
                pool = by_file.get(file.id, [])
                if not pool:
                    continue
                pick = representative_chunk(pool)
                if pick is not None:
                    add_chunk(pick)

    queries = ["readme", "purpose", "overview", "leaderboard", "package main"]
    for item in extra_lexical or []:
        if item.strip() and item.strip() not in queries:
            queries.append(item.strip())
    for query_text in queries:
        if len(choices) >= limit:
            break
        query = func.plainto_tsquery("simple", query_text)
        rows = (
            db.query(Chunk)
            .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
            .filter(GenerationChunk.index_generation_id == generation.id, GenerationChunk.search_vector.op("@@")(query))
            .order_by(desc(func.ts_rank(GenerationChunk.search_vector, query)), Chunk.start_line, Chunk.id)
            .limit(8)
            .all()
        )
        for chunk in rows:
            add_chunk(chunk)
            if len(choices) >= limit:
                break
    return choices
