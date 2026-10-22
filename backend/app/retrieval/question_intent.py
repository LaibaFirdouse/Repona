"""Deterministic question intent and retrieval defaults when the LLM planner is off or weak."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.retrieval.chunk_selection import wants_code_trace, wants_substantive_implementation
from app.retrieval.query_plan import QueryIntent, QueryPlanV1, RetrievalLane

if TYPE_CHECKING:
    from app.retrieval.conversation_context import QaTurn

_EXACT_LOOKUP = re.compile(
    r"\b("
    r"where is|where are|find the|locate|which file|what file|line number|"
    r"defined in|declaration of|definition of|who calls|callers of|"
    r"show me the file|path to|grep for"
    r")\b",
    re.I,
)

_DEBUGGING = re.compile(
    r"\b("
    r"error|exception|fail(?:s|ed|ure)?|bug|crash|broken|doesn'?t work|"
    r"wrong result|regression|panic|stack trace|why (?:does|is|do|are).+(?:fail|break|error)"
    r")\b",
    re.I,
)

_ARCHITECTURE = re.compile(
    r"\b("
    r"tell me everything|high[- ]level|architecture|big picture|overview|"
    r"what is this repo|what'?s this repo|purpose of|components|structure of the (?:repo|project|codebase)|"
    r"how (?:is|are) .+ (?:organized|structured)|design of"
    r")\b",
    re.I,
)

_EXECUTION_TRACE = re.compile(
    r"\b("
    r"request flow|execution flow|call flow|data flow|control flow|"
    r"end[- ]to[- ]end|from .+ to .+|walk through|step by step|"
    r"trace|call sequence|what happens when|what runs when|"
    r"how exactly|line by line|follow the code"
    r")\b",
    re.I,
)

_CONCEPTUAL_HOW = re.compile(
    r"\b(how does|how do|what does|what do|explain how|explain what)\b",
    re.I,
)


def _combined_text(question: str, history: list[QaTurn] | None) -> str:
    parts = [question.strip()]
    if history:
        for turn in history[-3:]:
            parts.append(turn.question.strip())
    return " ".join(part for part in parts if part)


def classify_intent(question: str, history: list[QaTurn] | None = None) -> QueryIntent:
    """Map natural language to QueryPlanV1 intent without an LLM."""
    text = question.strip()
    blob = _combined_text(question, history)

    if _EXACT_LOOKUP.search(text):
        return "exact_lookup"
    if _DEBUGGING.search(text):
        return "debugging"
    if _ARCHITECTURE.search(text) and not _EXECUTION_TRACE.search(text):
        return "architecture_question"
    if (
        _EXECUTION_TRACE.search(blob)
        or wants_code_trace(question, history or [])
        or wants_substantive_implementation(question, history or [])
    ):
        return "execution_trace"
    if _CONCEPTUAL_HOW.search(text) or _DEBUGGING.search(blob):
        return "conceptual_search"
    return "conceptual_search"


def default_lanes_for_intent(intent: QueryIntent) -> list[RetrievalLane]:
    if intent == "exact_lookup":
        return ["symbol", "lexical"]
    if intent == "architecture_question":
        return ["symbol", "lexical", "vector"]
    if intent == "debugging":
        return ["symbol", "lexical", "vector", "graph"]
    if intent == "execution_trace":
        return ["symbol", "lexical", "vector", "graph"]
    return ["symbol", "lexical", "vector", "graph"]


def default_graph_hops(intent: QueryIntent) -> int:
    if intent in {"debugging", "execution_trace"}:
        return 2
    if intent == "exact_lookup":
        return 0
    return 1


def heuristic_path_filters(question: str, history: list[QaTurn] | None = None) -> list[str]:
    """Path hints from the question text only—no repo-specific prefix lists."""
    text = _combined_text(question, history)
    hints: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        token = raw.strip().strip("/")
        if not token or token in seen:
            return
        seen.add(token)
        hints.append(token)

    for match in re.finditer(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+", text):
        add(match.group(0))
    for match in re.finditer(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\b", text):
        add(match.group(0))
    return hints[:8]


def needs_source_bodies_for_intent(intent: QueryIntent, question: str) -> bool:
    if intent == "architecture_question":
        if re.search(r"\b(readme|docs only|documentation only)\b", question, re.I):
            return False
    if intent == "exact_lookup":
        return True
    return True


def heuristic_plan_patch(question: str, history: list[QaTurn] | None = None) -> dict:
    intent = classify_intent(question, history)
    return {
        "intent": intent,
        "retrieval_lanes": default_lanes_for_intent(intent),
        "max_graph_hops": default_graph_hops(intent),
        "path_filters": heuristic_path_filters(question, history),
        "needs_source_bodies": needs_source_bodies_for_intent(intent, question),
    }


def merge_plan_with_heuristics(
    plan: QueryPlanV1,
    question: str,
    history: list[QaTurn] | None = None,
) -> QueryPlanV1:
    """Keep planner fields but fix weak intent and add path hints."""
    patch = heuristic_plan_patch(question, history)
    intent = plan.intent
    if plan.intent == "conceptual_search" and patch["intent"] != "conceptual_search":
        intent = patch["intent"]
    paths = list(plan.path_filters)
    for item in patch["path_filters"]:
        if item not in paths:
            paths.append(item)
    lanes = list(plan.retrieval_lanes)
    if intent in {"execution_trace", "debugging"} and "graph" not in lanes:
        lanes.append("graph")
    hops = max(plan.max_graph_hops, patch["max_graph_hops"])
    bodies = plan.needs_source_bodies or patch["needs_source_bodies"]
    return plan.model_copy(
        update={
            "intent": intent,
            "path_filters": paths[:8],
            "retrieval_lanes": lanes[:4],
            "max_graph_hops": min(hops, 3),
            "needs_source_bodies": bodies,
        }
    )


def apply_heuristic_fallback(
    *,
    question: str,
    history: list[QaTurn] | None,
    identifiers: list[str],
    path_filters: list[str],
    lexical_queries: list[str],
    semantic_query: str,
) -> tuple[QueryIntent, list[RetrievalLane], int, bool, list[str], list[str]]:
    patch = heuristic_plan_patch(question, history)
    intent = patch["intent"]
    lanes = patch["retrieval_lanes"]
    hops = patch["max_graph_hops"]
    bodies = patch["needs_source_bodies"]
    paths = list(dict.fromkeys(path_filters + patch["path_filters"]))[:8]
    return intent, lanes, hops, bodies, paths, lexical_queries


def answer_suffix_for_intent(intent: QueryIntent) -> str:
    if intent == "exact_lookup":
        return (
            "\nThe user wants a precise location: lead with file path and symbol or line facts from the excerpts, "
            "then briefly state what that symbol does if the excerpts show it."
        )
    if intent == "execution_trace":
        return (
            "\nAnswer as an ordered walkthrough of the code path: each step should name the function or block, "
            "what it reads/writes, and what it calls next. Prefer implementation files over build scripts unless "
            "the question is explicitly about build or benchmark harness entrypoints."
        )
    if intent == "architecture_question":
        return (
            "\nGive a coherent overview of purpose and major components, grounded in README and module entrypoints "
            "when present, plus one or two implementation excerpts for the most important runtime path."
        )
    if intent == "debugging":
        return (
            "\nFocus on failure paths, error handling, guards, and callers shown in the excerpts. "
            "Separate observed behavior from hypotheses."
        )
    return (
        "\nAnswer the question directly and precisely; prefer concrete identifiers and behavior from code over "
        "generic summaries of documentation."
    )


PLANNER_SYSTEM = (
    "You plan repository code retrieval. Return JSON only matching QueryPlanV1 (schema_version=1). "
    "Do not answer the user. Do not emit SQL or Cypher.\n\n"
    "Intent (pick one):\n"
    "- exact_lookup: find where a symbol, file, or definition lives.\n"
    "- execution_trace: step-by-step control/data flow through code (includes HTTP request handling, CLI, "
    "benchmark harness main→threads, or algorithm internals when the user asks how something runs).\n"
    "- architecture_question: repo purpose, components, high-level design (not line-by-line).\n"
    "- debugging: errors, failures, unexpected behavior, regressions.\n"
    "- conceptual_search: what something does or means without requiring a full trace.\n\n"
    "Disambiguation:\n"
    "- 'request flow' / 'how does a request get handled' → execution_trace; path_filters and lexical_queries must name "
    "concrete modules, routes, handlers, or entrypoints inferred from the question and repo kind—not README alone.\n"
    "- 'how exactly' / 'trace' / 'step by step' → execution_trace; lexical_queries must include the subject "
    "(class, function, or file stem the user named).\n"
    "- 'tell me everything' / 'overview' → architecture_question; vector+lexical lanes, moderate graph hops.\n\n"
    "Fields:\n"
    "- semantic_query: standalone English retrieval phrase (resolve follow-ups from conversation).\n"
    "- lexical_queries: 2–4 short Postgres tsquery phrases (symbols, paths, domain terms).\n"
    "- path_filters: up to 8 path substrings copied from the question or likely source paths for the subject "
    "(use path segments that appear in the user's words when present).\n"
    "- identifiers: symbol or type names mentioned or implied.\n"
    "- retrieval_lanes: subset of symbol, lexical, vector, graph (exact_lookup often drops vector).\n"
    "- max_graph_hops: 0–3 (2 for trace/debugging).\n"
    "- needs_source_bodies: false only for pure doc/overview with no code walkthrough asked.\n"
    "When conversation history is present, expand semantic_query and lexical_queries so they stand alone."
)
