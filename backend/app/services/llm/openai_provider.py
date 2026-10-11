from __future__ import annotations

import os

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, client: OpenAI | None = None) -> None:
        self.client = client

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        client = self.client
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY") or settings.openai_api_key
            if not api_key:
                raise LLMProviderError("the model provider is unavailable")
            client = OpenAI(api_key=api_key, timeout=settings.openai_timeout)

        model = os.getenv("OPENAI_MODEL") or settings.openai_model
        temperature = float(os.getenv("OPENAI_TEMPERATURE", settings.openai_temperature))

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=max_output_tokens,
                response_format={"type": "json_object"},
            )
        except OpenAIError as error:
            raise LLMProviderError("the model provider is unavailable") from error

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise LLMProviderError("the model provider is unavailable")
        return content
