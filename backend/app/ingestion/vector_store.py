"""Create the Qdrant collection for one embedding space and insert missing points."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.core.config import settings
from app.ingestion.assemble import ChunkRecord, GenerationDraft
from app.ingestion.versions import CHUNKING_VERSION, PARSER_VERSION
from app.storage.identity import point_id


class VectorStoreError(RuntimeError):
    pass


_POINT_BATCH = 64
_LOOKUP_BATCH = 256
_PAYLOAD_INDEX_FIELDS = (
    "repository_id",
    "parser_version",
    "chunking_version",
    "embedding_config_fingerprint",
    "file_id",
)


def collection_config(base_url: str, name: str) -> tuple[int, str]:
    existing = _request(base_url, f"/collections/{name}", method="GET", required=True)
    vectors = existing["result"]["config"]["params"]["vectors"]
    return int(vectors["size"]), str(vectors["distance"])


def ensure_collection(base_url: str, name: str, dimensions: int, distance: str = "Cosine") -> None:
    existing = _request(base_url, f"/collections/{name}", method="GET")
    if existing is not None:
        size = existing["result"]["config"]["params"]["vectors"]["size"]
        stored_distance = existing["result"]["config"]["params"]["vectors"]["distance"]
        if size != dimensions or stored_distance != distance:
            raise VectorStoreError(f"collection {name} has size {size} and distance {stored_distance}")
    else:
        _request(
            base_url,
            f"/collections/{name}",
            method="PUT",
            payload={"vectors": {"size": dimensions, "distance": distance}},
            required=True,
        )
    _ensure_payload_indexes(base_url, name)


def _ensure_payload_indexes(base_url: str, collection: str) -> None:
    """Qdrant Cloud requires keyword indexes before payload filters in search."""
    for field_name in _PAYLOAD_INDEX_FIELDS:
        try:
            _request(
                base_url,
                f"/collections/{collection}/index",
                method="PUT",
                payload={"field_name": field_name, "field_schema": "keyword"},
                required=True,
            )
        except VectorStoreError as error:
            message = str(error).lower()
            if "already exists" in message or "already indexed" in message:
                continue
            raise


def upsert_missing_points(
    base_url: str,
    collection: str,
    repository_id: str,
    draft: GenerationDraft,
    fingerprint: str,
    vectors: dict[str, list[float]],
    chunks=None,
) -> int:
    symbols = {item.id: item for item in draft.symbols}
    files = {item.id: item for item in draft.files}
    chosen = draft.chunks if chunks is None else chunks
    points = []
    for chunk in chosen:
        symbol = symbols.get(chunk.symbol_id)
        file = files[chunk.file_id]
        identifier = str(point_id(chunk.id, chunk.embedding_input_hash, fingerprint))
        payload = _payload(chunk, file, symbol, repository_id, fingerprint)
        points.append((identifier, chunk, payload))
    existing = _existing_points(base_url, collection, [identifier for identifier, _, _ in points])
    fresh = []
    for identifier, chunk, payload in points:
        current = existing.get(identifier)
        if current is None:
            vector = vectors.get(chunk.id)
            if vector is None:
                raise VectorStoreError(f"missing embedding for chunk {chunk.id}")
            fresh.append({"id": identifier, "vector": vector, "payload": payload})
            continue
        if current.get("chunk_id") != chunk.id or current.get("span_hash") != chunk.span_hash:
            raise VectorStoreError(f"point {identifier} does not match chunk {chunk.id}")
    if not fresh:
        return 0
    for start in range(0, len(fresh), _POINT_BATCH):
        _request(
            base_url,
            f"/collections/{collection}/points?wait=true",
            method="PUT",
            payload={"points": fresh[start : start + _POINT_BATCH]},
            required=True,
            timeout=120,
        )
    return len(fresh)


def _payload(chunk: ChunkRecord, file, symbol, repository_id: str, fingerprint: str) -> dict:
    return {
        "chunk_id": chunk.id,
        "repository_id": repository_id,
        "file_id": chunk.file_id,
        "symbol_id": chunk.symbol_id,
        "logical_file_key": file.logical_file_key,
        "relative_path": file.relative_path,
        "language": file.language,
        "symbol_kind": symbol.kind if symbol else None,
        "qualified_name": symbol.qualified_name if symbol else None,
        "chunk_kind": chunk.kind,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "parser_version": PARSER_VERSION,
        "chunking_version": CHUNKING_VERSION,
        "embedding_config_fingerprint": fingerprint,
        "span_hash": chunk.span_hash,
        "embedding_input_hash": chunk.embedding_input_hash,
    }


def delete_points_for_repository(base_url: str, collection: str, repository_id: str) -> None:
    _request(
        base_url,
        f"/collections/{collection}/points/delete?wait=true",
        method="POST",
        payload={
            "filter": {
                "must": [{"key": "repository_id", "match": {"value": repository_id}}],
            }
        },
        required=True,
    )


def search_points(base_url: str, collection: str, vector: list[float], query_filter: dict, limit: int, offset: int) -> list[dict]:
    body = _request(
        base_url,
        f"/collections/{collection}/points/search",
        method="POST",
        payload={
            "vector": vector,
            "filter": query_filter,
            "limit": limit,
            "offset": offset,
            "with_payload": True,
            "with_vector": False,
        },
        required=True,
    )
    return list(body.get("result") or [])


def fetch_point_payloads(base_url: str, collection: str, identifiers: list[str]) -> dict[str, dict]:
    return _existing_points(base_url, collection, identifiers)


def fetch_point_vectors(base_url: str, collection: str, identifiers: list[str]) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for start in range(0, len(identifiers), _LOOKUP_BATCH):
        batch = identifiers[start : start + _LOOKUP_BATCH]
        body = _request(
            base_url,
            f"/collections/{collection}/points",
            method="POST",
            payload={"ids": batch, "with_payload": False, "with_vector": True},
            required=True,
        )
        for point in body["result"]:
            vector = point.get("vector")
            if vector is None:
                continue
            found[str(point["id"])] = [float(value) for value in vector]
    return found


def _existing_points(base_url: str, collection: str, identifiers: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(identifiers), _LOOKUP_BATCH):
        batch = identifiers[start : start + _LOOKUP_BATCH]
        body = _request(
            base_url,
            f"/collections/{collection}/points",
            method="POST",
            payload={"ids": batch, "with_payload": True, "with_vector": False},
            required=True,
        )
        for point in body["result"]:
            found[str(point["id"])] = point.get("payload") or {}
    return found


def _request_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = settings.qdrant_api_key.strip()
    if key:
        headers["api-key"] = key
    return headers


def _request(base_url: str, path: str, method: str, payload: dict | None = None, required: bool = False, timeout: int = 30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=_request_headers(),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and not required:
            return None
        detail = exc.read().decode("utf-8", errors="replace")
        raise VectorStoreError(detail or str(exc)) from exc
