from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class RepositoryCreateRequest(BaseModel):
    repo_url: HttpUrl = Field(..., description="URL of the repository to analyze")


class DirectoryEntry(BaseModel):
    name: str
    path: str
    kind: Literal["directory", "file"]
    children: list["DirectoryEntry"] = Field(default_factory=list)


class TechnologyDetection(BaseModel):
    name: str
    category: str
    source: str


class RepositoryMetadata(BaseModel):
    file_count: int
    directory_count: int
    ignored_directories: list[str]
    technologies: list[TechnologyDetection]
    directory_structure: list[DirectoryEntry]


class RepositorySummary(BaseModel):
    executive_summary: str
    main_technologies: list[str]
    architecture_observations: list[str]
    notable_directories: list[str]
    next_steps: list[str]


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class RepositorySummaryResult(BaseModel):
    summary: RepositorySummary
    token_usage: TokenUsage


class RepositoryGraphStats(BaseModel):
    file_nodes: int
    module_nodes: int
    service_nodes: int
    file_import_relationships: int
    module_use_relationships: int


class RepositoryCreateResponse(BaseModel):
    repository_id: str
    analysis_report_id: str
    repo_url: HttpUrl
    status: str
    message: str
    metadata: RepositoryMetadata
    summary: RepositorySummary
    token_usage: TokenUsage
    graph: RepositoryGraphStats
