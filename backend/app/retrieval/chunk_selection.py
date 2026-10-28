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

_GENERIC_HOW_IT_WORKS = re.compile(r"\bhow (?:is|does|are|do) .+ work\b", re.I)

_IMPLEMENTATION_STRONG = re.compile(
    r"\b("
    r"workflow|workflows|end[- ]to[- ]end|request flow|"
    r"how (?:is|does|are|do) .+ (?:stored|persist|implemented|make sure|ensure|guarantee|validate|verify|pick|choose|select|route|handle)|"
    r"implemented in code|in code|data (?:is )?stored|persist(?:ed|ence)?|schema|api route|route handler|"
    r"functions? implemented|list and explain|what (?:are|is) the different|"
    r"implementation|source code|from code|not (?:the )?readme|"
    r"http request|which code runs|pick which code|request handling|errors during request"
    r")\b",
    re.I,
)

_IMPLEMENTATION_QUESTION = re.compile(
    r"\b("
    r"workflow|workflows|end[- ]to[- ]end|request flow|how (?:is|does|are|do) .+ (?:work|stored|persist|implemented|make sure|ensure|guarantee|validate|verify|pick|choose|select|route|handle)|"
    r"implemented in code|in code|data (?:is )?stored|persist(?:ed|ence)?|schema|api route|route handler|"
    r"functions? implemented|list and explain|what (?:are|is) the different|"
    r"implementation|source code|from code|not (?:the )?readme|"
    r"http request|which code runs|pick which code|request handling|errors during request"
    r")\b",
    re.I,
)

