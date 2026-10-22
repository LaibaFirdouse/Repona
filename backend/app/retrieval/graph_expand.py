"""Multi-hop Neo4j expansion from fused retrieval anchors."""

from __future__ import annotations

from app.models.catalog import Symbol

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


def _symbol_roles(db, symbol_ids: list[str]) -> dict[str, str]:
    if not symbol_ids:
        return {}
    rows = db.query(Symbol.id, Symbol.kind).filter(Symbol.id.in_(symbol_ids)).all()
    return {row.id: ("module" if row.kind == "module" else "symbol") for row in rows}


def graph_neighbor_symbol_ids(
    graph_session,
    db,
    generation_id: str,
    seed_symbol_ids: list[str],
    *,
    max_hops: int = 2,
    limit: int = 16,
) -> list[str]:
    """Symbol ids reachable via CALLS/REFERENCES within max_hops of seed anchors (e.g. main)."""
    if not seed_symbol_ids or max_hops < 1:
        return []
    hop_cap = max(1, min(max_hops, 3))
    frontier = list(dict.fromkeys(seed_symbol_ids))[:8]
    visited = set(frontier)
    collected: list[str] = []

    for hop in range(hop_cap):
        if not frontier:
            break
        records = graph_session.run(
            _EDGE_QUERY,
            generation_id=generation_id,
            anchor_ids=list(frontier),
        )
        batch = [dict(record) for record in records]
        if not batch:
            break
        role_ids = [row["anchor_id"] for row in batch] + [row["other_id"] for row in batch]
        roles = _symbol_roles(db, role_ids)
        next_ids = next_symbol_frontier(set(frontier), batch, roles, limit=limit)
        frontier = []
        for symbol_id in next_ids:
            if symbol_id in visited:
                continue
            visited.add(symbol_id)
            collected.append(symbol_id)
            frontier.append(symbol_id)
            if len(collected) >= limit:
                return collected[:limit]
    return collected[:limit]
