"""Format prior Q&A turns for retrieval planning and grounded answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.core.config import settings
from app.retrieval.answer_prose import sanitize_answer_prose
from app.retrieval.query_terms import extract_search_terms

# A follow-up with fewer distinctive words than this ("and where is it called?") borrows the
# previous question's words; a self-contained question is searched on its own.
_FOLLOW_UP_MIN_TERMS = 3


@dataclass(frozen=True)
class QaTurn:
    question: str
    answer: str = ""


def normalize_turns(history: Iterable[QaTurn] | None) -> list[QaTurn]:
    """Bounded conversation context. Turns that ended in a refusal line are dropped.

    Refusals stay in the stored chat (the UI shows them), but replaying them taught the model to
    refuse again and pulled the unanswered topic into later questions.
    """
    if not history:
        return []
    kept: list[QaTurn] = []
    for item in history:
        question = (item.question or "").strip()
        if not question:
            continue
        answer = (item.answer or "").strip()
        if answer in REFUSAL_LINES:
            continue
        if len(answer) > settings.answer_conversation_answer_chars:
            answer = answer[: settings.answer_conversation_answer_chars].rstrip() + "…"
        kept.append(QaTurn(question=question, answer=answer))
    if len(kept) > settings.answer_conversation_max_turns:
        kept = kept[-settings.answer_conversation_max_turns :]
    return kept


def retrieval_question_text(question: str, history: list[QaTurn]) -> str:
    """Search text for one question: the question itself, plus the previous question for short follow-ups.

    Earlier answers are never included. They used to be (3 turns x 400 chars), which put model
    prose and the words "User"/"Assistant" into every search, pushed the current question past the
    embedding model's 256-token window, and kept earlier topics' symbols marked as required.
    """
    current = question.strip()
    if not history:
        return current
    if len(extract_search_terms(current)) >= _FOLLOW_UP_MIN_TERMS:
        return current
    previous = history[-1].question.strip()
    if not previous:
        return current
    # Current question first so it survives truncation by the embedding model.
    return f"{current} {previous}"


def format_planner_user(question: str, history: list[QaTurn]) -> str:
    lines = [
        "Return JSON for QueryPlanV1 fields only (schema_version=1).",
        "Resolve pronouns and references from the conversation when planning retrieval.",
        "Classify intent precisely: exact_lookup, execution_trace, architecture_question, debugging, or conceptual_search.",
        "Set path_filters and lexical_queries from the user's nouns, symbols, and any paths they mention—never invent "
        "fixed directory names that do not appear in the question unless you infer a standard module name they named "
        "(e.g. a class or file stem).",
        "For tell-me-everything or purpose questions: architecture_question; README may appear in lexical_queries.",
        "For how-it-works without a trace ask: conceptual_search with needs_source_bodies true.",
    ]
    if history:
        lines.append("")
        lines.append("Conversation so far:")
        for turn in history:
            lines.append(f"User: {turn.question}")
            if turn.answer:
                lines.append(f"Assistant: {turn.answer}")
        lines.append("")
    lines.append("Current question (plan retrieval for this message):")
    lines.append(question.strip())
    lines.append(
        'Example shape: {"intent":"conceptual_search","search_terms":[],"identifiers":[],'
        '"path_filters":[],"lexical_queries":[],"semantic_query":"","retrieval_lanes":'
        '["symbol","lexical","vector"],"graph_relations":["calls"],"max_graph_hops":1,'
        '"source_targets":[],"needs_source_bodies":true}'
    )
    return "\n".join(lines)


def format_answer_history(history: list[QaTurn]) -> str:
    if not history:
        return ""
    lines = [
        "Conversation so far (trusted session context — not repository evidence):",
    ]
    for turn in history:
        lines.append(f"User: {turn.question}")
        if turn.answer:
            lines.append(f"Assistant: {sanitize_answer_prose(turn.answer)}")
    lines.append("")
    return "\n".join(lines)
