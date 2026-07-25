from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


class OllamaProvider(BaseLLMProvider):
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or settings.ollama_base_url).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or settings.ollama_model

    def _candidate_base_urls(self) -> list[str]:
        configured = self.base_url.rstrip("/")
        candidates = [configured]
        for fallback in [
            "http://host.docker.internal:11434",
            "http://172.17.0.1:11434",
            "http://172.18.0.1:11434",
            "http://localhost:11434",
            "http://127.0.0.1:11434",
        ]:
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise LLMProviderError("Prompt cannot be empty.")
        print(f"Model = {self.model}")
        print(f"Base URL = {self.base_url}")

        # payload = {
        #     "model": self.model,
        #     "prompt": prompt,
        #     "stream": False,
        # }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "stop": [
                    "```",
                    "\n\nHuman:",
                    "\n\nUser:"
                ]
            }
        }
        request_data = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None

        # for base_url in self._candidate_base_urls():
        #     request = urllib.request.Request(
        #         f"{base_url}/api/generate",
        #         data=request_data,
        #         headers={"Content-Type": "application/json"},
        #         method="POST",
        #     )
        #     try:
        #         with urllib.request.urlopen(request, timeout=120) as response:
        #             raw_response = response.read().decode("utf-8")
        #     except (urllib.error.URLError, TimeoutError) as error:
        #         last_error = error
        #         continue
        for base_url in self._candidate_base_urls():
          print(f"Trying URL: {base_url}")
 
          request = urllib.request.Request(
           f"{base_url}/api/generate",
           data=request_data,
           headers={"Content-Type": "application/json"},
           method="POST",
          )

          try:
            with urllib.request.urlopen(request, timeout=600) as response:
             raw_response = response.read().decode("utf-8")
        #   except (urllib.error.URLError, TimeoutError) as error:
        #      print(f"FAILED {base_url}: {error}")
        #      last_error = error
        #      continue
          except Exception as error:
                import traceback

                print("=" * 50)
                print(type(error))
                print(repr(error))
                traceback.print_exc()
                print("=" * 50)

                last_error = error
                continue

          try:
                payload_response = json.loads(raw_response)
          except json.JSONDecodeError as error:
                raise LLMProviderError("Ollama returned invalid JSON.") from error

          content = payload_response.get("response", "")
          if not isinstance(content, str) or not content.strip():
                  raise LLMProviderError("Ollama returned an empty response.")

          return content

        if last_error is not None:
            raise LLMProviderError(f"Ollama request failed: {last_error}") from last_error
        raise LLMProviderError("Ollama request failed.")
