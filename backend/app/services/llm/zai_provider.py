from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class ZaiProvider(BaseLLMProvider):
    """Ask Z.ai through its chat completions API. The caller supplies the API key."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.model = model or os.getenv("ZAI_MODEL") or settings.zai_model
        self.api_key = api_key if api_key is not None else (os.getenv("ZAI_API_KEY") or "")
        self.base_url = (base_url or os.getenv("ZAI_BASE_URL") or settings.zai_base_url).rstrip("/")
        self.timeout = float(
            timeout if timeout is not None else (os.getenv("ZAI_TIMEOUT") or settings.zai_timeout)
        )

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not self.api_key.strip() or not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        prompt = (
            f"{user_message}\n\n"
            "Reply with one JSON object only. "
            f"Keep the response within {max_output_tokens} tokens."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": max_output_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LLMProviderError("the model provider is unavailable") from None
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise LLMProviderError("the model provider is unavailable") from None
        choices = body.get("choices") if isinstance(body, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("the model provider is unavailable")
        return content
