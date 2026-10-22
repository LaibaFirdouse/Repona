"""LLM query planning with deterministic fallback when the planner fails."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable

from app.core.config import settings
from app.retrieval.question_intent import (
    PLANNER_SYSTEM,
    apply_heuristic_fallback,
    merge_plan_with_heuristics,
)
from app.retrieval.query_plan import PLAN_SCHEMA_VERSION, QueryPlanV1, RetrievalLane
from app.retrieval.query_terms import build_lexical_queries, extract_search_terms, normalize_question

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
    """True when the vector lane is requested or lazy embed can run for this query."""
    if "vector" in request.lanes:
        return True
    if vectors_ready == 0 and not settings.lazy_embed_on_query:
        return False
    return False


def expand_vector_lane(
    request: RetrievalRequest,
    *,
    lexical_hit_count: int,
    name_hit_count: int,
    vectors_ready: int,
) -> RetrievalRequest:
    """Legacy hook for benchmarks; lanes come from the LLM planner."""
    return request


def route_question(
    question: str,
    *,
    planner: Callable[[str], QueryPlanV1 | None] | None = None,
    history: list | None = None,
    intent_question: str | None = None,
) -> RetrievalRequest:
    started = perf_counter()
    normalized = normalize_question(question)
    intent_source = (intent_question or question).strip()
    terms = extract_search_terms(normalized)
    paths = [token for token in terms if "/" in token or _PATH_HINT.search(token)]
    identifiers = [token for token in terms if token not in paths]
    lexical = build_lexical_queries(normalized)
    semantic = normalized
    used_planner = False
    fallback_reason: str | None = None
    intent = "conceptual_search"
    lanes: list[RetrievalLane] = ["symbol", "lexical", "vector", "graph"]
    max_hops = 1
    needs_bodies = True

    if planner is not None and settings.query_planner_enabled:
        plan = planner(normalized)
        if plan is not None and plan.schema_version == PLAN_SCHEMA_VERSION:
            used_planner = True
            plan = merge_plan_with_heuristics(plan, intent_source, history)
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
    elif settings.query_planner_enabled:
        fallback_reason = "planner_not_provided"
    else:
        fallback_reason = "query_planner_disabled"

    if not used_planner:
        intent, lanes, max_hops, needs_bodies, paths, lexical = apply_heuristic_fallback(
            question=intent_source,
            history=history,
            identifiers=identifiers,
            path_filters=paths,
            lexical_queries=lexical,
            semantic_query=semantic,
        )

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
            "heuristic_intent": intent,
        },
    )


def parse_planner_json(raw: str) -> QueryPlanV1 | None:
    try:
        payload = json.loads(raw)
        return QueryPlanV1.model_validate(payload)
    except (json.JSONDecodeError, ValueError):
        return None
