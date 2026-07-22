from __future__ import annotations

import os

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider


class LLMFactory:
    @staticmethod
    def create_provider() -> BaseLLMProvider:
        provider_name = (os.getenv("LLM_PROVIDER") or settings.llm_provider).strip().lower()
        if provider_name == "ollama":
            return OllamaProvider()
        if provider_name == "openai":
            return OpenAIProvider()
        raise ValueError(f"Unsupported LLM provider: {provider_name}")
