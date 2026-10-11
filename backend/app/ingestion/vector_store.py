"""Create the Qdrant collection for one embedding space and insert missing points."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.ingestion.assemble import ChunkRecord, GenerationDraft
from app.ingestion.versions import CHUNKING_VERSION, PARSER_VERSION
from app.storage.identity import point_id


class VectorStoreError(RuntimeError):
    pass


def ensure_collection(base_url: str, name: str, dimensions: int, distance: str = "Cosine") -> None:
    existing = _request(base_url, f"/collections/{name}", method="GET")
    if existing is not None:
        size = existing["result"]["config"]["params"]["vectors"]["size"]
        stored_distance = existing["result"]["config"]["params"]["vectors"]["distance"]
        if size != dimensions or stored_distance != distance:
            raise VectorStoreError(f"collection {name} has size {size} and distance {stored_distance}")
        return
    _request(
        base_url,
        f"/collections/{name}",
        method="PUT",
        payload={"vectors": {"size": dimensions, "distance": distance}},
        required=True,
    )


def upsert_missing_points(
    base_url: str,
    collection: str,
    repository_id: str,
    draft: GenerationDraft,
    fingerprint: str,
    vectors: dict[str, list[float]],
) -> None:
    points = []
    for chunk in draft.chunks:
        symbol = next((item for item in draft.symbols if item.id == chunk.symbol_id), None)
        file = next(item for item in draft.files if item.id == chunk.file_id)
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
        return
    _request(
        base_url,
        f"/collections/{collection}/points?wait=true",
        method="PUT",
        payload={"points": fresh},
        required=True,
    )


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


def _existing_points(base_url: str, collection: str, identifiers: list[str]) -> dict[str, dict]:
    if not identifiers:
        return {}
    body = _request(
        base_url,
        f"/collections/{collection}/points",
        method="POST",
        payload={"ids": identifiers, "with_payload": True, "with_vector": False},
        required=True,
    )
    found = {}
    for point in body["result"]:
        found[str(point["id"])] = point.get("payload") or {}
    return found


def _request(base_url: str, path: str, method: str, payload: dict | None = None, required: bool = False):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and not required:
            return None
        detail = exc.read().decode("utf-8", errors="replace")
        raise VectorStoreError(detail or str(exc)) from exc
