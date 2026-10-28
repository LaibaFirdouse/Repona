"""Drop evidence that does not fit, and keep only claims whose ids are in the packet."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.retrieval.answer_prose import sanitize_answer_prose

NO_CITED_CLAIM = "The repository evidence did not produce a cited claim."
INSUFFICIENT_EVIDENCE = "The retrieved evidence is not sufficient to explain this."
SOURCE_BYTE_CAP = 24_000
INPUT_BYTE_CAP = 32_000


@dataclass(frozen=True)
class Packed:
    spans: list
    edges: list
    technologies: list
    raw_source_bytes: int
    excluded: tuple = ()


def _span_identity(span) -> tuple:
    if isinstance(span, dict):
        chunk_id = span.get("chunk_id") if span.get("chunk_id") is not None else span.get("id")
        return (
            chunk_id,
            span.get("relative_path"),
            span.get("start_line"),
            span.get("end_line"),
            span.get("source_start_byte"),
            span.get("source_end_byte"),
        )
    return (getattr(span, "chunk_id", None), None, None, None, None, None)


def _spans_overlap(left, right) -> bool:
    """True when two loaded spans cover the same or overlapping source range."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if left.get("chunk_id") and left.get("chunk_id") == right.get("chunk_id"):
        return True
    if left.get("relative_path") != right.get("relative_path"):
        return False
    ls, le = left.get("source_start_byte"), left.get("source_end_byte")
    rs, re = right.get("source_start_byte"), right.get("source_end_byte")
    if ls is None or le is None or rs is None or re is None:
        l_start, l_end = left.get("start_line"), left.get("end_line")
        r_start, r_end = right.get("start_line"), right.get("end_line")
        if None in (l_start, l_end, r_start, r_end):
            return False
        return not (l_end < r_start or r_end < l_start)
    return not (le <= rs or re <= ls)


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
    margin_tokens: int = 0,
    count_tokens=None,
    span_text,
    edge_text,
    technology_text,
) -> Packed:
    """Add higher-priority evidence first. `span_text` returns `(raw_bytes, utf8_piece)`.

    A span that does not fit is skipped so later, smaller candidates can still enter.
    Exclusion reasons are recorded on Packed.excluded (chunk id + reason).
    """
    counter = count_tokens or estimate_tokens
    kept_spans = []
    kept_edges = []
    kept_technologies = []
    excluded: list[dict] = []
    raw_bytes = 0
    body = header
    seen_ids: set = set()
    for span in spans:
        identity = _span_identity(span)
        chunk_id = identity[0]
        if chunk_id is not None and chunk_id in seen_ids:
            excluded.append({"chunk_id": chunk_id, "reason": "duplicate_chunk_id"})
            continue
        if any(_spans_overlap(span, kept) for kept in kept_spans):
            excluded.append({"chunk_id": chunk_id, "reason": "overlapping_span"})
            continue
        span_raw, piece = span_text(span)
        if raw_bytes + span_raw > source_byte_cap:
            excluded.append({"chunk_id": chunk_id, "reason": "source_byte_cap", "raw_bytes": span_raw})
            continue
        if not _fits(
            body + piece,
            raw_bytes + span_raw,
            source_byte_cap,
            input_byte_cap,
            context_tokens,
            output_tokens,
            margin_tokens,
            counter,
        ):
            excluded.append({"chunk_id": chunk_id, "reason": "context_budget", "raw_bytes": span_raw})
            continue
        body += piece
        raw_bytes += span_raw
        kept_spans.append(span)
        if chunk_id is not None:
            seen_ids.add(chunk_id)
    for edge in edges:
        piece = edge_text(edge)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens, margin_tokens, counter):
            excluded.append({"edge_id": getattr(edge, "edge_id", None), "reason": "context_budget"})
            continue
        body += piece
        kept_edges.append(edge)
    for technology in technologies:
        piece = technology_text(technology)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens, margin_tokens, counter):
            excluded.append({"technology_id": technology.get("id") if isinstance(technology, dict) else None, "reason": "context_budget"})
            continue
        body += piece
        kept_technologies.append(technology)
    return Packed(kept_spans, kept_edges, kept_technologies, raw_bytes, tuple(excluded))


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


_qwen_tokenizer = None


def render_qwen_chat(system: str, user: str) -> str:
    """Render the Qwen chat prompt Ollama evaluates. This matches qwen2.5:1.5b."""
    return (
        "<|im_start|>system\n"
        + system
        + "<|im_end|>\n<|im_start|>user\n"
        + user
        + "<|im_end|>\n<|im_start|>assistant\n"
    )


def qwen_prompt_tokens(system: str, user: str) -> int:
    """Count system, user, and chat-template tokens with the Qwen tokenizer."""
    return len(_qwen().encode(render_qwen_chat(system, user), add_special_tokens=False))


def ollama_prompt_fits(system: str, user: str, output_tokens: int) -> bool:
    """True when the rendered prompt, output budget, and margin fit OLLAMA_NUM_CTX."""
    return fits_model_context(
        qwen_prompt_tokens(system, user),
        output_tokens,
        settings.context_margin_tokens,
        settings.ollama_num_ctx,
    )


def fits_model_context(prompt_tokens: int, output_tokens: int, margin_tokens: int, context_tokens: int) -> bool:
    return prompt_tokens + output_tokens + margin_tokens <= context_tokens


