"""Fast deterministic query routing with optional LLM query planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable

from app.core.config import settings
from app.retrieval.query_plan import PLAN_SCHEMA_VERSION, QueryPlanV1, RetrievalLane
from app.retrieval.query_terms import build_lexical_queries, extract_search_terms, normalize_question

_TRACE_WORDS = re.compile(
    r"\b(trace|call\s*stack|stack\s*trace|who\s+calls|call\s+chain|data\s+flow|propagat)\b",
    re.I,
)
_DEBUG_WORDS = re.compile(r"\b(bug|error|exception|fail|broken|fix|regression|crash)\b", re.I)
_ARCH_WORDS = re.compile(r"\b(architect|design|structure|module|layer|component|overview)\b", re.I)
_PATH_HINT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+|[A-Za-z0-9_.-]+/")


@dataclass(frozen=True)
class RetrievalRequest:
    """Resolved retrieval parameters for AnswerService lanes."""

    question: str
    intent: str
    identifiers: list[str]
    path_filters: list[str]
    lexical_queries: list[str]
    semantic_query: str
    lanes: list[RetrievalLane]
    max_graph_hops: int
    needs_source_bodies: bool
    used_llm_planner: bool = False
    planner_fallback_reason: str | None = None
    router_ms: float = 0.0
    trace: dict = field(default_factory=dict)


def vector_lane_will_run(
    request: RetrievalRequest,
    *,
    lexical_hit_count: int,
    name_hit_count: int,
    vectors_ready: int,
) -> bool:
    """True when the vector lane is already requested or would be expanded for this query."""
    if "vector" in request.lanes:
        return True
    weak = lexical_hit_count + name_hit_count < settings.lexical_min_hits
    if not weak and request.intent not in {"conceptual_search", "architecture_question", "debugging"}:
        return False
    if vectors_ready == 0 and not settings.lazy_embed_on_query:
        return False
    return True


def expand_vector_lane(
    request: RetrievalRequest,
    *,
    lexical_hit_count: int,
    name_hit_count: int,
    vectors_ready: int,
) -> RetrievalRequest:
    """Enable semantic search when lexical/name evidence is weak but vectors exist or can be built."""
    lanes = list(request.lanes)
    if "vector" in lanes:
        return request
    if not vector_lane_will_run(
        request,
        lexical_hit_count=lexical_hit_count,
        name_hit_count=name_hit_count,
        vectors_ready=vectors_ready,
    ):
        return request
    lanes.append("vector")
    return RetrievalRequest(
        question=request.question,
        intent=request.intent,
        identifiers=request.identifiers,
        path_filters=request.path_filters,
        lexical_queries=request.lexical_queries,
        semantic_query=request.semantic_query,
        lanes=lanes,
        max_graph_hops=request.max_graph_hops,
        needs_source_bodies=request.needs_source_bodies,
        used_llm_planner=request.used_llm_planner,
        planner_fallback_reason=request.planner_fallback_reason,
        router_ms=request.router_ms,
        trace={**request.trace, "vector_lane_expanded": True},
    )


def route_question(
    question: str,
    *,
    planner: Callable[[str], QueryPlanV1 | None] | None = None,
) -> RetrievalRequest:
    started = perf_counter()
    normalized = normalize_question(question)
    terms = extract_search_terms(normalized)
    paths = [token for token in terms if "/" in token or _PATH_HINT.search(token)]
    identifiers = [token for token in terms if token not in paths]

    intent = _deterministic_intent(normalized, identifiers, paths)
    lanes = _deterministic_lanes(intent, identifiers, paths, normalized)
    lexical = build_lexical_queries(normalized)
    semantic = normalized
    used_planner = False
    fallback_reason: str | None = None
    max_hops = 1
    needs_bodies = True

    if _should_invoke_planner(intent, normalized, identifiers, paths) and planner is not None:
        plan = planner(normalized)
        if plan is not None and plan.schema_version == PLAN_SCHEMA_VERSION:
            used_planner = True
            intent = plan.intent
            identifiers = plan.identifiers or identifiers
            paths = plan.path_filters or paths
            lexical = plan.lexical_queries or lexical
            semantic = plan.semantic_query or semantic
            lanes = list(plan.retrieval_lanes)
            max_hops = plan.max_graph_hops
            needs_bodies = plan.needs_source_bodies
        else:
            fallback_reason = "planner_unavailable_or_invalid"

    elapsed = (perf_counter() - started) * 1000
    return RetrievalRequest(
        question=normalized,
        intent=intent,
        identifiers=identifiers[:16],
        path_filters=paths[:8],
        lexical_queries=lexical[:4],
        semantic_query=semantic[:2000],
        lanes=lanes,
        max_graph_hops=max_hops,
        needs_source_bodies=needs_bodies,
        used_llm_planner=used_planner,
        planner_fallback_reason=fallback_reason,
        router_ms=round(elapsed, 2),
        trace={
            "intent": intent,
            "lanes": lanes,
            "used_llm_planner": used_planner,
            "planner_fallback_reason": fallback_reason,
        },
    )


def _deterministic_intent(question: str, identifiers: list[str], paths: list[str]) -> str:
    if paths and len(paths) >= len(identifiers):
        return "exact_lookup"
    if identifiers and len(question.split()) <= 12 and not _TRACE_WORDS.search(question):
        if len(identifiers) == 1 or any("." in item for item in identifiers):
            return "exact_lookup"
        if any(len(item) >= 4 and ("_" in item or item[:1].islower()) for item in identifiers):
            return "exact_lookup"
    if _TRACE_WORDS.search(question):
        return "execution_trace"
    if _DEBUG_WORDS.search(question):
        return "debugging"
    if _ARCH_WORDS.search(question):
        return "architecture_question"
    return "conceptual_search"


def _deterministic_lanes(intent: str, identifiers: list[str], paths: list[str], question: str) -> list[RetrievalLane]:
    if paths:
        return ["symbol", "lexical"]
    if intent == "exact_lookup" and identifiers:
        return ["symbol", "lexical"]
    if intent == "execution_trace":
        return ["symbol", "graph", "lexical"]
    if intent == "debugging":
        return ["symbol", "lexical", "vector"]
    if intent == "architecture_question":
        return ["symbol", "lexical", "vector", "graph"]
    return ["symbol", "lexical", "vector"]


def _should_invoke_planner(intent: str, question: str, identifiers: list[str], paths: list[str]) -> bool:
    if not settings.query_planner_enabled:
        return False
    if intent in {"exact_lookup"} and (paths or len(identifiers) <= 2):
        return False
    complexity = len(question.split()) + len(identifiers) + len(paths)
    if intent == "execution_trace":
        return complexity >= settings.query_planner_min_complexity
    if intent == "conceptual_search" and complexity >= settings.query_planner_min_complexity + 4:
        return True
    return complexity >= settings.query_planner_min_complexity + 6


def parse_planner_json(raw: str) -> QueryPlanV1 | None:
    try:
        payload = json.loads(raw)
        return QueryPlanV1.model_validate(payload)
    except (json.JSONDecodeError, ValueError):
        return None


PLANNER_SYSTEM = (
    "You plan repository code retrieval. Return JSON only matching QueryPlanV1. "
    "Do not answer the user. Do not emit SQL or Cypher."
)
