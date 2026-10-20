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


def sanitize_answer_prose(text: str) -> str:
    """Remove internal evidence labels and collapse whitespace after cleanup."""
    if not text:
        return text
    cleaned = _SOURCES_LINE.sub("", text)
    cleaned = _PAREN_EVIDENCE.sub("", cleaned)
    cleaned = _STANDALONE_EVIDENCE.sub("", cleaned)
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
