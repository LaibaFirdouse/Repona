"""User-visible answer text cleanup (keep JSON evidence_ids separate)."""

from __future__ import annotations

import re

# Parenthetical groups like (E4), (E5, E6), (E5–E8)
_PAREN_EVIDENCE = re.compile(
    r"\(\s*E\d+(?:\s*[,&]\s*E\d+)*(?:\s*[–-]\s*E\d+)?\s*\)",
    re.IGNORECASE,
)
# Standalone evidence tags the model copies into prose. Case-sensitive and at most two digits,
# so identifiers such as E501 (a lint code) or e2e survive.
_STANDALONE_EVIDENCE = re.compile(r"\bE\d{1,2}\b")
# UI chrome sometimes pasted into stored answers
_SOURCES_LINE = re.compile(r"^\s*Sources\s*·\s*\d+\s*$", re.MULTILINE | re.IGNORECASE)
# Fenced code blocks (closed, or running to the end) and inline code spans.
_CODE_SEGMENT = re.compile(r"```.*?(?:```|\Z)|`[^`\n]+`", re.DOTALL)

PROSE_STYLE_SUFFIX = (
    "\nWrite readable prose: one short paragraph per major idea, in the order a reader needs them. "
    "Name files and functions where they help the reader find the code; do not list every import or export. "
    "Quote code only when a few lines make the point clearer than prose."
)


def sanitize_answer_prose(text: str) -> str:
    """Remove internal evidence labels from prose. Code blocks and inline code are left untouched.

    Only labels are removed. Rewriting wording (deleting "excerpt", dropping clauses about missing
    code) produced broken sentences, and collapsing spaces flattened the indentation of code blocks.
    """
    if not text:
        return text
    parts: list[str] = []
    cursor = 0
    for match in _CODE_SEGMENT.finditer(text):
        parts.append(_clean_prose(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_clean_prose(text[cursor:]))
    return "".join(parts).strip()


def _clean_prose(segment: str) -> str:
    cleaned = _SOURCES_LINE.sub("", segment)
    cleaned = _PAREN_EVIDENCE.sub("", cleaned)
    cleaned = _STANDALONE_EVIDENCE.sub("", cleaned)
    # A removed label can leave a space before punctuation: "early (E2):" -> "early :".
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    # Collapse runs inside a line only; leading indentation (Markdown lists) is kept.
    cleaned = re.sub(r"(?<=\S)[ \t]{2,}", " ", cleaned)
    return cleaned


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
