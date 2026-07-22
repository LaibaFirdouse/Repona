from __future__ import annotations

import json

from app.schemas.repository import (
    DirectoryEntry,
    RepositoryMetadata,
    RepositorySummary,
    RepositorySummaryResult,
    TokenUsage,
)
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.llm_factory import LLMFactory


class OpenAISummaryServiceError(Exception):
    pass


class OpenAISummaryService:
    def __init__(
        self,
        client: object | None = None,
        llm_provider: BaseLLMProvider | None = None,
    ) -> None:
        self.client = client
        self.llm_provider = llm_provider or LLMFactory.create_provider()

    def summarize_repository(
        self,
        repo_url: str,
        metadata: RepositoryMetadata,
    ) -> RepositorySummaryResult:
        system_prompt = self.build_system_prompt()
        user_prompt = self.build_user_prompt(repo_url, metadata)
        response_content = self.call_llm(system_prompt, user_prompt)
        summary = self.parse_summary(response_content)
        return RepositorySummaryResult(summary=summary, token_usage=TokenUsage())

    def build_system_prompt(self) -> str:
        return (
            "You are a senior backend engineer analyzing repository metadata. "
            "Return only valid JSON. Do not include markdown. "
            "Be concise, practical, and beginner-friendly. "
            "Base your answer only on the metadata provided."
        )

    def build_user_prompt(self, repo_url: str, metadata: RepositoryMetadata) -> str:
        prompt_payload = {
            "repo_url": repo_url,
            "file_count": metadata.file_count,
            "directory_count": metadata.directory_count,
            "technologies": [
                (
                    technology.model_dump()
                    if hasattr(technology, "model_dump")
                    else technology.dict()
                )
                for technology in metadata.technologies
            ],
            "top_level_structure": [
                self.serialize_directory_entry(entry)
                for entry in metadata.directory_structure[:30]
            ],
            "required_json_shape": {
                "executive_summary": "short paragraph explaining what the repository appears to be",
                "main_technologies": ["technology names"],
                "architecture_observations": ["practical observations from metadata"],
                "notable_directories": [
                    "important directory paths and why they matter"
                ],
                "next_steps": ["suggested next analysis steps"],
            },
        }
        return json.dumps(prompt_payload, indent=2)

    def serialize_directory_entry(
        self, entry: DirectoryEntry, max_depth: int = 2
    ) -> dict:
        serialized_entry = {
            "name": entry.name,
            "path": entry.path,
            "kind": entry.kind,
        }

        if entry.kind == "directory" and max_depth > 0:
            serialized_entry["children"] = [
                self.serialize_directory_entry(child, max_depth - 1)
                for child in entry.children[:20]
            ]

        return serialized_entry

    
    def call_llm(self, system_prompt: str, user_prompt: str) -> str:
           prompt = self.build_provider_prompt(system_prompt, user_prompt)
           print("=" * 80)
           print("PROMPT LENGTH:", len(prompt))
           print(prompt[:1000])   # only first 1000 chars
           print("=" * 80)

           try:
             return self.llm_provider.generate(prompt)
           except Exception as error:
             import traceback
             traceback.print_exc()
             raise OpenAISummaryServiceError(str(error)) from error
        

    def build_provider_prompt(self, system_prompt: str, user_prompt: str) -> str:
        return f"SYSTEM PROMPT:\n{system_prompt}\n\nUSER PROMPT:\n{user_prompt}"

    def parse_summary(self, response_content: str) -> RepositorySummary:
        try:
            summary_payload = json.loads(response_content)
        except json.JSONDecodeError as error:
            raise OpenAISummaryServiceError("LLM returned invalid JSON.") from error

        try:
            return RepositorySummary(**summary_payload)
        except ValueError as error:
            raise OpenAISummaryServiceError(
                "OpenAI summary did not match the expected shape."
            ) from error
