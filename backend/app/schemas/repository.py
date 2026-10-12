from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class RepositoryCreateRequest(BaseModel):
    repo_url: HttpUrl = Field(..., description="Public GitHub URL, or a private URL with an access token")
    tracked_branch: str | None = Field(default=None, max_length=255)
    user_id: str = Field(..., min_length=1, max_length=36)
    access_token: str | None = Field(default=None, max_length=2048)


class RepositoryCreateResponse(BaseModel):
    repository_id: str
    normalized_url: str
    tracked_branch: str
    commit_sha: str | None
    generation_id: str | None
    action: Literal["serve", "wait"]
    message: str
