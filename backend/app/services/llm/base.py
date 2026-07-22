from __future__ import annotations


class LLMProviderError(Exception):
    """Raised when a provider cannot generate a response."""


class BaseLLMProvider:
    def generate(self, prompt: str) -> str:
        raise NotImplementedError
