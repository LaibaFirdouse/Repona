"""Multi-hop Neo4j expansion from fused retrieval anchors."""

from __future__ import annotations

_CALL_EXPAND = frozenset({"CALLS", "REFERENCES"})

_EDGE_QUERY = """
MATCH (generation:IndexGeneration {id: $generation_id})
MATCH (anchor)-[rel]-(other)
WHERE anchor.id IN $anchor_ids
  AND rel.index_generation_id = generation.id
  AND rel.writer_epoch = generation.graph_epoch
  AND type(rel) <> 'HAS_SOURCE'
  AND type(rel) <> 'HAS_GENERATION'
RETURN anchor.id AS anchor_id,
       other.id AS other_id,
       type(rel) AS relationship_type,
       rel.edge_id AS edge_id,
       coalesce(rel.mentioned_name, '') AS mentioned_name,
       coalesce(rel.resolution, '') AS resolution,
       startNode(rel).id = anchor.id AS outgoing
"""


def next_symbol_frontier(
    frontier: set[str],
    rows: list[dict],
    roles: dict[str, str],
    *,
    limit: int,
) -> list[str]:
    """Symbol ids one hop away via CALLS/REFERENCES from the current frontier."""
    next_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row["relationship_type"] not in _CALL_EXPAND:
            continue
        anchor_id = row["anchor_id"]
        if anchor_id not in frontier:
            continue
        other_id = row["other_id"]
        expand_id = other_id if bool(row["outgoing"]) else other_id
        if roles.get(expand_id) != "symbol":
            continue
        if expand_id in frontier or expand_id in seen:
            continue
        seen.add(expand_id)
        next_ids.append(expand_id)
        if len(next_ids) >= limit:
            break
    return next_ids


def seed_symbol_anchors(fused: list[str], roles: dict[str, str], *, limit: int) -> list[str]:
    symbols = [anchor for anchor in fused if roles.get(anchor) == "symbol"]
    if symbols:
        return symbols[:limit]
    return fused[:limit]
