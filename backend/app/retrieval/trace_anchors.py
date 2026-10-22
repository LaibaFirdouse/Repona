"""Catalog-backed anchor chunks for entry-point and execution-trace questions."""

from __future__ import annotations

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.catalog import File, GenerationFile, IndexGeneration, Symbol
from app.retrieval.catalog_context import (
    chunk_choices_for_files,
    generation_has_path,
    _files_matching_terms,
)
from app.retrieval.chunk_selection import is_test_path, pick_best_symbol_chunk
from app.retrieval.fusion import ChunkChoice
from app.retrieval.query_plan import QueryIntent
from app.retrieval.query_terms import extract_search_terms

_ENTRY_ANCHOR = re.compile(
    r"\b("
    r"main entry|entry point|bootstrap|startup|start up|"
    r"what runs first|program entry|application entry|"
    r"in terms of code|end[- ]to[- ]end"
    r")\b",
    re.I,
)

_MAIN_SYMBOL_NAMES = frozenset({"main", "Main"})

# Path substrings tried only when they exist in this generation's file catalog.
_ENTRY_PATH_HINTS = ("layout", "page", "providers", "router", "middleware")


def question_wants_trace_anchors(question: str, intent: QueryIntent) -> bool:
    if intent in {"execution_trace", "debugging"}:
        return True
    return bool(_ENTRY_ANCHOR.search(question.strip()))


def _main_entry_symbols(
    db: Session,
    generation: IndexGeneration,
    *,
    limit: int,
) -> list[Symbol]:
    rows = (
        db.query(Symbol, File.relative_path)
        .join(File, File.id == Symbol.file_id)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            Symbol.repository_id == generation.repository_id,
            Symbol.parser_version == generation.parser_version,
            Symbol.name.in_(tuple(_MAIN_SYMBOL_NAMES)),
        )
        .order_by(Symbol.qualified_name, Symbol.id)
        .limit(limit * 3)
        .all()
    )
    symbols: list[Symbol] = []
    for symbol, path in rows:
        if is_test_path(str(path)):
            continue
        symbols.append(symbol)
        if len(symbols) >= limit:
            break
    return symbols


def _entry_catalog_files(
    db: Session,
    generation_id: str,
    question: str,
    *,
    limit: int,
) -> list[File]:
    terms = list(extract_search_terms(question))
    q_lower = question.lower()
    if _ENTRY_ANCHOR.search(question):
        for hint in _ENTRY_PATH_HINTS:
            if generation_has_path(db, generation_id, hint):
                terms.append(hint)
    seen: set[str] = set()
    ordered: list[File] = []
    for batch in (_files_matching_terms(db, generation_id, terms, limit=limit * 2),):
        for file in batch:
            if file.id in seen or is_test_path(file.relative_path):
                continue
            seen.add(file.id)
            ordered.append(file)
            if len(ordered) >= limit:
                return ordered
    if _ENTRY_ANCHOR.search(question):
        filters = []
        for hint in _ENTRY_PATH_HINTS:
            if hint in q_lower or generation_has_path(db, generation_id, hint):
                filters.append(File.relative_path.ilike(f"%{hint}%"))
        if filters:
            rows = (
                db.query(File)
                .join(GenerationFile, GenerationFile.file_id == File.id)
                .filter(GenerationFile.index_generation_id == generation_id, or_(*filters))
                .order_by(File.relative_path, File.id)
                .limit(limit * 2)
                .all()
            )
            for file in rows:
                if file.id in seen or is_test_path(file.relative_path):
                    continue
                seen.add(file.id)
                ordered.append(file)
                if len(ordered) >= limit:
                    break
    return ordered


def catalog_trace_anchor_choices(
    db: Session,
    generation: IndexGeneration,
    question: str,
    *,
    limit: int = 8,
) -> list[ChunkChoice]:
    """Driver/main symbols and entry-shaped files from the indexed catalog only."""
    choices: list[ChunkChoice] = []
    seen: set[str] = set()

    def add(choice: ChunkChoice | None) -> None:
        if choice is None or choice.chunk_id in seen or len(choices) >= limit:
            return
        seen.add(choice.chunk_id)
        choices.append(choice)

    for symbol in _main_entry_symbols(db, generation, limit=4):
        add(pick_best_symbol_chunk(db, generation.id, symbol.id))

    files = _entry_catalog_files(db, generation.id, question, limit=6)
    if files:
        for choice in chunk_choices_for_files(
            db,
            generation.id,
            files,
            limit=limit,
            bodies_per_file=2,
        ):
            add(choice)

    return choices[:limit]
