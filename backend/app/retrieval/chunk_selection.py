"""Choose chunks that contain real implementation text, not import headers."""

from __future__ import annotations

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.catalog import Chunk, GenerationChunk, GenerationFile, File, IndexGeneration
from app.retrieval.conversation_context import QaTurn
from app.retrieval.fusion import ChunkChoice

_META_CONFIG_NAMES = frozenset(
    {
        "readme.md",
        "readme",
        "readme.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "tsconfig.json",
        "jsconfig.json",
        "go.mod",
        "cargo.toml",
        "pyproject.toml",
        "setup.py",
        "composer.json",
    }
)

_IMPLEMENTATION_QUESTION = re.compile(
    r"\b("
    r"workflow|workflows|end[- ]to[- ]end|request flow|how (?:is|does|are|do) .+ (?:work|stored|persist|implemented)|"
    r"implemented in code|in code|data (?:is )?stored|persist(?:ed|ence)?|schema|api route|route handler|"
    r"functions? implemented|list and explain|what (?:are|is) the different|"
    r"implementation|source code|from code|not (?:the )?readme"
    r")\b",
    re.I,
)

_IMPL_PATH_TERMS = (
    "app/",
    "lib/",
    "internal/",
    "cmd/",
    "backend/",
    "server/",
    "src/",
    "pages/",
    "components/",
    "handlers/",
    "routes/",
    "api/",
)


def is_meta_config_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    return name in _META_CONFIG_NAMES


def wants_substantive_implementation(question: str, history: list[QaTurn]) -> bool:
    if _IMPLEMENTATION_QUESTION.search(question.strip()):
        return True
    return any(_IMPLEMENTATION_QUESTION.search(turn.question) for turn in history[-4:])


def representative_chunk(chunks: list[Chunk]) -> Chunk | None:
    """Prefer the largest symbol body in a file—not import lines or file overviews."""
    if not chunks:
        return None
    bodies = [chunk for chunk in chunks if chunk.kind == "symbol_body"]
    if bodies:
        return max(bodies, key=lambda chunk: (chunk.end_byte - chunk.start_byte, chunk.ordinal))
    parts = [chunk for chunk in chunks if chunk.kind == "symbol_part"]
    if parts:
        return max(parts, key=lambda chunk: (chunk.end_byte - chunk.start_byte, chunk.ordinal))
    avoid = {chunk for chunk in chunks if chunk.kind in {"import_block", "file_overview"}}
    pool = [chunk for chunk in chunks if chunk not in avoid] or list(chunks)
    return max(pool, key=lambda chunk: (chunk.end_byte - chunk.start_byte, chunk.ordinal))


def pick_file_chunk(chunks: list[Chunk]) -> Chunk | None:
    """Single chunk to represent a file in path-based retrieval."""
    return representative_chunk(chunks)


def chunk_byte_sizes(db: Session, generation_id: str, chunk_ids: list[str]) -> dict[str, int]:
    if not chunk_ids:
        return {}
    rows = (
        db.query(Chunk.id, Chunk.start_byte, Chunk.end_byte)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.id.in_(chunk_ids))
        .all()
    )
    return {row.id: max(0, row.end_byte - row.start_byte) for row in rows}


def pick_best_symbol_chunk(db: Session, generation_id: str, symbol_id: str) -> ChunkChoice | None:
    """Best symbol_body (by span size) for graph and name expansion paths."""
    from app.retrieval.fusion import ChunkChoice, neighbor_chunk

    rows = (
        db.query(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.symbol_id == symbol_id)
        .all()
    )
    if not rows:
        return None
    choices = [ChunkChoice(row.id, symbol_id, row.kind, row.ordinal) for row in rows]
    sizes = {row.id: max(0, row.end_byte - row.start_byte) for row in rows}
    return neighbor_chunk(choices, sizes)


