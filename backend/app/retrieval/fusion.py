"""Rank anchors from the three lanes and choose the spans those anchors contribute."""

from __future__ import annotations

from dataclasses import dataclass

RRF_CONSTANT = 60
KIND_ORDER = {
    "symbol_body": 0,
    "symbol_part": 1,
    "import_block": 2,
    "file_overview": 3,
    "non_code": 4,
}
EDGE_ORDER = {
    "CALLS": 0,
    "ROUTES_TO": 1,
    "REFERENCES": 2,
    "IMPORTS": 3,
    "DEPENDS_ON": 4,
    "DECLARES": 5,
    "CONTAINS": 6,
}


@dataclass(frozen=True)
class LaneHit:
    anchor_id: str
    chunk_id: str | None
    rank: int


@dataclass(frozen=True)
class ChunkChoice:
    chunk_id: str
    anchor_id: str
    kind: str
    ordinal: int


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    relationship_type: str
    outgoing: bool
    anchor_id: str
    anchor_rank: int
    other_id: str
    other_role: str
    mentioned_name: str
    resolution: str


def consecutive_ranks(hits: list[LaneHit]) -> list[tuple[str, int]]:
    """Collapse one lane to its best hit per anchor, then number those anchors from 1."""
    best: dict[str, int] = {}
    for hit in hits:
        current = best.get(hit.anchor_id)
        if current is None or hit.rank < current:
            best[hit.anchor_id] = hit.rank
    ordered = sorted(best, key=lambda anchor: (best[anchor], anchor))
    return [(anchor, index) for index, anchor in enumerate(ordered, start=1)]


def fuse_anchors(lanes: list[list[tuple[str, int]]]) -> list[str]:
    scores: dict[str, float] = {}
    for lane in lanes:
        for anchor, rank in lane:
            scores[anchor] = scores.get(anchor, 0.0) + 1.0 / (RRF_CONSTANT + rank)
    return sorted(scores, key=lambda anchor: (-scores[anchor], anchor))


