"""Vector lane helpers: when to search and how to scope lazy generations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ingestion.lazy_vectors import count_vector_ready
from app.models.catalog import IndexGeneration
from app.retrieval.fusion import LaneHit
from app.retrieval.query_router import RetrievalRequest


def should_skip_vector_lane(db: Session, generation_id: str) -> bool:
    """Skip Qdrant when the catalog has no ready vectors (lazy embed must run first)."""
    return count_vector_ready(db, generation_id) == 0


def should_retry_vector_lane(
    request: RetrievalRequest,
    vector_hits: list[LaneHit],
    *,
    lazy_embed_on_query: bool,
    snapshot_available: bool,
) -> bool:
    """Warm/embed again from post-search hits when the vector lane returned nothing."""
    if "vector" not in request.lanes or vector_hits:
        return False
    if not lazy_embed_on_query or not snapshot_available:
        return False
    return True
