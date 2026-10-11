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


class RepositoryQuestionResponse(BaseModel):
    repository_id: str
    question: str
    action: Literal["serve", "wait"]
    generation_id: str | None
    commit_sha: str | None
    message: str
