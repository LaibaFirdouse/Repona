from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class OpenRouterProvider(BaseLLMProvider):
    """Generate text using the OpenRouter API."""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:

        self.api_key = (
            api_key
            if api_key is not None
            else (
                os.getenv("OPENROUTER_API_KEY")
                or settings.openrouter_api_key
            )
        ).strip()

        self.model = (
            model
            or os.getenv("OPENROUTER_MODEL")
            or settings.openrouter_model
        )

        self.timeout = float(
            timeout
            if timeout is not None
            else (
                os.getenv("OPENROUTER_TIMEOUT")
                or settings.openrouter_timeout
            )
        )

        self.site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
        self.app_name = os.getenv(
            "OPENROUTER_APP_NAME",
            "Repona",
        ).strip()

    def generate(
        self,
        system_message: str,
        user_message: str,
        max_output_tokens: int,
    ) -> str:

        if (
            not self.api_key
            or not self.model
            or not user_message.strip()
            or max_output_tokens < 1
        ):
            raise LLMProviderError(
                "the model provider is unavailable"
            )

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
            "max_tokens": max_output_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Optional OpenRouter application attribution headers.
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url

        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name

        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
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

        if not isinstance(body, dict):
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        choices = body.get("choices", [])

        if not isinstance(choices, list) or not choices:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        message = choices[0].get("message", {})

        if not isinstance(message, dict):
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        content = message.get("content")

        if isinstance(content, str):
            result = content.strip()

        elif isinstance(content, list):
            result = "\n".join(
                item["text"]
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ).strip()

        else:
            result = ""

        if not result:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        return result
