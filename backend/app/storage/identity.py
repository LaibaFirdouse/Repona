"""UUID5 identities, URL normalization, and source-byte helpers.

Entity names are length-prefixed frames so two different component lists
cannot hash to the same UUID5 input.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

DNS_NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

TAG_TEXT = 0x01
TAG_UUID = 0x02
TAG_BYTES = 0x03
TAG_UINT32 = 0x04

INPUT_RECIPE = "parent_qualified_name_and_signature\nspan_utf8"
VECTOR_NORMALIZATION = "none"
DISTANCE = "cosine"


def _uuid5(namespace: UUID, name: bytes) -> UUID:
    digest = hashlib.sha1(namespace.bytes + name).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


NAMESPACE = _uuid5(DNS_NAMESPACE, b"repona.dev/identity/v1")


class IdentityError(ValueError):
    pass


def frame_text(value: str) -> bytes:
    payload = value.encode("utf-8")
    return bytes([TAG_TEXT]) + struct.pack(">I", len(payload)) + payload


def frame_uuid(value: UUID | str | None) -> bytes:
    if value is None or value == "":
        return bytes([TAG_UUID]) + struct.pack(">I", 0)
    parsed = value if isinstance(value, UUID) else UUID(str(value))
    return bytes([TAG_UUID]) + struct.pack(">I", 16) + parsed.bytes


def frame_bytes(payload: bytes) -> bytes:
    return bytes([TAG_BYTES]) + struct.pack(">I", len(payload)) + payload


def frame_uint32(value: int) -> bytes:
    if value < 0 or value > 0xFFFFFFFF:
        raise IdentityError(f"uint32 out of range: {value}")
    return bytes([TAG_UINT32]) + struct.pack(">I", 4) + struct.pack(">I", value)


def entity_id(entity_type: str, *frames: bytes) -> UUID:
    return _uuid5(NAMESPACE, frame_text(entity_type) + b"".join(frames))


def parse_sha256(value: bytes | str) -> bytes:
    if isinstance(value, str):
        raw = bytes.fromhex(value)
    else:
        raw = value
    if len(raw) != 32:
        raise IdentityError("SHA-256 must be 32 bytes")
    return raw


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def content_hash(payload: bytes) -> str:
    return sha256_hex(payload)


def repository_id(normalized_url: str) -> UUID:
    return entity_id("repository", frame_text(normalized_url))


def source_revision_id(repository: UUID | str, commit_sha: str) -> UUID:
    return entity_id(
        "source_revision",
        frame_uuid(repository),
        frame_text(commit_sha),
    )


def logical_file_key(repository: UUID | str, relative_path: str) -> UUID:
    return entity_id(
        "logical_file",
        frame_uuid(repository),
        frame_text(relative_path),
    )


def directory_id(repository: UUID | str, relative_path: str) -> UUID:
    return entity_id(
        "directory",
        frame_uuid(repository),
        frame_text(relative_path),
    )


def file_id(repository: UUID | str, relative_path: str, content: bytes | str) -> UUID:
    return entity_id(
        "file",
        frame_uuid(repository),
        frame_text(relative_path),
        frame_bytes(parse_sha256(content)),
    )


def symbol_id(
    file: UUID | str,
    parser_version: str,
    kind: str,
    qualified_name: str,
    signature: str,
    ordinal: int,
) -> UUID:
    return entity_id(
        "symbol",
        frame_uuid(file),
        frame_text(parser_version),
        frame_text(kind),
        frame_text(qualified_name),
        frame_text(signature),
        frame_uint32(ordinal),
    )


def chunk_id(
    anchor: str,
    symbol: UUID | str | None,
    file: UUID | str,
    parser_version: str,
    chunking_version: str,
    chunk_kind: str,
    ordinal: int,
    span: bytes | str,
) -> UUID:
    if anchor not in {"symbol", "file"}:
        raise IdentityError(f"unknown chunk anchor: {anchor}")
    return entity_id(
        "chunk",
        frame_text(anchor),
        frame_uuid(symbol),
        frame_uuid(file),
        frame_text(parser_version),
        frame_text(chunking_version),
        frame_text(chunk_kind),
        frame_uint32(ordinal),
        frame_bytes(parse_sha256(span)),
    )


def index_generation_id(
    source_revision: UUID | str,
    parser_version: str,
    chunking_version: str,
    lexical_index_version: str,
    inclusion_rules_version: str,
    embedding_config_fingerprint: bytes | str,
    build_attempt: int,
) -> UUID:
    return entity_id(
        "index_generation",
        frame_uuid(source_revision),
        frame_text(parser_version),
        frame_text(chunking_version),
        frame_text(lexical_index_version),
        frame_text(inclusion_rules_version),
        frame_bytes(parse_sha256(embedding_config_fingerprint)),
        frame_uint32(build_attempt),
    )


def point_id(
    chunk: UUID | str,
    embedding_input: bytes | str,
    embedding_config_fingerprint: bytes | str,
) -> UUID:
    return entity_id(
        "point",
        frame_uuid(chunk),
        frame_bytes(parse_sha256(embedding_input)),
        frame_bytes(parse_sha256(embedding_config_fingerprint)),
    )


def identity_key(
    repository: UUID | str,
    relative_path: str,
    kind: str,
    qualified_name: str,
    signature: str,
) -> UUID:
    return entity_id(
        "identity_key",
        frame_uuid(repository),
        frame_text(relative_path),
        frame_text(kind),
        frame_text(qualified_name),
        frame_text(signature),
    )


def edge_id(
    relationship_type: str,
    index_generation: UUID | str,
    source: UUID | str,
    target: UUID | str,
    mentioned_name: str = "",
    resolution: str = "",
) -> UUID:
    return entity_id(
        relationship_type,
        frame_uuid(index_generation),
        frame_uuid(source),
        frame_uuid(target),
        frame_text(mentioned_name),
        frame_text(resolution),
    )


def normalize_repository_url(
    url: str,
    *,
    allow_loopback_http: bool = False,
) -> str:
    raw = url.strip()
    if raw.startswith("git@") and ":" in raw:
        host, path = raw[4:].split(":", 1)
        raw = f"https://{host}/{path}"
    parts = urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    # Loopback HTTP is allowed for local fixture/integration indexing only.
    # Non-loopback repositories must remain HTTPS.
    hostname = (parts.hostname or "").lower()
    if scheme == "http" and hostname in {"127.0.0.1", "localhost", "::1"}:
        if not allow_loopback_http:
            raise IdentityError("loopback HTTP is disabled")
    elif scheme != "https":
        raise IdentityError(f"unsupported repository URL scheme: {parts.scheme or '(none)'}")
    if parts.username or parts.password:
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
    else:
        netloc = parts.netloc
    host = netloc.lower()
    path = parts.path
    if path.endswith("/"):
        path = path[:-1]
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if not host or path in {"", "/"}:
        raise IdentityError(f"repository URL has no host or path: {url}")
    out_scheme = "http" if scheme == "http" else "https"
    return urlunsplit((out_scheme, host, path, "", ""))


def canonical_embedding_config(
    model: str,
    model_revision: str,
    dimensions: int,
    distance: str = DISTANCE,
    input_recipe: str = INPUT_RECIPE,
    vector_normalization: str = VECTOR_NORMALIZATION,
) -> bytes:
    payload = {
        "dimensions": dimensions,
        "distance": distance,
        "input_recipe": input_recipe,
        "model": model,
        "model_revision": model_revision,
        "vector_normalization": vector_normalization,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def embedding_config_fingerprint(
    model: str,
    model_revision: str,
    dimensions: int,
    distance: str = DISTANCE,
    input_recipe: str = INPUT_RECIPE,
    vector_normalization: str = VECTOR_NORMALIZATION,
) -> str:
    raw = canonical_embedding_config(
        model,
        model_revision,
        dimensions,
        distance,
        input_recipe,
        vector_normalization,
    )
    return sha256_hex(raw)


def collection_name(model: str, dimensions: int, distance: str = DISTANCE) -> str:
    model_key = hashlib.sha256(model.encode("utf-8")).hexdigest()[:16]
    return f"code_chunks__{model_key}__{dimensions}__{distance}"


def context_prefix(parent_qualified_name: str | None, signature: str | None) -> str:
    if not parent_qualified_name:
        return ""
    if signature:
        return f"{parent_qualified_name}({signature})"
    return parent_qualified_name


def without_nul(value: str) -> str:
    """Drop NUL bytes. PostgreSQL text cannot store them; span bytes and chunk ids stay as they are."""
    return value.replace("\x00", "")


def lexical_input(prefix: str, span: bytes) -> bytes:
    decoded = without_nul(span.decode("utf-8", errors="replace"))
    return without_nul(prefix).encode("utf-8") + b"\n" + decoded.encode("utf-8")


def embedding_input_hash(prefix: str, span: bytes) -> str:
    return sha256_hex(lexical_input(prefix, span))


@dataclass(frozen=True)
class LineSpan:
    start_byte: int
    end_byte: int


def line_spans(payload: bytes) -> list[LineSpan]:
    if not payload:
        return []
    spans: list[LineSpan] = []
    start = 0
    for index, byte in enumerate(payload):
        if byte == 0x0A:
            spans.append(LineSpan(start, index + 1))
            start = index + 1
    if start < len(payload):
        spans.append(LineSpan(start, len(payload)))
    return spans


def line_count(payload: bytes) -> int:
    return len(line_spans(payload))


def line_labels(
    payload: bytes,
    start_byte: int,
    end_byte: int,
    spans: list[LineSpan] | None = None,
) -> tuple[int | None, int | None]:
    if end_byte < start_byte:
        raise IdentityError("end_byte is before start_byte")
    if start_byte == end_byte:
        return None, None
    if spans is None:
        spans = line_spans(payload)
    low = 0
    high = len(spans)
    while low < high:
        mid = (low + high) // 2
        if spans[mid].end_byte <= start_byte:
            low = mid + 1
        else:
            high = mid
    if low >= len(spans) or spans[low].start_byte >= end_byte:
        return None, None
    first = low + 1
    last = first
    index = low + 1
    while index < len(spans) and spans[index].start_byte < end_byte:
        last = index + 1
        index += 1
    return first, last