def chunk_ids_for_anchor(hits: list[LaneHit]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for hit in hits:
        if hit.chunk_id is None:
            continue
        seen.setdefault(hit.anchor_id, set())
        if hit.chunk_id in seen[hit.anchor_id]:
            continue
        seen[hit.anchor_id].add(hit.chunk_id)
        grouped.setdefault(hit.anchor_id, []).append(hit.chunk_id)
    return grouped


def _span_bytes(choice: ChunkChoice, chunk_sizes: dict[str, int]) -> int:
    return chunk_sizes.get(choice.chunk_id, 0)


def ordered_symbol_parts(choices: list[ChunkChoice]) -> list[ChunkChoice]:
    """Return symbol_part chunks in source order (ordinal), preserving identity."""
    parts = [chunk for chunk in choices if chunk.kind == "symbol_part"]
    return sorted(parts, key=lambda chunk: (chunk.ordinal, chunk.chunk_id))


def representative_chunk_choices(
    choices: list[ChunkChoice],
    chunk_sizes: dict[str, int] | None = None,
) -> list[ChunkChoice]:
    """Prefer a full symbol_body; otherwise return ordered symbol_part constituents.

    Oversized symbols are stored only as symbol_part chunks. Selecting a single
    "largest" part and treating it as the whole symbol loses validation logic,
    mid-body branches, and return paths. Return every part in source order.
    """
    if not choices:
        return []
    sizes = chunk_sizes or {}
    bodies = [chunk for chunk in choices if chunk.kind == "symbol_body"]
    if bodies:
        pick = max(bodies, key=lambda chunk: (_span_bytes(chunk, sizes), chunk.ordinal))
        return [pick]
    parts = ordered_symbol_parts(choices)
    if parts:
        return parts
    avoid = {chunk for chunk in choices if chunk.kind in {"import_block", "file_overview"}}
    pool = [chunk for chunk in choices if chunk not in avoid] or list(choices)
    pick = max(pool, key=lambda chunk: (_span_bytes(chunk, sizes), chunk.ordinal))
    return [pick]


def name_only_chunks(
    chunks: list[ChunkChoice],
    chunk_sizes: dict[str, int] | None = None,
) -> list[ChunkChoice]:
    return representative_chunk_choices(chunks, chunk_sizes)


def preserve_named_spans(spans: list[ChunkChoice], named: list[ChunkChoice], limit: int) -> list[ChunkChoice]:
    """Keep spans for symbols the question names, then the existing order, within the span cap.

    Fusion order is unchanged. A named span that would fall past the cap is moved into the kept set.
    """
    if limit < 1:
        return []
    named_ids = []
    front: list[ChunkChoice] = []
    for choice in named:
        if choice.chunk_id in named_ids:
            continue
        named_ids.append(choice.chunk_id)
        front.append(choice)
    rest = [span for span in spans if span.chunk_id not in named_ids]
    return (front + rest)[:limit]


def materialize_anchors(
    fused: list[str],
    hits_by_anchor: dict[str, list[str]],
    chunks_by_id: dict[str, ChunkChoice],
    chunks_by_symbol: dict[str, list[ChunkChoice]],
    chunk_sizes: dict[str, int] | None = None,
) -> list[ChunkChoice]:
    chosen: list[ChunkChoice] = []
    seen: set[str] = set()
    for anchor in fused:
        hit_ids = hits_by_anchor.get(anchor, [])
        if hit_ids:
            group = [chunks_by_id[chunk_id] for chunk_id in hit_ids if chunk_id in chunks_by_id]
            id_order = {chunk_id: index for index, chunk_id in enumerate(hit_ids)}
            group.sort(
                key=lambda chunk: (
                    id_order.get(chunk.chunk_id, 999),
                    KIND_ORDER.get(chunk.kind, 9),
                    chunk.ordinal,
                    chunk.chunk_id,
                )
            )
        else:
            group = representative_chunk_choices(chunks_by_symbol.get(anchor, []), chunk_sizes)
        for chunk in group:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chosen.append(chunk)
    return chosen


def neighbor_chunk(
    chunks: list[ChunkChoice],
    chunk_sizes: dict[str, int] | None = None,
) -> ChunkChoice | None:
    """Seed chunk for a graph neighbor: prefer body, else the first part in source order."""
    picked = representative_chunk_choices(chunks, chunk_sizes)
    if not picked:
        return None
    # For multi-part symbols, seed with the earliest ordinal so sibling expansion
    # can walk forward in source order rather than jumping to the largest middle slice.
    if len(picked) > 1 and all(item.kind == "symbol_part" for item in picked):
        return picked[0]
    return picked[0]


def allow_edge(relationship: str, outgoing: bool, anchor_role: str, other_role: str) -> bool:
    if relationship in {"CALLS", "REFERENCES"}:
        return True
    # Protocol correspondence (HTTP client → handler). Not a CALLS edge; still
    # admissible for evidence neighbor expansion when both ends are symbols.
    if relationship == "ROUTES_TO":
        del outgoing
        return anchor_role == "symbol" and other_role == "symbol"
    if relationship == "IMPORTS":
        return anchor_role == "file" if outgoing else anchor_role in {"file", "module"}
    if relationship == "DEPENDS_ON":
        return anchor_role == "module" and other_role == "module"
    if relationship == "DECLARES":
        return not outgoing and anchor_role in {"symbol", "module"}
    if relationship == "CONTAINS":
        return not outgoing and anchor_role == "file"
    return False


def select_edges(edges: list[GraphEdge], limit: int = 24) -> list[GraphEdge]:
    ordered = sorted(
        edges,
        key=lambda edge: (
            edge.anchor_rank,
            EDGE_ORDER.get(edge.relationship_type, 9),
            0 if edge.outgoing else 1,
            edge.edge_id,
        ),
    )
    return ordered[:limit]