def _qwen():
    global _qwen_tokenizer
    if _qwen_tokenizer is None:
        from transformers import AutoTokenizer

        _qwen_tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-1.5B-Instruct")
    return _qwen_tokenizer


def render_answer(claims: list[dict]) -> str:
    """Turn kept claims into the prose a person reads."""
    paragraphs = []
    for claim in claims:
        text = sanitize_answer_prose(str(claim.get("text") or "").strip())
        if not text:
            continue
        paragraphs.append(text)
    return "\n\n".join(paragraphs) if paragraphs else NO_CITED_CLAIM


def implementation_before_tests(spans: list[dict], question: str = "") -> list[dict]:
    """Keep the same spans. A name written in the question comes first, then other implementation files, then tests."""

    def is_test(span: dict) -> bool:
        path = str(span.get("relative_path") or "").replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        return "/tests/" in f"/{path}" or name.startswith("test_")

    def group(span: dict) -> int:
        qualified = str(span.get("qualified_name") or "")
        if qualified and qualified in question:
            return 0
        if is_test(span):
            return 2
        return 1

    return sorted(spans, key=group)


def assign_evidence_aliases(source_ids: list[str]) -> dict[str, str]:
    """Map E1, E2, ... to source ids for one request. A repeated source id keeps its first alias."""
    aliases: dict[str, str] = {}
    seen: set[str] = set()
    for source_id in source_ids:
        if source_id in seen:
            continue
        seen.add(source_id)
        aliases[f"E{len(aliases) + 1}"] = source_id
    return aliases


def resolve_evidence_aliases(claims: list[dict], aliases: dict[str, str]) -> list[dict]:
    """Replace exact alias keys with source ids. Prefixes and unknown tokens stay unchanged."""
    resolved = []
    for claim in claims:
        evidence_ids = []
        for item in claim.get("evidence_ids") or []:
            token = str(item)
            evidence_ids.append(aliases[token] if token in aliases else token)
        resolved.append({"kind": claim.get("kind"), "text": claim.get("text"), "evidence_ids": evidence_ids})
    return resolved


def dedupe_citations_by_path(citations: list[dict]) -> list[dict]:
    """One chip per file in the UI; merge line ranges when multiple chunks cite the same path."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in citations:
        path = str(item.get("path") or "")
        if path not in merged:
            merged[path] = dict(item)
            order.append(path)
            continue
        row = merged[path]
        start = item.get("start_line")
        end = item.get("end_line")
        if start is not None and end is not None:
            prev_s, prev_e = row.get("start_line"), row.get("end_line")
            if prev_s is not None and prev_e is not None:
                row["start_line"] = min(prev_s, start)
                row["end_line"] = max(prev_e, end)
            else:
                row["start_line"] = start
                row["end_line"] = end
    return [merged[path] for path in order]


def span_citations(spans: list[dict], claims: list[dict]) -> list[dict]:
    """Map kept evidence ids to the span path and lines. Edges are not citations."""
    cited = {item for claim in claims for item in claim["evidence_ids"]}
    raw = [
        {
            "chunk_id": span["chunk_id"],
            "path": span["relative_path"],
            "start_line": span["start_line"],
            "end_line": span["end_line"],
        }
        for span in spans
        if span["chunk_id"] in cited
    ]
    return dedupe_citations_by_path(raw)


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


def _weak_evidence_kinds() -> frozenset[str]:
    return frozenset({"import_block", "file_overview", "non_code"})


def is_weak_implementation_span(span: dict) -> bool:
    """Import/overview/readme metadata is weak support for how-it-works claims."""
    kind = str(span.get("chunk_kind") or "")
    path = str(span.get("relative_path") or "").replace("\\", "/").lower()
    if kind in _weak_evidence_kinds():
        return True
    name = path.rsplit("/", 1)[-1]
    if name.startswith("readme") or name in {"package.json", "pyproject.toml"}:
        return True
    return False


def review_implementation_support(
    claims: list[dict],
    spans: list[dict],
    *,
    require_implementation: bool,
) -> tuple[list[dict], bool]:
    """When implementation evidence is in the pack, reject claims citing only weak spans.

    Citation id validity is separate from semantic support: a README id can be a
    valid packet id and still fail as sole support for an implementation claim.
    """
    if not require_implementation or not claims:
        return claims, True
    by_id = {str(span.get("chunk_id")): span for span in spans if span.get("chunk_id")}
    substantive_ids = {
        chunk_id for chunk_id, span in by_id.items() if not is_weak_implementation_span(span)
    }
    if not substantive_ids:
        # No implementation evidence reached the prompt—caller should abstain.
        return [], False
    kept: list[dict] = []
    for claim in claims:
        ids = [str(item) for item in claim.get("evidence_ids") or []]
        strong = [item for item in ids if item in substantive_ids]
        if not strong:
            continue
        kept.append({**claim, "evidence_ids": strong})
    return kept, bool(kept)


def _fits(
    text: str,
    raw_source_bytes: int,
    source_byte_cap: int,
    input_byte_cap: int,
    context_tokens: int,
    output_tokens: int,
    margin_tokens: int,
    count_tokens,
) -> bool:
    if raw_source_bytes > source_byte_cap:
        return False
    encoded = text.encode("utf-8")
    if len(encoded) > input_byte_cap:
        return False
    return fits_model_context(count_tokens(text), output_tokens, margin_tokens, context_tokens)
