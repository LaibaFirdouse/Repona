"""User-visible answer text cleanup (keep JSON evidence_ids separate)."""

from __future__ import annotations

import re

# Parenthetical groups like (E4), (E5, E6), (E5–E8)
_PAREN_EVIDENCE = re.compile(
    r"\(\s*E\d+(?:\s*[,&]\s*E\d+)*(?:\s*[–-]\s*E\d+)?\s*\)",
    re.IGNORECASE,
)
# Standalone evidence tags the model copies into prose
_STANDALONE_EVIDENCE = re.compile(r"\bE\d+\b", re.IGNORECASE)
# UI chrome sometimes pasted into stored answers
_SOURCES_LINE = re.compile(r"^\s*Sources\s*·\s*\d+\s*$", re.MULTILINE | re.IGNORECASE)
# Retrieval-audit phrasing the model should not show users
_META_SNIPPETS = (
    re.compile(r"\b(?:the )?(?:provided |retrieved |supplied )?(?:source )?excerpts?\b", re.I),
    re.compile(r"\bthis packet\b", re.I),
    re.compile(r"\bprior (?:answer|summary|turn)\b", re.I),
    re.compile(r"\bfrom the README alone\b", re.I),
    re.compile(r"\bnot (?:present|included) in (?:these |the )?(?:excerpts|spans|fragments)\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\b(?:do not|does not) appear in (?:these |the )?(?:supplied )?(?:source )?(?:spans|fragments)\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\bwhich are not (?:included|shown) in\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\b(?:cannot|can't) be (?:faithfully )?stated from\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\bgiven only truncated\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\bwould need to be available\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\brepository material provided\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\bcurrent retrieval\b[^.!?]*[.!?]?", re.I),
    re.compile(r"\bhow (?:evidence|retrieval) (?:was|is) chosen\b[^.!?]*[.!?]?", re.I),
)

_RETRIEVAL_AUDIT_PHRASE = re.compile(
    r"\b("
    r"cannot be stated|can't be stated|not present in the retrieved|"
    r"retrieved lines|retrieved spans|retrieved material|"
    r"truncated export|incomplete default export|"
    r"faithful .{0,40}walkthrough|"
    r"would need to be available|repository material provided"
    r")\b",
    re.I,
)

PROSE_STYLE_SUFFIX = (
    "\nWrite readable prose: one or two short paragraphs per major idea. "
    "Mention specific file paths only where they anchor a non-obvious claim (roughly two to four paths per answer, not every sentence). "
    "Do not enumerate every import, export, or function name in a row; describe behavior and structure in plain language. "
    "evidence_ids still cite all supporting excerpts in JSON."
)


def prose_is_retrieval_audit(text: str) -> bool:
    """True when user-visible text reads like a retrieval gap analysis, not an answer."""
    if not text or not text.strip():
        return False
    lowered = text.lower()
    audit_hits = len(_RETRIEVAL_AUDIT_PHRASE.findall(text))
    excerpt_hits = lowered.count("excerpt") + lowered.count("retrieved")
    if audit_hits >= 2:
        return True
    if audit_hits >= 1 and excerpt_hits >= 2:
        return True
    if audit_hits >= 1 and len(text) > 400 and "cannot" in lowered:
        return True
    return False


def filter_audit_claims(claims: list[dict]) -> list[dict]:
    """Drop or trim claims that only describe missing retrieval; sanitize the rest."""
    kept: list[dict] = []
    for claim in claims:
        raw = str(claim.get("text") or "").strip()
        if not raw:
            continue
        if prose_is_retrieval_audit(raw):
            continue
        cleaned = sanitize_answer_prose(raw)
        if not cleaned or prose_is_retrieval_audit(cleaned):
            continue
        kept.append({**claim, "text": cleaned})
    return kept


def sanitize_answer_prose(text: str) -> str:
    """Remove internal evidence labels and collapse whitespace after cleanup."""
    if not text:
        return text
    cleaned = _SOURCES_LINE.sub("", text)
    cleaned = _PAREN_EVIDENCE.sub("", cleaned)
    cleaned = _STANDALONE_EVIDENCE.sub("", cleaned)
    for pattern in _META_SNIPPETS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\.", ".", cleaned)
    cleaned = re.sub(r"[ \t]+,", ",", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned.strip()


def meta_reasoning_question(question: str) -> bool:
    """True when the user asks how or why a prior answer was reached."""
    q = question.strip().lower()
    if not q:
        return False
    patterns = (
        "how did you",
        "how do you know",
        "why did you",
        "why do you say",
        "what made you",
        "come to that conclusion",
        "come to the above conclusion",
        "explain your reasoning",
        "explain the reasoning",
        "how did u ",
        "why did u ",
    )
    return any(fragment in q for fragment in patterns)


META_ANSWER_SUFFIX = (
    "\nThe user is asking about your reasoning. Reply in direct engineering prose: name files, "
    "types, and behaviors from the repository excerpts. Do not mention excerpt numbers (E1, E2), "
    "do not say \"the prior answer\" or \"the excerpts\", and do not describe search or retrieval."
)
