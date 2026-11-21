from __future__ import annotations


class LLMProviderError(Exception):
    """Raised when a provider cannot generate a response.

    ``failure_kind`` is an optional diagnostic code for smoke tests and traces.
    Public HTTP handlers must still return a generic unavailable message and must
    not expose this field to clients.
    """

    def __init__(self, message: str, *, failure_kind: str | None = None) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind


class BaseLLMProvider:
    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        raise NotImplementedError
