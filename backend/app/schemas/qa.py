from typing import Literal

from pydantic import BaseModel, Field

AskProviderName = Literal["cursor", "claude", "codex", "ollama", "opencode", "zai", "copilot"]
ASK_PROVIDERS = frozenset(("cursor", "claude", "codex", "ollama", "opencode", "zai", "copilot"))


class ConversationTurn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=8000)


class RepositoryQuestionRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1, max_length=36)
    question: str = Field(..., min_length=3, max_length=2000)
    history: list[ConversationTurn] = Field(
        default_factory=list,
        max_length=32,
        description="Prior turns in this chat (oldest first). Used for follow-up questions.",
    )
    provider: AskProviderName | None = None
    model: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(/[A-Za-z0-9][A-Za-z0-9._-]{0,80})?$",
        description="Model id for the chosen provider. OpenCode uses provider/model. Omit it to use that provider's default.",
    )


class Citation(BaseModel):
    chunk_id: str
    path: str
    start_line: int | None
    end_line: int | None


class RepositoryQuestionResponse(BaseModel):
    repository_id: str
    question: str
    action: Literal["serve", "wait"]
    generation_id: str | None = None
    commit_sha: str | None = None
    message: str
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    provider: AskProviderName | None = None
    model: str | None = None
