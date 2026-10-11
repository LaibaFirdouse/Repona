"""Turn one ask result into an evaluation record. This does not judge claim wording."""

from __future__ import annotations

from app.retrieval.packet import NO_CITED_CLAIM


def span_reached(spans: list[dict], qualified_name: str, text_marker: str | None = None) -> bool:
    for span in spans:
        if span.get("qualified_name") != qualified_name:
            continue
        if text_marker is None or text_marker in (span.get("text") or ""):
            return True
    return False


def technology_reached(rows: list[dict], name: str) -> bool:
    return any(row.get("name") == name for row in rows)


def case_record(
    *,
    case_id: str,
    question: str,
    needed: list[dict],
    packet_spans: list[dict],
    packet_technologies: list[dict],
    claims: list[dict],
    citations: list[dict],
    citations_valid: bool,
    message: str,
    retrieval_seconds: float | None,
    model_seconds: float | None,
    action: str,
    error: str | None = None,
) -> dict:
    """Record packet membership, citation checks, the no-claim line, and latency.

    `needed` entries are `{"kind": "span", "qualified_name": ..., "text_marker": ...}`
    or `{"kind": "technology", "name": ...}`. An empty list means the repository
    has no span that could answer the question. Claim wording is copied for a
    person to read. It is not scored.
    """
    details = []
    for item in needed:
        if item["kind"] == "span":
            reached = span_reached(packet_spans, item["qualified_name"], item.get("text_marker"))
        else:
            reached = technology_reached(packet_technologies, item["name"])
        details.append({**item, "reached": reached})
    reached = None if not needed else all(item["reached"] for item in details)
    return {
        "id": case_id,
        "question": question,
        "action": action,
        "needed_reached": reached,
        "needed": details,
        "citation_ids_verify": None if error else citations_valid,
        "citations": citations,
        "no_claim_line": message == NO_CITED_CLAIM,
        "claims": claims,
        "message": message,
        "retrieval_seconds": retrieval_seconds,
        "model_seconds": model_seconds,
        "error": error,
    }