_QUESTION_ABOUT_TESTS = re.compile(
    r"\b(tests?|unit tests?|pytest|unittest|test suite|test coverage|what about tests)\b",
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


def wants_trace_intent_substantive(question: str, history: list[QaTurn]) -> bool:
    """Strong implementation cues for execution_trace intent—not bare 'how does X work' alone."""
    text = question.strip()
    if _HOW_EXACTLY.search(text) or _IMPLEMENTATION_STRONG.search(text):
        return True
    if _GENERIC_HOW_IT_WORKS.search(text):
        return False
    return any(
        _HOW_EXACTLY.search(turn.question) or _IMPLEMENTATION_STRONG.search(turn.question)
        for turn in history[-4:]
    )


def chunk_byte_length(chunk: Chunk) -> int:
    return max(0, int(chunk.end_byte) - int(chunk.start_byte))


def is_substantive_chunk_kind(kind: str, raw_bytes: int, *, min_bytes: int | None = None) -> bool:
    """Bodies and sufficiently large symbol_part slices both carry implementation text.

    Oversized symbols are stored only as symbol_part chunks; treating every part as
    non-substantive causes README/import matches to win implementation questions.
    """
    floor = min_bytes if min_bytes is not None else settings.answer_min_substantive_chunk_bytes
    if kind in {"symbol_body", "symbol_part"} and raw_bytes >= floor:
        return True
    return False


def loaded_span_is_substantive(span: dict, *, min_bytes: int | None = None) -> bool:
    kind = str(span.get("chunk_kind") or "")
    raw = int(span.get("raw_bytes") or 0)
    return is_substantive_chunk_kind(kind, raw, min_bytes=min_bytes)


def deprioritize_thin_spans(loaded: list[dict], *, min_bytes: int | None = None) -> list[dict]:
    """Push stub symbol slices behind chunks large enough to explain behavior."""
    floor = min_bytes if min_bytes is not None else settings.answer_min_substantive_chunk_bytes
    front = [span for span in loaded if loaded_span_is_substantive(span, min_bytes=floor)]
    back = [span for span in loaded if not loaded_span_is_substantive(span, min_bytes=floor)]
    return front + back if front else loaded


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
        # File-level representative: earliest part keeps source order for display/path hits.
        return min(parts, key=lambda chunk: (chunk.ordinal, chunk.id))
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


def _symbol_row_choices(db: Session, generation_id: str, symbol_id: str) -> tuple[list[Chunk], list[ChunkChoice]]:
    rows = (
        db.query(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.symbol_id == symbol_id)
        .all()
    )
    from app.retrieval.fusion import ChunkChoice

    choices = [ChunkChoice(row.id, symbol_id, row.kind, row.ordinal) for row in rows]
    return rows, choices


def pick_best_symbol_chunk(db: Session, generation_id: str, symbol_id: str) -> ChunkChoice | None:
    """Seed chunk for a known symbol (graph/name paths). Prefer body; else first part."""
    from app.retrieval.fusion import neighbor_chunk

    rows, choices = _symbol_row_choices(db, generation_id, symbol_id)
    if not choices:
        return None
    sizes = {row.id: max(0, row.end_byte - row.start_byte) for row in rows}
    return neighbor_chunk(choices, sizes)


def pick_symbol_implementation_chunks(
    db: Session,
    generation_id: str,
    symbol_id: str,
) -> list[ChunkChoice]:
    """All implementation chunks for a known symbol: body, or ordered symbol_part parts."""
    from app.retrieval.fusion import representative_chunk_choices

    rows, choices = _symbol_row_choices(db, generation_id, symbol_id)
    if not choices:
        return []
    sizes = {row.id: max(0, row.end_byte - row.start_byte) for row in rows}
    return representative_chunk_choices(choices, sizes)


def upgrade_chunk_choice(db: Session, generation_id: str, chunk: Chunk) -> Chunk:
    """Upgrade thin/header hits without relabeling a different symbol as this one.

    Invariants:
    - symbol_part may only upgrade to a symbol_body of the *same* symbol_id.
    - import/overview/non_code may upgrade to a body in the same file (file-level hit).
    - Never substitute an unrelated symbol's body for a requested symbol_part.
    """
    min_bytes = settings.answer_min_substantive_chunk_bytes
    if chunk.kind == "symbol_body" and chunk_byte_length(chunk) >= min_bytes:
        return chunk
    if chunk.kind == "symbol_part":
        if not chunk.symbol_id:
            return chunk
        same_symbol_bodies = (
            db.query(Chunk)
            .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
            .filter(
                GenerationChunk.index_generation_id == generation_id,
                Chunk.symbol_id == chunk.symbol_id,
                Chunk.kind == "symbol_body",
            )
            .all()
        )
        if not same_symbol_bodies:
            return chunk
        return max(same_symbol_bodies, key=lambda item: (item.end_byte - item.start_byte, item.ordinal))
    if chunk.kind not in {"import_block", "file_overview", "non_code"}:
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
    """Upgrade thin/header choices while preserving symbol identity on the ChunkChoice."""
    upgraded: list[ChunkChoice] = []
    for span in spans:
        chunk = db.get(Chunk, span.chunk_id)
        if chunk is None:
            upgraded.append(span)
            continue
        best = upgrade_chunk_choice(db, generation_id, chunk)
        if best.id == span.chunk_id:
            upgraded.append(span)
            continue
        # Reject substitutions that would attach a different symbol's body to this anchor.
        if chunk.symbol_id and best.symbol_id and best.symbol_id != chunk.symbol_id:
            upgraded.append(span)
            continue
        if span.anchor_id and best.symbol_id and best.symbol_id != span.anchor_id and chunk.kind == "symbol_part":
            upgraded.append(span)
            continue
        anchor = best.symbol_id or span.anchor_id
        upgraded.append(ChunkChoice(best.id, anchor, best.kind, best.ordinal))
    return upgraded


def deprioritize_meta_config_spans(spans: list) -> list:
    front = [span for span in spans if not is_meta_config_path(_choice_path(span))]
    back = [span for span in spans if is_meta_config_path(_choice_path(span))]
    return front + back


def _adjacent_symbol_parts(
    parts: list[Chunk],
    *,
    seed_ordinal: int | None,
    limit: int,
) -> list[Chunk]:
    """Keep parts around the seed ordinal in source order (validation → mid → return)."""
    ordered = sorted(parts, key=lambda item: (item.ordinal, item.id))
    if not ordered or limit < 1:
        return []
    if seed_ordinal is None:
        return ordered[:limit]
    # Expand outward from the seed so mid-body hits still get head/tail context.
    by_ord = {item.ordinal: item for item in ordered}
    ordinals = sorted(by_ord)
    if seed_ordinal not in by_ord:
        nearest = min(ordinals, key=lambda value: (abs(value - seed_ordinal), value))
        seed_ordinal = nearest
    selected: list[Chunk] = []
    left = right = seed_ordinal
    selected.append(by_ord[seed_ordinal])
    while len(selected) < limit:
        grew = False
        if left - 1 in by_ord:
            left -= 1
            selected.append(by_ord[left])
            grew = True
            if len(selected) >= limit:
                break
        if right + 1 in by_ord:
            right += 1
            selected.append(by_ord[right])
            grew = True
        if not grew:
            break
    return sorted(selected, key=lambda item: (item.ordinal, item.id))


def expand_spans_with_sibling_bodies(
    db: Session,
    generation_id: str,
    spans: list[ChunkChoice],
    *,
    max_bodies_per_file: int | None = None,
    max_added: int = 16,
) -> list[ChunkChoice]:
    """Expand selected symbols with their own ordered parts/bodies; then optional file peers.

    Same-symbol parts come first (invariant C/D). Other symbol bodies from the same
    file are secondary and never replace a requested symbol's identity (invariant B/F).
    """
    cap = max_bodies_per_file if max_bodies_per_file is not None else settings.answer_impl_chunks_per_file
    seen = {span.chunk_id for span in spans}
    extra: list[ChunkChoice] = []
    requested_symbols: dict[str, int | None] = {}
    file_ids: set[str] = set()
    file_level_hits: set[str] = set()

    for span in spans:
        chunk = db.get(Chunk, span.chunk_id)
        if chunk is None:
            continue
        file_ids.add(chunk.file_id)
        if chunk.symbol_id:
            seed = chunk.ordinal if chunk.kind == "symbol_part" else None
            if chunk.symbol_id not in requested_symbols:
                requested_symbols[chunk.symbol_id] = seed
            elif seed is not None and requested_symbols[chunk.symbol_id] is None:
                requested_symbols[chunk.symbol_id] = seed
        if chunk.kind in {"import_block", "file_overview", "non_code"}:
            file_level_hits.add(chunk.file_id)

    for symbol_id, seed_ordinal in requested_symbols.items():
        if len(extra) >= max_added:
            break
        rows, _choices = _symbol_row_choices(db, generation_id, symbol_id)
        bodies = [row for row in rows if row.kind == "symbol_body"]
        parts = [row for row in rows if row.kind == "symbol_part"]
        if bodies:
            candidates = sorted(bodies, key=lambda item: (item.ordinal, item.id))
        else:
            remaining = max(0, max_added - len(extra))
            candidates = _adjacent_symbol_parts(
                parts,
                seed_ordinal=seed_ordinal,
                limit=max(remaining, 1),
            )
        for body in candidates:
            if body.id in seen:
                continue
            if len(extra) >= max_added:
                break
            seen.add(body.id)
            extra.append(ChunkChoice(body.id, symbol_id, body.kind, body.ordinal))

    # Secondary: other bodies in the same file for file-level hits or remaining budget.
    for file_id in file_ids:
        if len(extra) >= max_added:
            break
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
        # Prefer source order over sheer size so control-flow neighbors stay contiguous.
        bodies.sort(key=lambda item: (item.start_byte, item.ordinal, item.id))
        added_for_file = 0
        for body in bodies:
            if body.id in seen:
                continue
            if body.symbol_id and body.symbol_id in requested_symbols:
                continue
            # Only pull unrelated file peers when the hit was file-level or budget remains.
            if file_id not in file_level_hits and added_for_file >= max(1, cap // 2):
                break
            if added_for_file >= cap:
                break
            seen.add(body.id)
            anchor = body.symbol_id or body.file_id
            extra.append(ChunkChoice(body.id, anchor, body.kind, body.ordinal))
            added_for_file += 1
            if len(extra) >= max_added:
                break
    return list(spans) + extra


def is_test_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/")
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or name.startswith("test_")


def question_about_tests(question: str) -> bool:
    return bool(_QUESTION_ABOUT_TESTS.search(question.strip()))


def omit_test_spans_unless_asked(loaded: list[dict], question: str) -> list[dict]:
    """Drop test-file chunks for implementation questions; keep pack if only tests matched."""
    if question_about_tests(question):
        return loaded
    kept = [span for span in loaded if not is_test_path(str(span.get("relative_path") or ""))]
    return kept if kept else loaded


def limit_spans_per_file(loaded: list[dict], *, max_per_file: int = 2) -> list[dict]:
    """Keep the evidence pack diverse—avoid ten unrelated chunks from the same file.

    Once a symbol is admitted, its remaining symbol_part / symbol_body slices are
    kept regardless of the per-file cap so split oversized functions stay intact.
    """
    counts: dict[str, int] = {}
    admitted_symbols: set[str] = set()
    kept: list[dict] = []
    for span in loaded:
        path = str(span.get("relative_path") or "")
        symbol_id = str(span.get("symbol_id") or "")
        kind = str(span.get("chunk_kind") or "")
        if symbol_id and symbol_id in admitted_symbols and kind in {"symbol_body", "symbol_part"}:
            kept.append(span)
            continue
        used = counts.get(path, 0)
        if used >= max_per_file:
            continue
        counts[path] = used + 1
        if symbol_id:
            admitted_symbols.add(symbol_id)
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
