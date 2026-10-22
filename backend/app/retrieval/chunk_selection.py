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

_HOW_EXACTLY = re.compile(r"\bhow exactly\b", re.I)

_CODE_TRACE = re.compile(
    r"\b("
    r"trace|walk through|step by step|call sequence|which functions?|what functions?|"
    r"logic behind|functions?/.+logic|behind (?:it|this|that)"
    r")\b",
    re.I,
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

_BUILD_FILE_NAMES = frozenset({"makefile", "cmakelists.txt"})

_DOMAIN_IMPL = re.compile(
    r"\b("
    r"queue|ring|enqueue|dequeue|spsc|mpsc|mpmc|lock[- ]?free|mutex|channel|buffer"
    r")\b",
    re.I,
)


def is_meta_config_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    return name in _META_CONFIG_NAMES


def wants_substantive_implementation(question: str, history: list[QaTurn]) -> bool:
    text = question.strip()
    if _HOW_EXACTLY.search(text) or _IMPLEMENTATION_QUESTION.search(text):
        return True
    return any(
        _HOW_EXACTLY.search(turn.question) or _IMPLEMENTATION_QUESTION.search(turn.question)
        for turn in history[-4:]
    )


def wants_code_trace(question: str, history: list[QaTurn]) -> bool:
    if _CODE_TRACE.search(question.strip()):
        return True
    return any(_CODE_TRACE.search(turn.question) for turn in history[-4:])


def wants_domain_implementation(question: str, history: list[QaTurn]) -> bool:
    """Data-structure / algorithm questions that should prefer headers and lib code over harness files."""
    if _DOMAIN_IMPL.search(question.strip()):
        return True
    return any(_DOMAIN_IMPL.search(turn.question) for turn in history[-4:])


def _is_build_metadata_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    if is_meta_config_path(relative_path):
        return True
    return name in _BUILD_FILE_NAMES


def deprioritize_build_metadata_spans(spans: list) -> list:
    """Push README/Makefile/CMake-style paths behind implementation sources when boosting code bodies."""
    front = [span for span in spans if not _is_build_metadata_path(_choice_path(span))]
    back = [span for span in spans if _is_build_metadata_path(_choice_path(span))]
    return front + back if front else spans


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
    question: str = "",
    limit: int = 24,
) -> list[ChunkChoice]:
    from app.retrieval.catalog_context import catalog_implementation_chunk_choices

    return catalog_implementation_chunk_choices(db, generation, question, limit=limit)


def _choice_path(span) -> str:
    if isinstance(span, dict):
        return str(span.get("relative_path") or "")
    return str(getattr(span, "relative_path", "") or "")
