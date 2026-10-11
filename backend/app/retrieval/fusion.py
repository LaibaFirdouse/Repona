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
    "REFERENCES": 1,
    "IMPORTS": 2,
    "DEPENDS_ON": 3,
    "DECLARES": 4,
    "CONTAINS": 5,
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


def name_only_chunks(chunks: list[ChunkChoice]) -> list[ChunkChoice]:
    bodies = [chunk for chunk in chunks if chunk.kind == "symbol_body"]
    if bodies:
        return sorted(bodies, key=lambda chunk: (chunk.ordinal, chunk.chunk_id))[:1]
    parts = [chunk for chunk in chunks if chunk.kind == "symbol_part"]
    return sorted(parts, key=lambda chunk: (chunk.ordinal, chunk.chunk_id))


def materialize_anchors(
    fused: list[str],
    hits_by_anchor: dict[str, list[str]],
    chunks_by_id: dict[str, ChunkChoice],
    chunks_by_symbol: dict[str, list[ChunkChoice]],
) -> list[ChunkChoice]:
    chosen: list[ChunkChoice] = []
    seen: set[str] = set()
    for anchor in fused:
        hit_ids = hits_by_anchor.get(anchor, [])
        if hit_ids:
            group = [chunks_by_id[chunk_id] for chunk_id in hit_ids if chunk_id in chunks_by_id]
        else:
            group = name_only_chunks(chunks_by_symbol.get(anchor, []))
        group.sort(key=lambda chunk: (KIND_ORDER.get(chunk.kind, 9), chunk.ordinal, chunk.chunk_id))
        for chunk in group:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chosen.append(chunk)
    return chosen


def neighbor_chunk(chunks: list[ChunkChoice]) -> ChunkChoice | None:
    bodies = [chunk for chunk in chunks if chunk.kind == "symbol_body"]
    if bodies:
        return sorted(bodies, key=lambda chunk: (chunk.ordinal, chunk.chunk_id))[0]
    parts = [chunk for chunk in chunks if chunk.kind == "symbol_part"]
    if not parts:
        return None
    return sorted(parts, key=lambda chunk: (chunk.ordinal, chunk.chunk_id))[0]


def allow_edge(relationship: str, outgoing: bool, anchor_role: str, other_role: str) -> bool:
    if relationship in {"CALLS", "REFERENCES"}:
        return True
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
