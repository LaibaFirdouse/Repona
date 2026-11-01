from __future__ import annotations

import os

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.codex_provider import CodexProvider
from app.services.llm.copilot_provider import CopilotProvider
from app.services.llm.cursor_provider import CursorProvider
from app.services.llm.grok_provider import GrokProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.opencode_provider import OpenCodeProvider
from app.services.llm.zai_provider import ZaiProvider
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.openrouter_provider import OpenRouterProvider

def provider_name(requested: str | None = None) -> str:
    name = (
        requested
        or os.getenv("LLM_PROVIDER")
        or settings.llm_provider
    ).strip().lower()

    if name == "claude-code":
        return "claude"

    return name


class LLMFactory:

    @staticmethod
    def create_provider(
        model: str | None = None,
        provider: str | None = None,
    ) -> BaseLLMProvider:

        selected = provider_name(provider)

        providers = {
            "cursor": CursorProvider,
            "openrouter": OpenRouterProvider,
            "claude": ClaudeProvider,
            "codex": CodexProvider,
            "opencode": OpenCodeProvider,
            "zai": ZaiProvider,
            "grok": GrokProvider,
            "copilot": CopilotProvider,
            "ollama": OllamaProvider,
            "openai": OpenAIProvider,
            "groq": GroqProvider,
        }

        provider_class = providers.get(selected)

        if provider_class is None:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        return provider_class(model=model)

    @staticmethod
    def configured_model(provider: str | None = None) -> str:

        selected = provider_name(provider)

        models = {
            "openrouter": (os.getenv("OPENROUTER_MODEL"),settings.openrouter_model),
            "cursor": (os.getenv("CURSOR_MODEL"), settings.cursor_model),
            "claude": (os.getenv("CLAUDE_MODEL"), settings.claude_model),
            "codex": (os.getenv("CODEX_MODEL"), settings.codex_model),
            "opencode": (os.getenv("OPENCODE_MODEL"), settings.opencode_model),
            "zai": (os.getenv("ZAI_MODEL"), settings.zai_model),
            "grok": (os.getenv("GROK_MODEL"), settings.grok_model),
            "copilot": (os.getenv("COPILOT_MODEL"), settings.copilot_model),
            "ollama": (os.getenv("OLLAMA_MODEL"), settings.ollama_model),
            "openai": (os.getenv("OPENAI_MODEL"), settings.openai_model),
            "groq": (os.getenv("GROQ_MODEL"), settings.groq_model),
        }

        if selected not in models:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        env_model, configured_model = models[selected]

        return env_model or configured_model
