"""Content-addressed embedding reuse via existing Qdrant points."""

from __future__ import annotations

from time import perf_counter

from app.ingestion.assemble import ChunkRecord
from app.ingestion.vector_store import fetch_point_payloads, fetch_point_vectors
from app.storage.identity import point_id, without_nul


def embed_chunks_with_reuse(
    base_url: str,
    collection: str,
    repository_id: str,
    fingerprint: str,
    embed,
    chunks: list[ChunkRecord],
    renew,
    stats: dict | None = None,
) -> dict[str, list[float]]:
    """Return vectors for chunks, reusing Qdrant when point id and payload match."""
    if not chunks:
        return {}
    identifiers: list[str] = []
    by_id: dict[str, ChunkRecord] = {}
    for chunk in chunks:
        identifier = str(point_id(chunk.id, chunk.embedding_input_hash, fingerprint))
        identifiers.append(identifier)
        by_id[identifier] = chunk

    lookup_started = perf_counter()
    payloads = fetch_point_payloads(base_url, collection, identifiers)
    if stats is not None:
        stats["qdrant_payload_lookup_ms"] = stats.get("qdrant_payload_lookup_ms", 0.0) + (perf_counter() - lookup_started) * 1000

    reuse_ids: list[str] = []
    embed_needed: list[ChunkRecord] = []
    for identifier, chunk in by_id.items():
        payload = payloads.get(identifier)
        if payload is None:
            embed_needed.append(chunk)
            continue
        if (
            payload.get("chunk_id") != chunk.id
            or payload.get("span_hash") != chunk.span_hash
            or payload.get("embedding_config_fingerprint") != fingerprint
            or payload.get("embedding_input_hash") != chunk.embedding_input_hash
            or payload.get("repository_id") not in {None, repository_id}
        ):
            embed_needed.append(chunk)
            continue
        reuse_ids.append(identifier)

    vectors: dict[str, list[float]] = {}
    if reuse_ids:
        vector_started = perf_counter()
        reused = fetch_point_vectors(base_url, collection, reuse_ids)
        if stats is not None:
            stats["qdrant_vector_fetch_ms"] = stats.get("qdrant_vector_fetch_ms", 0.0) + (perf_counter() - vector_started) * 1000
        for identifier in reuse_ids:
            chunk = by_id[identifier]
            vector = reused.get(identifier)
            if vector is None:
                embed_needed.append(chunk)
                continue
            vectors[chunk.id] = vector

    if stats is not None:
        stats["embed_cache_hits"] = stats.get("embed_cache_hits", 0) + len(vectors)
        stats["embed_cache_misses"] = stats.get("embed_cache_misses", 0) + len(embed_needed)

    if embed_needed:
        model_started = perf_counter()
        vectors.update(_embed_fresh(embed, embed_needed, renew))
        if stats is not None:
            stats["embed_model_ms"] = stats.get("embed_model_ms", 0.0) + (perf_counter() - model_started) * 1000
    return vectors


def _embed_fresh(embed, chunks: list[ChunkRecord], renew) -> dict[str, list[float]]:
    many = getattr(embed, "many", None)
    vectors: dict[str, list[float]] = {}
    if many is None:
        for index, chunk in enumerate(chunks):
            if index and index % 100 == 0:
                renew()
            vectors[chunk.id] = [float(value) for value in embed(without_nul(chunk.lexical_text))]
        return vectors
    for start in range(0, len(chunks), 32):
        if start:
            renew()
        batch = chunks[start : start + 32]
        encoded = many([without_nul(chunk.lexical_text) for chunk in batch])
        if len(encoded) != len(batch):
            raise RuntimeError("the embedding model returned a different number of vectors")
        for chunk, vector in zip(batch, encoded, strict=True):
            vectors[chunk.id] = [float(value) for value in vector]
    return vectors
