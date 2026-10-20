"""Choose chunks that contain real implementation text, not import headers."""

from __future__ import annotations

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

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
    r"workflow|end[- ]to[- ]end|how (?:is|does|are|do) .+ (?:work|stored|persist)|"
    r"data (?:is )?stored|persist(?:ed|ence)?|mongoose|schema|api route|route handler|"
    r"functions? implemented|what (?:are|is) the different|lib/|app/api|"
    r"implementation|source code|from code|not (?:the )?readme"
    r")\b",
    re.I,
)

_WEB_IMPL_PATH_TERMS = (
    "app/api/",
    "lib/",
    "lib/models",
    "pages/api",
    "src/",
    "server/",
    "components/",
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
            upgraded.append(span)
            continue
        best = max(bodies, key=lambda item: (item.end_byte - item.start_byte, item.ordinal))
        upgraded.append(ChunkChoice(best.id, span.anchor_id, best.kind, best.ordinal))
    return upgraded


def deprioritize_meta_config_spans(spans: list) -> list:
    front = [span for span in spans if not is_meta_config_path(_choice_path(span))]
    back = [span for span in spans if is_meta_config_path(_choice_path(span))]
    return front + back


def filter_meta_config_when_impl(loaded: list[dict], *, min_keep: int = 3) -> list[dict]:
    impl = [span for span in loaded if not is_meta_config_path(str(span.get("relative_path") or ""))]
    if len(impl) >= min_keep:
        return impl
    return loaded


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

    filters = [File.relative_path.ilike(f"%{term}%") for term in _WEB_IMPL_PATH_TERMS]
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
    for file in files:
        best = representative_chunk(by_file.get(file.id, []))
        if best is not None:
            add_chunk(best)
        if len(choices) >= limit:
            break
    return choices


def _choice_path(span) -> str:
    if isinstance(span, dict):
        return str(span.get("relative_path") or "")
    return str(getattr(span, "relative_path", "") or "")
