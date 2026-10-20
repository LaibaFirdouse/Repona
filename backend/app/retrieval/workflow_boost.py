"""Lexical and path boosts for multi-workflow / implementation questions."""

from __future__ import annotations

import re

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.models.catalog import Chunk, GenerationChunk, IndexGeneration
from app.retrieval.chunk_selection import representative_chunk, upgrade_chunk_choice
from app.retrieval.fusion import ChunkChoice

_SNAKE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_CAMEL = re.compile(r"\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+\b")

_KEYWORD_HINTS = (
    "import rules",
    "export rules",
    "modify rules",
    "declarativeNetRequest",
    "chrome.storage",
    "service worker",
    "NETWORK_LOG",
    "IMPORT_RULES",
    "getServerSession",
    "generateTasks",
    "connectDB",
    "NextAuth",
    "route handler",
    "mongoose",
    "Groq",
)


def workflow_lexical_queries(question: str) -> list[str]:
    """Terms likely to appear in handler code for workflow-style questions."""
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in (_SNAKE, _CAMEL):
        for match in pattern.finditer(question):
            token = match.group()
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(token)
    lower = question.lower()
    for hint in _KEYWORD_HINTS:
        if hint.lower() in lower and hint.lower() not in seen:
            seen.add(hint.lower())
            ordered.append(hint)
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


def loaded_needs_more_bodies(loaded: list[dict], *, min_bodies: int = 4, min_bytes: int = 350) -> bool:
    """True when the pack is mostly headers/imports or too few substantial bodies."""
    if not loaded:
        return True
    substantial = [
        span
        for span in loaded
        if span.get("chunk_kind") == "symbol_body" and int(span.get("raw_bytes") or 0) >= min_bytes
    ]
    if len(substantial) < min_bodies:
        return True
    importish = sum(
        1
        for span in loaded
        if span.get("chunk_kind") in {"import_block", "file_overview"}
        or int(span.get("raw_bytes") or 0) < 120
    )
    return importish > len(loaded) // 2
