"""Prefer implementation sources over README when the user asks for code."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.catalog import IndexGeneration
from app.retrieval.conversation_context import QaTurn
from app.retrieval.chunk_selection import is_meta_config_path, wants_code_trace

_CODE_FIRST = re.compile(
    r"\b("
    r"from (?:the )?code|from source|source code|implementation|implemented|"
    r"in include/|under include/|\.hpp|\.cpp|\.c\b|not (?:the )?readme|"
    r"rather than (?:the )?readme|instead of (?:the )?readme|"
    r"tell (?:me )?(?:that |this )?from code|can you tell .+ from code|"
    r"how (?:is|are|does|do) .+ (?:work|implemented)|explain how .+ (?:is )?implement"
    r")\b",
    re.I,
)

def code_first_question(question: str) -> bool:
    return bool(_CODE_FIRST.search(question.strip()))


def wants_implementation_evidence(question: str, history: list[QaTurn]) -> bool:
    if code_first_question(question):
        return True
    if wants_code_trace(question, history):
        return True
    return any(code_first_question(turn.question) for turn in history[-4:])


def is_readme_path(relative_path: str) -> bool:
    return is_meta_config_path(relative_path) and "readme" in relative_path.lower()


def deprioritize_readme_spans(spans: list) -> list:
    front = [span for span in spans if not is_readme_path(_span_path(span))]
    back = [span for span in spans if is_readme_path(_span_path(span))]
    return front + back


def filter_readme_when_code_first(loaded: list) -> list:
    """Drop README spans when enough non-README evidence is already present.

    Does not globally ban documentation—only removes it when implementation
    excerpts can answer an implementation / code-first question on their own.
    """
    impl = [span for span in loaded if not is_readme_path(_span_path(span))]
    return impl if len(impl) >= 2 else loaded


def code_first_chunk_choices(
    db: Session,
    generation: IndexGeneration,
    *,
    question: str = "",
    limit: int = 24,
) -> list:
    from app.retrieval.catalog_context import catalog_implementation_chunk_choices

    return catalog_implementation_chunk_choices(db, generation, question, limit=limit)


def _span_path(span) -> str:
    if isinstance(span, dict):
        return str(span.get("relative_path") or "")
    return str(getattr(span, "relative_path", "") or "")


CODE_FIRST_SUFFIX = (
    "\nGround the answer in implementation source files present in the repository excerpts (largest symbol bodies first). "
    "Use README or package.json only when they add product or setup facts the code excerpts do not cover—never instead of code for how-it-works questions. "
    "Do not say \"the excerpts\", \"this packet\", or \"README alone\". "
    "Write in flowing paragraphs: do not put a file path, import list, or export name in every sentence. "
    "When the question covers several flows or features, explain each from the code excerpts provided—do not stop at imports."
)

CODE_TRACE_SUFFIX = (
    "\nThe user wants a step-by-step walkthrough in the implementation excerpts: follow real control flow "
    "(entry function → branches → calls → returns) in order. Name functions and key variables from the code. "
    "Do not substitute Makefile/README/build docs when class or function bodies for the topic are present. "
    "If only a benchmark driver is present, trace that driver line-by-line; do not claim internals that are not in the excerpts. "
    "When the question asks for a request or execution flow, organise claims in execution order and cite the "
    "source lines that establish each hop. Prefer a partial cited trace over empty claims when some stages are present."
)
