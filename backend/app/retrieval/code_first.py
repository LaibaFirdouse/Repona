"""Prefer implementation sources over README when the user asks for code."""

from __future__ import annotations

import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.catalog import Chunk, File, GenerationChunk, GenerationFile, IndexGeneration
from app.retrieval.conversation_context import QaTurn
from app.retrieval.fusion import ChunkChoice

_CODE_FIRST = re.compile(
    r"\b("
    r"from (?:the )?code|from source|source code|implementation|implemented|"
    r"in include/|under include/|\.hpp|\.cpp|\.c\b|not (?:the )?readme|"
    r"rather than (?:the )?readme|instead of (?:the )?readme|"
    r"tell (?:me )?(?:that |this )?from code|can you tell .+ from code|"
    r"how (?:is|are|does|do) .+ (?:work|implemented)|explain how .+ (?:is )?implement"
    r")\b",
    re.I,
)

_CODE_PATH_TERMS = (
    "include/",
    "src/",
    "allocator",
    "_allocator",
    ".hpp",
    ".cpp",
    ".cc",
    ".c",
    "freelist",
    "bitmap",
)


def code_first_question(question: str) -> bool:
    return bool(_CODE_FIRST.search(question.strip()))


def wants_implementation_evidence(question: str, history: list[QaTurn]) -> bool:
    if code_first_question(question):
        return True
    return any(code_first_question(turn.question) for turn in history[-4:])


def is_readme_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    return name in {"readme.md", "readme", "readme.txt"}


def deprioritize_readme_spans(spans: list) -> list:
    front = [span for span in spans if not is_readme_path(_span_path(span))]
    back = [span for span in spans if is_readme_path(_span_path(span))]
    return front + back


def filter_readme_when_code_first(loaded: list[dict], *, min_keep: int = 2) -> list[dict]:
    non_readme = [span for span in loaded if not is_readme_path(str(span.get("relative_path") or ""))]
    if len(non_readme) >= min_keep:
        return non_readme
    return loaded


def code_first_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    limit: int = 24,
) -> list[ChunkChoice]:
    seen: set[str] = set()
    choices: list[ChunkChoice] = []

    def add_chunk(chunk: Chunk) -> None:
        if chunk.id in seen or len(choices) >= limit:
            return
        seen.add(chunk.id)
        anchor = chunk.symbol_id or chunk.file_id
        choices.append(ChunkChoice(chunk.id, anchor, chunk.kind, chunk.ordinal))

    filters = [File.relative_path.ilike(f"%{term}%") for term in _CODE_PATH_TERMS]
    files = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(GenerationFile.index_generation_id == generation.id, or_(*filters))
        .order_by(File.relative_path, File.id)
        .limit(limit * 3)
        .all()
    )
    files = [f for f in files if not is_readme_path(f.relative_path)]
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
        pool = by_file.get(file.id, [])
        if not pool:
            continue
        body = [c for c in pool if c.kind in {"symbol_body", "non_code", "file_overview", "import_block"}]
        pick = min(body or pool, key=lambda c: (0 if c.kind == "symbol_body" else 1, c.end_byte - c.start_byte, c.ordinal))
        add_chunk(pick)
        if len(choices) >= limit:
            break
    return choices


def _span_path(span) -> str:
    if isinstance(span, dict):
        return str(span.get("relative_path") or "")
    return str(getattr(span, "relative_path", "") or "")


CODE_FIRST_SUFFIX = (
    "\nThe user wants implementation detail from source files (include/, src/, .hpp, .cpp), "
    "not README.md marketing or benchmark tables. Ground every claim in those implementation excerpts. "
    "Do not say \"the excerpts\", \"this packet\", or \"README alone\"; explain what the code does. "
    "If an allocator header is missing from the excerpts, say which files you do see and what they show—briefly."
)
