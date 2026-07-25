from pydantic import BaseModel


class RepositoryQuestionRequest(BaseModel):
    question: str


class RepositoryQuestionResponse(BaseModel):
    answer: str
    sources: list[str]