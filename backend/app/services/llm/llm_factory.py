from __future__ import annotations

import os

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.codex_provider import CodexProvider
from app.services.llm.copilot_provider import CopilotProvider
from app.services.llm.cursor_provider import CursorProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.opencode_provider import OpenCodeProvider
from app.services.llm.zai_provider import ZaiProvider


def provider_name(requested: str | None = None) -> str:
    name = (requested or os.getenv("LLM_PROVIDER") or settings.llm_provider).strip().lower()
    if name == "claude-code":
        return "claude"
    return name


class LLMFactory:
    @staticmethod
    def create_provider(model: str | None = None, provider: str | None = None) -> BaseLLMProvider:
        selected = provider_name(provider)
        if selected == "cursor":
            return CursorProvider(model=model)
        if selected == "claude":
            return ClaudeProvider(model=model)
        if selected == "codex":
            return CodexProvider(model=model)
        if selected == "opencode":
            return OpenCodeProvider(model=model)
        if selected == "zai":
            return ZaiProvider(model=model)
        if selected == "copilot":
            return CopilotProvider(model=model)
        if selected == "ollama":
            return OllamaProvider(model=model)
        if selected == "openai":
            return OpenAIProvider(model=model)
        raise ValueError(f"Unsupported LLM provider: {selected}")

    @staticmethod
    def configured_model(provider: str | None = None) -> str:
        selected = provider_name(provider)
        if selected == "cursor":
            return os.getenv("CURSOR_MODEL") or settings.cursor_model
        if selected == "claude":
            return os.getenv("CLAUDE_MODEL") or settings.claude_model
        if selected == "codex":
            return os.getenv("CODEX_MODEL") or settings.codex_model
        if selected == "opencode":
            return os.getenv("OPENCODE_MODEL") or settings.opencode_model
        if selected == "zai":
            return os.getenv("ZAI_MODEL") or settings.zai_model
        if selected == "copilot":
            return os.getenv("COPILOT_MODEL") or settings.copilot_model
        if selected == "ollama":
            return os.getenv("OLLAMA_MODEL") or settings.ollama_model
        return os.getenv("OPENAI_MODEL") or settings.openai_model
