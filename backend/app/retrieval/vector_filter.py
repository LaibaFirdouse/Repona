"""Qdrant filters for generation-scoped vector search."""

from __future__ import annotations

from app.models.catalog import IndexGeneration


def vector_query_filter(generation: IndexGeneration, file_ids: list[str], max_file_ids: int) -> dict:
    """Scope search to this generation. Omit huge file_id lists; membership is checked in Postgres."""
    must = [
        {"key": "repository_id", "match": {"value": generation.repository_id}},
        {"key": "parser_version", "match": {"value": generation.parser_version}},
        {"key": "chunking_version", "match": {"value": generation.chunking_version}},
        {"key": "embedding_config_fingerprint", "match": {"value": generation.embedding_config_fingerprint}},
    ]
    if file_ids and len(file_ids) <= max_file_ids:
        must.append({"key": "file_id", "match": {"any": file_ids}})
    return {"must": must}
