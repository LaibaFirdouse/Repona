"""Drop evidence that does not fit, and keep only claims whose ids are in the packet."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings

NO_CITED_CLAIM = "The repository evidence did not produce a cited claim."
SOURCE_BYTE_CAP = 24_000
INPUT_BYTE_CAP = 32_000


@dataclass(frozen=True)
class Packed:
    spans: list
    edges: list
    technologies: list
    raw_source_bytes: int


def pack_evidence(
    header: str,
    spans: list,
    edges: list,
    technologies: list,
    *,
    source_byte_cap: int = SOURCE_BYTE_CAP,
    input_byte_cap: int = INPUT_BYTE_CAP,
    context_tokens: int,
    output_tokens: int,
    span_text,
    edge_text,
    technology_text,
) -> Packed:
    """Add higher-priority evidence first. `span_text` returns `(raw_bytes, utf8_piece)`."""
    kept_spans = []
    kept_edges = []
    kept_technologies = []
    raw_bytes = 0
    body = header
    for span in spans:
        span_raw, piece = span_text(span)
        if raw_bytes + span_raw > source_byte_cap:
            break
        if not _fits(body + piece, raw_bytes + span_raw, source_byte_cap, input_byte_cap, context_tokens, output_tokens):
            break
        body += piece
        raw_bytes += span_raw
        kept_spans.append(span)
    for edge in edges:
        piece = edge_text(edge)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens):
            break
        body += piece
        kept_edges.append(edge)
    for technology in technologies:
        piece = technology_text(technology)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens):
            break
        body += piece
        kept_technologies.append(technology)
    return Packed(kept_spans, kept_edges, kept_technologies, raw_bytes)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if settings.llm_provider == "openai":
        try:
            import tiktoken

            return len(tiktoken.encoding_for_model(settings.openai_model).encode(text))
        except Exception:
            pass
    return max(1, len(text.encode("utf-8")) // 4)


def review_claims(claims: list[dict], valid_ids: set[str]) -> tuple[list[dict], bool]:
    """Keep a claim that still has one packet id. That id is not semantic support."""
    saw_invalid = False
    kept: list[dict] = []
    for claim in claims:
        kind = claim.get("kind")
        if kind not in {"observed", "inference", "suggestion"}:
            continue
        raw_ids = [str(item) for item in claim.get("evidence_ids") or []]
        if any(item not in valid_ids for item in raw_ids):
            saw_invalid = True
        good = [item for item in raw_ids if item in valid_ids]
        if not good:
            continue
        kept.append({"kind": kind, "text": str(claim.get("text") or ""), "evidence_ids": good})
    citations_valid = bool(kept) and not saw_invalid
    return kept, citations_valid


def _fits(text: str, raw_source_bytes: int, source_byte_cap: int, input_byte_cap: int, context_tokens: int, output_tokens: int) -> bool:
    if raw_source_bytes > source_byte_cap:
        return False
    encoded = text.encode("utf-8")
    if len(encoded) > input_byte_cap:
        return False
    return estimate_tokens(text) + output_tokens <= context_tokens
