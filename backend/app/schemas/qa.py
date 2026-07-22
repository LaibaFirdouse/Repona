from pydantic import BaseModel, Field

from app.schemas.repository import TokenUsage


class RepositoryQuestionRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=3, max_length=2000)


class RepositoryQuestionAnswer(BaseModel):
    answer: str
    confidence: str
    sources: list[str]
    graph_context_used: bool


class RepositoryQuestionResponse(BaseModel):
    repository_id: str
    question: str
    answer: RepositoryQuestionAnswer
    token_usage: TokenUsage
