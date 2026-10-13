"""Drop evidence that does not fit, and keep only claims whose ids are in the packet."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings

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
    """Add higher-priority evidence first. `span_text` returns `(raw_bytes, utf8_piece)`."""
    counter = count_tokens or estimate_tokens
    kept_spans = []
    kept_edges = []
    kept_technologies = []
    raw_bytes = 0
    body = header
    for span in spans:
        span_raw, piece = span_text(span)
        if raw_bytes + span_raw > source_byte_cap:
            break
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
            break
        body += piece
        raw_bytes += span_raw
        kept_spans.append(span)
    for edge in edges:
        piece = edge_text(edge)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens, margin_tokens, counter):
            break
        body += piece
        kept_edges.append(edge)
    for technology in technologies:
        piece = technology_text(technology)
        if not _fits(body + piece, raw_bytes, source_byte_cap, input_byte_cap, context_tokens, output_tokens, margin_tokens, counter):
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
    """Turn kept claims into the sentences a person reads."""
    sentences = []
    for claim in claims:
        text = str(claim.get("text") or "").strip()
        if not text:
            continue
        if text[-1] not in ".?!":
            text += "."
        sentences.append(text)
    return " ".join(sentences) if sentences else NO_CITED_CLAIM


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


def span_citations(spans: list[dict], claims: list[dict]) -> list[dict]:
    """Map kept evidence ids to the span path and lines. Edges are not citations."""
    cited = {item for claim in claims for item in claim["evidence_ids"]}
    return [
        {
            "chunk_id": span["chunk_id"],
            "path": span["relative_path"],
            "start_line": span["start_line"],
            "end_line": span["end_line"],
        }
        for span in spans
        if span["chunk_id"] in cited
    ]


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
