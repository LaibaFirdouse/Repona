from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.services.llm.base import BaseLLMProvider, LLMProviderError


class GrokProvider(BaseLLMProvider):
    """Generate text using the xAI Grok API."""

    API_URL = "https://api.x.ai/v1/responses"

    def __init__(
        self,
        model: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.api_key = os.getenv("XAI_API_KEY", "").strip()
        self.model = model or os.getenv("GROK_MODEL", "grok-4.7")
        self.timeout = timeout

    def generate(
        self,
        system_message: str,
        user_message: str,
        max_output_tokens: int,
    ) -> str:

        if not self.api_key:
            raise LLMProviderError("the model provider is unavailable")

        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": system_message,
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
            "max_output_tokens": max_output_tokens,
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
                body = json.loads(response.read().decode("utf-8"))

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

        # Extract generated text from the Responses API output.
        output = body.get("output", [])

        result = []

        for item in output:
            if item.get("type") != "message":
                continue

            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    result.append(content.get("text", ""))

        text = "\n".join(result).strip()

        if not text:
            raise LLMProviderError(
                "the model provider is unavailable"
            )

        return text
