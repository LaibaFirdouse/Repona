"""Format prior Q&A turns for retrieval planning and grounded answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.core.config import settings
from app.retrieval.answer_prose import sanitize_answer_prose


@dataclass(frozen=True)
class QaTurn:
    question: str
    answer: str = ""


def normalize_turns(history: Iterable[QaTurn] | None) -> list[QaTurn]:
    if not history:
        return []
    kept: list[QaTurn] = []
    for item in history:
        question = (item.question or "").strip()
        if not question:
            continue
        answer = (item.answer or "").strip()
        if len(answer) > settings.answer_conversation_answer_chars:
            answer = answer[: settings.answer_conversation_answer_chars].rstrip() + "…"
        kept.append(QaTurn(question=question, answer=answer))
    if len(kept) > settings.answer_conversation_max_turns:
        kept = kept[-settings.answer_conversation_max_turns :]
    return kept


def retrieval_question_text(question: str, history: list[QaTurn]) -> str:
    """Expand a follow-up into one retrieval string for lanes and lexical search."""
    current = question.strip()
    if not history:
        return current
    parts: list[str] = []
    for turn in history[-3:]:
        line = f"User asked: {turn.question}"
        if turn.answer:
            summary = turn.answer[:400].rstrip()
            if len(turn.answer) > 400:
                summary += "…"
            line += f" Assistant answered: {summary}"
        parts.append(line)
    parts.append(f"Follow-up question: {current}")
    return " ".join(parts)


def format_planner_user(question: str, history: list[QaTurn]) -> str:
    lines = [
        "Return JSON for QueryPlanV1 fields only (schema_version=1).",
        "Resolve pronouns and references from the conversation when planning retrieval.",
        "Classify intent precisely: exact_lookup, execution_trace, architecture_question, debugging, or conceptual_search.",
        "For HTTP/API request flow: path_filters toward routes, handlers, api/, middleware; intent execution_trace.",
        "For algorithms, queues, benchmarks, or make/cmake flow: path_filters toward include/, src/, adapters/, benchmark/; "
        "intent execution_trace when the user wants steps or flow.",
        "For tell-me-everything or purpose questions: architecture_question; include README in lexical_queries.",
        "For how-it-works without a trace ask: conceptual_search with needs_source_bodies true and implementation paths.",
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
