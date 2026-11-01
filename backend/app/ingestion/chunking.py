import re
from dataclasses import dataclass

from app.ingestion.versions import MAX_CHUNK_BYTES

# Symbol kinds whose body is mostly other symbols. Their own chunk keeps only the header
# (declaration, docstring, fields before the first member); each member is chunked on its own.
# Chunking the whole class as well duplicated every method in the index and the prompt.
CONTAINER_KINDS = frozenset({"class", "struct", "interface", "trait", "impl", "enum"})
_HAS_WORD = re.compile(rb"[A-Za-z0-9]")
_WORD_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


@dataclass(frozen=True)
class Span:
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class ChunkDraft:
    anchor: str
    symbol_ordinal_key: tuple[str, str, str, int] | None
    kind: str
    ordinal: int
    start_byte: int
    end_byte: int


def split_on_newlines(payload: bytes, start: int, end: int, limit: int = MAX_CHUNK_BYTES) -> list[Span]:
    parts: list[Span] = []
    cursor = start
    while cursor < end:
        if end - cursor <= limit:
            parts.append(Span(cursor, end))
            break
        window_end = cursor + limit
        newline = payload.rfind(b"\n", cursor, window_end)
        if newline >= cursor:
            parts.append(Span(cursor, newline + 1))
            cursor = newline + 1
            continue
        newline = payload.find(b"\n", window_end, end)
        if newline == -1:
            parts.append(Span(cursor, end))
            break
        parts.append(Span(cursor, newline + 1))
        cursor = newline + 1
    return parts


def merge_spans(spans: list[Span]) -> list[Span]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda span: (span.start_byte, span.end_byte))
    merged = [ordered[0]]
    for span in ordered[1:]:
        current = merged[-1]
        if span.start_byte <= current.end_byte:
            merged[-1] = Span(current.start_byte, max(current.end_byte, span.end_byte))
        else:
            merged.append(span)
    return merged


def uncovered_spans(byte_size: int, claimed: list[Span]) -> list[Span]:
    gaps: list[Span] = []
    cursor = 0
    for span in claimed:
        if span.start_byte > cursor:
            gaps.append(Span(cursor, span.start_byte))
        cursor = max(cursor, span.end_byte)
    if cursor < byte_size:
        gaps.append(Span(cursor, byte_size))
    return gaps


def _first_member_starts(symbol_spans: list[tuple[tuple[str, str, str, int], str, Span]]) -> dict[int, int]:
    """For each container symbol (by list index), the start byte of the earliest symbol nested in it."""
    order = sorted(
        range(len(symbol_spans)),
        key=lambda index: (symbol_spans[index][2].start_byte, -symbol_spans[index][2].end_byte),
    )
    firsts: dict[int, int] = {}
    for position, index in enumerate(order):
        _, kind, span = symbol_spans[index]
        if kind not in CONTAINER_KINDS:
            continue
        for later in order[position + 1 :]:
            member = symbol_spans[later][2]
            if member.start_byte >= span.end_byte:
                break
            if member.end_byte <= span.end_byte and (member.start_byte, member.end_byte) != (span.start_byte, span.end_byte):
                firsts[index] = member.start_byte
                break
    return firsts


def _header_end(payload: bytes, start: int, end: int) -> int:
    """End of a container header: drop trailing blank space, keep the final line break."""
    while end > start and payload[end - 1] in b" \t\r\n":
        end -= 1
    if start < end < len(payload) and payload[end : end + 1] == b"\n":
        end += 1
    return end


def _trim_gap(payload: bytes, gap: Span) -> Span | None:
    """Drop leading and trailing lines with no letters or digits (blank lines, closing braces)."""
    first = _HAS_WORD.search(payload, gap.start_byte, gap.end_byte)
    if first is None:
        return None
    start = max(gap.start_byte, payload.rfind(b"\n", gap.start_byte, first.start()) + 1)
    last = gap.end_byte - 1
    while last > first.start() and payload[last] not in _WORD_BYTES:
        last -= 1
    newline = payload.find(b"\n", last, gap.end_byte)
    end = gap.end_byte if newline == -1 else newline + 1
    return Span(start, end)


def build_chunks(
    payload: bytes,
    symbol_spans: list[tuple[tuple[str, str, str, int], str, Span]],
    import_spans: list[Span],
    limit: int = MAX_CHUNK_BYTES,
) -> list[ChunkDraft]:
    """symbol_spans entries are (ordinal key, kind, span). Module symbols are omitted from chunks.

    A container (class, struct, ...) with nested symbols contributes only its header; the nested
    symbols carry the rest. Gaps between symbols lose leading and trailing lines with no letters or
    digits (blank lines, closing braces); a gap with nothing else is not a chunk.
    """

    chunks: list[ChunkDraft] = []
    claimed: list[Span] = []
    member_starts = _first_member_starts(symbol_spans)
    for index, (key, kind, span) in enumerate(symbol_spans):
        if kind == "module" or span.start_byte == span.end_byte:
            continue
        if index in member_starts:
            span = Span(span.start_byte, _header_end(payload, span.start_byte, member_starts[index]))
            if span.start_byte == span.end_byte:
                continue
        claimed.append(span)
        pieces = split_on_newlines(payload, span.start_byte, span.end_byte, limit)
        chunk_kind = "symbol_part" if span.end_byte - span.start_byte > limit else "symbol_body"
        for ordinal, piece in enumerate(pieces):
            chunks.append(
                ChunkDraft(
                    anchor="symbol",
                    symbol_ordinal_key=key,
                    kind=chunk_kind,
                    ordinal=ordinal,
                    start_byte=piece.start_byte,
                    end_byte=piece.end_byte,
                )
            )
    for ordinal, span in enumerate(sorted(import_spans, key=lambda item: item.start_byte)):
        claimed.append(span)
        chunks.append(
            ChunkDraft(
                anchor="file",
                symbol_ordinal_key=None,
                kind="import_block",
                ordinal=ordinal,
                start_byte=span.start_byte,
                end_byte=span.end_byte,
            )
        )
    gaps = uncovered_spans(len(payload), merge_spans(claimed))
    if not gaps:
        return chunks
    covers_file = len(gaps) == 1 and gaps[0].start_byte == 0 and gaps[0].end_byte == len(payload)
    if covers_file and len(payload) <= limit:
        chunks.append(
            ChunkDraft(
                anchor="file",
                symbol_ordinal_key=None,
                kind="file_overview",
                ordinal=0,
                start_byte=0,
                end_byte=len(payload),
            )
        )
        return chunks
    ordinal = 0
    for gap in gaps:
        trimmed = _trim_gap(payload, gap)
        if trimmed is None:
            continue
        for piece in split_on_newlines(payload, trimmed.start_byte, trimmed.end_byte, limit):
            if not _HAS_WORD.search(payload, piece.start_byte, piece.end_byte):
                continue
            chunks.append(
                ChunkDraft(
                    anchor="file",
                    symbol_ordinal_key=None,
                    kind="non_code",
                    ordinal=ordinal,
                    start_byte=piece.start_byte,
                    end_byte=piece.end_byte,
                )
            )
            ordinal += 1
    return chunks
