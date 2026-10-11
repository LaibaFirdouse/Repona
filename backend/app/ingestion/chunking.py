from dataclasses import dataclass

from app.ingestion.versions import MAX_CHUNK_BYTES


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


def build_chunks(
    payload: bytes,
    symbol_spans: list[tuple[tuple[str, str, str, int], str, Span]],
    import_spans: list[Span],
    limit: int = MAX_CHUNK_BYTES,
) -> list[ChunkDraft]:
    """symbol_spans entries are (ordinal key, kind, span). Module symbols are omitted from chunks."""

    chunks: list[ChunkDraft] = []
    claimed: list[Span] = []
    for key, kind, span in symbol_spans:
        if kind == "module" or span.start_byte == span.end_byte:
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
        for piece in split_on_newlines(payload, gap.start_byte, gap.end_byte, limit):
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
