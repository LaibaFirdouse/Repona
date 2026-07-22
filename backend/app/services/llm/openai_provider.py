from __future__ import annotations

import os

from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, client: OpenAI | None = None) -> None:
        self.client = client

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise LLMProviderError("Prompt cannot be empty.")

        api_key = os.getenv("OPENAI_API_KEY") or settings.openai_api_key
        if not api_key:
            raise LLMProviderError("OPENAI_API_KEY is not configured.")

        model = os.getenv("OPENAI_MODEL") or settings.openai_model
        temperature = float(os.getenv("OPENAI_TEMPERATURE", settings.openai_temperature))
        max_tokens = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", settings.openai_max_output_tokens))

        client = self.client or OpenAI(api_key=api_key)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except OpenAIError as error:
            raise LLMProviderError("OpenAI generation failed.") from error

        content = response.choices[0].message.content
        if not content:
            raise LLMProviderError("OpenAI returned an empty response.")

        return content