def upgrade_chunk_choice(db: Session, generation_id: str, chunk: Chunk) -> Chunk:
    """Prefer a symbol body from the same file when the hit is an import/overview slice."""
    if chunk.kind not in {"import_block", "file_overview"}:
        return chunk
    bodies = (
        db.query(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(
            GenerationChunk.index_generation_id == generation_id,
            Chunk.file_id == chunk.file_id,
            Chunk.kind == "symbol_body",
        )
        .all()
    )
    if not bodies:
        return chunk
    return max(bodies, key=lambda item: (item.end_byte - item.start_byte, item.ordinal))


def enrich_spans_with_bodies(db: Session, generation_id: str, spans: list[ChunkChoice]) -> list[ChunkChoice]:
    """Replace import-only span choices with the best symbol_body from the same file."""
    upgraded: list[ChunkChoice] = []
    for span in spans:
        if span.kind not in {"import_block", "file_overview"}:
            upgraded.append(span)
            continue
        chunk = db.get(Chunk, span.chunk_id)
        if chunk is None:
            upgraded.append(span)
            continue
        best = upgrade_chunk_choice(db, generation_id, chunk)
        upgraded.append(ChunkChoice(best.id, span.anchor_id, best.kind, best.ordinal))
    return upgraded


def deprioritize_meta_config_spans(spans: list) -> list:
    front = [span for span in spans if not is_meta_config_path(_choice_path(span))]
    back = [span for span in spans if is_meta_config_path(_choice_path(span))]
    return front + back


def expand_spans_with_sibling_bodies(
    db: Session,
    generation_id: str,
    spans: list[ChunkChoice],
    *,
    max_bodies_per_file: int | None = None,
    max_added: int = 16,
) -> list[ChunkChoice]:
    """Large files are split into many chunks; retrieval often keeps only one—add sibling bodies."""
    cap = max_bodies_per_file if max_bodies_per_file is not None else settings.answer_impl_chunks_per_file
    seen = {span.chunk_id for span in spans}
    file_ids: set[str] = set()
    for span in spans:
        chunk = db.get(Chunk, span.chunk_id)
        if chunk is not None:
            file_ids.add(chunk.file_id)
    extra: list[ChunkChoice] = []
    for file_id in file_ids:
        bodies = (
            db.query(Chunk)
            .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
            .filter(
                GenerationChunk.index_generation_id == generation_id,
                Chunk.file_id == file_id,
                Chunk.kind == "symbol_body",
            )
            .all()
        )
        bodies.sort(key=lambda item: (item.end_byte - item.start_byte, item.ordinal), reverse=True)
        added_for_file = 0
        for body in bodies:
            if body.id in seen:
                continue
            if added_for_file >= cap:
                break
            seen.add(body.id)
            anchor = body.symbol_id or body.file_id
            extra.append(ChunkChoice(body.id, anchor, body.kind, body.ordinal))
            added_for_file += 1
            if len(extra) >= max_added:
                break
        if len(extra) >= max_added:
            break
    return list(spans) + extra


def limit_spans_per_file(loaded: list[dict], *, max_per_file: int = 2) -> list[dict]:
    """Keep the evidence pack diverse—avoid ten chunks from the same file."""
    counts: dict[str, int] = {}
    kept: list[dict] = []
    for span in loaded:
        path = str(span.get("relative_path") or "")
        used = counts.get(path, 0)
        if used >= max_per_file:
            continue
        counts[path] = used + 1
        kept.append(span)
    return kept if kept else loaded


def implementation_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    limit: int = 24,
) -> list[ChunkChoice]:
    seen: set[str] = set()
    choices: list[ChunkChoice] = []

    def add_chunk(chunk: Chunk, anchor: str | None = None) -> None:
        if chunk.id in seen or len(choices) >= limit:
            return
        seen.add(chunk.id)
        anchor_id = anchor or chunk.symbol_id or chunk.file_id
        choices.append(ChunkChoice(chunk.id, anchor_id, chunk.kind, chunk.ordinal))

    filters = [File.relative_path.ilike(f"%{term}%") for term in _IMPL_PATH_TERMS]
    files = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation.id, or_(*filters))
        .order_by(File.relative_path, File.id)
        .limit(limit * 4)
        .all()
    )
    files = [row for row in files if not is_meta_config_path(row.relative_path)]
    if not files:
        return choices
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
    per_file = max(2, min(settings.answer_impl_chunks_per_file, 4))
    for file in files:
        pool = by_file.get(file.id, []) or []
        bodies = [c for c in pool if c.kind == "symbol_body"]
        bodies.sort(key=lambda item: (item.end_byte - item.start_byte, item.ordinal), reverse=True)
        if not bodies:
            best = representative_chunk(pool)
            if best is not None:
                add_chunk(best)
        else:
            for body in bodies[:per_file]:
                add_chunk(body)
        if len(choices) >= limit:
            break
    return choices


def _choice_path(span) -> str:
    if isinstance(span, dict):
        return str(span.get("relative_path") or "")
    return str(getattr(span, "relative_path", "") or "")
