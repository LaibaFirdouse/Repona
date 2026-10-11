from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError


def _is_running_in_docker() -> bool:
    """Return True when the process runs inside a container."""
    if Path("/.dockerenv").exists():
        return True
    cgroup = Path("/proc/1/cgroup")
    if cgroup.exists():
        try:
            return "docker" in cgroup.read_text(errors="ignore")
        except OSError:
            return False
    return False


class OllamaProvider(BaseLLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        connect_timeout: float = 3.0,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or settings.ollama_base_url
        ).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL") or settings.ollama_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("OLLAMA_TIMEOUT") or settings.ollama_timeout)
        )
        self.connect_timeout = connect_timeout

    def _candidate_base_urls(self) -> list[str]:
        configured = self.base_url.rstrip("/")
        candidates = [configured]
        if _is_running_in_docker():
            fallbacks = [
                "http://host.docker.internal:11434",
                "http://172.17.0.1:11434",
                "http://localhost:11434",
                "http://127.0.0.1:11434",
            ]
        else:
            fallbacks = [
                "http://localhost:11434",
                "http://127.0.0.1:11434",
                "http://172.17.0.1:11434",
            ]
        for fallback in fallbacks:
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _find_reachable_base_url(self) -> str | None:
        """Return the first candidate that answers within connect_timeout."""
        for base_url in self._candidate_base_urls():
            request = urllib.request.Request(
                f"{base_url}/api/tags",
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.connect_timeout) as response:
                    response.read()
            except Exception:
                continue
            return base_url
        return None

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        reachable_base_url = self._find_reachable_base_url()
        if reachable_base_url is None:
            raise LLMProviderError("the model provider is unavailable")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_output_tokens},
        }
        request = urllib.request.Request(
            f"{reachable_base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_response = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LLMProviderError("the model provider is unavailable") from error

        try:
            payload_response = json.loads(raw_response)
        except json.JSONDecodeError as error:
            raise LLMProviderError("the model provider is unavailable") from error

        message = payload_response.get("message") if isinstance(payload_response, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("the model provider is unavailable")
        return content
