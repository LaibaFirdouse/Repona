from __future__ import annotations


class LLMProviderError(Exception):
    """Raised when a provider cannot generate a response."""


class BaseLLMProvider:
    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        raise NotImplementedError
