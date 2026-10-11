from typing import Literal

from pydantic import BaseModel, Field


class RepositoryQuestionRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1, max_length=36)
    question: str = Field(..., min_length=3, max_length=2000)


class RepositoryQuestionAnswer(BaseModel):
    answer: str
    confidence: str
    sources: list[str]
    graph_context_used: bool


class Claim(BaseModel):
    kind: Literal["observed", "inference", "suggestion"]
    text: str
    evidence_ids: list[str]


class Citation(BaseModel):
    chunk_id: str
    path: str
    start_line: int | None
    end_line: int | None


class GraphFact(BaseModel):
    edge_id: str
    relationship_type: str
    direction: Literal["outgoing", "incoming"]
    source_id: str
    target_id: str
    source_name: str
    target_name: str
    mentioned_name: str
    resolution: str


class RepositoryQuestionResponse(BaseModel):
    repository_id: str
    question: str
    action: Literal["serve", "wait"]
    generation_id: str | None = None
    commit_sha: str | None = None
    message: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    graph_facts: list[GraphFact] = Field(default_factory=list)
    citations_valid: bool = False
