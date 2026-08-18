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
        # Short timeout used only to probe whether a candidate URL is reachable,
        # so dead endpoints (e.g. host.docker.internal on a Linux host) fail fast
        # instead of blocking for the full generation timeout.
        self.connect_timeout = connect_timeout

    def _candidate_base_urls(self) -> list[str]:
        configured = self.base_url.rstrip("/")
        candidates = [configured]
        if _is_running_in_docker():
            # Inside a container host.docker.internal resolves to the host via
            # the host-gateway; loopback addresses only reach the container.
            fallbacks = [
                "http://host.docker.internal:11434",
                "http://172.17.0.1:11434",
                "http://localhost:11434",
                "http://127.0.0.1:11434",
            ]
        else:
            # On a native Linux host host.docker.internal does not resolve and
            # the default bridge gateway is usually not where Ollama listens;
            # prefer loopback addresses so unreachable URLs fail fast.
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
            print(f"Trying URL: {base_url}")
            request = urllib.request.Request(
                f"{base_url}/api/tags",
                headers={"Content-Type": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.connect_timeout
                ) as response:
                    response.read()
            except Exception:
                continue
            return base_url
        return None

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise LLMProviderError("Prompt cannot be empty.")
        print(f"Model = {self.model}")
        print(f"Base URL = {self.base_url}")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "stop": [
                    "\n\nHuman:",
                    "\n\nUser:",
                ]
            },
        }
        request_data = json.dumps(payload).encode("utf-8")

        reachable_base_url = self._find_reachable_base_url()
        if reachable_base_url is None:
            raise LLMProviderError(
                "Could not reach Ollama at any candidate URL: "
                + ", ".join(self._candidate_base_urls())
            )
        print(f"Using reachable URL: {reachable_base_url}")

        request = urllib.request.Request(
            f"{reachable_base_url}/api/generate",
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw_response = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise LLMProviderError(
                f"Ollama request to {reachable_base_url} failed after "
                f"{self.timeout}s: {error}"
            ) from error

        try:
            payload_response = json.loads(raw_response)
        except json.JSONDecodeError as error:
            raise LLMProviderError("Ollama returned invalid JSON.") from error

        content = payload_response.get("response", "")
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("Ollama returned an empty response.")

        return content
