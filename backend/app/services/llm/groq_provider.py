from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class GroqProvider(BaseLLMProvider):
    """Generate text using the Groq API."""

    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else (os.getenv("GROQ_API_KEY") or settings.groq_api_key)
        ).strip()

        self.model = (
            model
            or os.getenv("GROQ_MODEL")
            or settings.groq_model
        )

        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("GROQ_TIMEOUT") or settings.groq_timeout)
        )

    def generate(
        self,
        system_message: str,
        user_message: str,
        max_output_tokens: int,
    ) -> str:

        if (
            not self.api_key
            or not user_message.strip()
            or max_output_tokens < 1
        ):
            raise LLMProviderError("the model provider is unavailable")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
            "max_completion_tokens": max_output_tokens,
        }

        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
            ) as response:
                body = json.loads(
                    response.read().decode("utf-8")
                )

        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeError,
            TimeoutError,
        ):
            raise LLMProviderError(
                "the model provider is unavailable"
            ) from None

        choices = body.get("choices", [])

        if not choices:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        message = choices[0].get("message", {})
        content = message.get("content")

        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        return content.strip()
