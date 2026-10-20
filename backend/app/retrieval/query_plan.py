"""Versioned structured retrieval plan returned by the optional LLM planner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

PLAN_SCHEMA_VERSION = 1

QueryIntent = Literal[
    "exact_lookup",
    "conceptual_search",
    "execution_trace",
    "debugging",
    "architecture_question",
]

RetrievalLane = Literal["symbol", "lexical", "vector", "graph"]

GraphRelation = Literal["calls", "imports", "uses"]


class QueryPlanV1(BaseModel):
    """Retrieval plan only — never SQL/Cypher or generation control."""

    schema_version: int = Field(default=PLAN_SCHEMA_VERSION, ge=1, le=1)
    intent: QueryIntent = "conceptual_search"
    search_terms: list[str] = Field(default_factory=list, max_length=16)
    identifiers: list[str] = Field(default_factory=list, max_length=16)
    path_filters: list[str] = Field(default_factory=list, max_length=8)
    lexical_queries: list[str] = Field(default_factory=list, max_length=4)
    semantic_query: str = Field(default="", max_length=2000)
    retrieval_lanes: list[RetrievalLane] = Field(
        default_factory=lambda: ["symbol", "lexical", "vector", "graph"],
        max_length=4,
    )
    graph_relations: list[GraphRelation] = Field(default_factory=lambda: ["calls", "imports"], max_length=3)
    max_graph_hops: int = Field(default=1, ge=0, le=3)
    source_targets: list[str] = Field(default_factory=list, max_length=12)
    needs_source_bodies: bool = True

    @field_validator("search_terms", "identifiers", "path_filters", "lexical_queries", "source_targets")
    @classmethod
    def _strip_terms(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("semantic_query")
    @classmethod
    def _strip_semantic(cls, value: str) -> str:
        return value.strip()

    @field_validator("retrieval_lanes")
    @classmethod
    def _lanes_nonempty(cls, values: list[RetrievalLane]) -> list[RetrievalLane]:
        if not values:
            return ["symbol", "lexical", "vector"]
        seen: list[RetrievalLane] = []
        for lane in values:
            if lane not in seen:
                seen.append(lane)
        return seen
