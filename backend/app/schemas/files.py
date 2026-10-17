from __future__ import annotations

from pydantic import BaseModel, Field


class RepositoryFileListResponse(BaseModel):
    repository_id: str
    paths: list[str] = Field(default_factory=list)


class FileLine(BaseModel):
    n: int
    text: str


class RepositoryFileContentResponse(BaseModel):
    path: str
    total_lines: int
    focus_start: int | None = None
    focus_end: int | None = None
    lines: list[FileLine] = Field(default_factory=list)
