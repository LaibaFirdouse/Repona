from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import run_cli

Runner = Callable[[list[str], str, float], str]


class OpenCodeProvider(BaseLLMProvider):
    """Ask OpenCode for text only. Plugins are off and the directory is empty."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.model = model or os.getenv("OPENCODE_MODEL") or settings.opencode_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("OPENCODE_TIMEOUT") or settings.opencode_timeout)
        )
        self.executable = executable or os.getenv("OPENCODE_BIN") or "opencode"
        self._runner = runner or run_cli

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        prompt = (
            f"{system_message}\n\n{user_message}\n\n"
            "Reply with one JSON object only. "
            f"Keep the response within {max_output_tokens} tokens. "
            "Do not read files and do not run commands."
        )
        try:
            with tempfile.TemporaryDirectory(prefix="repona-opencode-") as workspace:
                stdout = self._runner(
                    [
                        self.executable,
                        "run",
                        "--pure",
                        "--format",
                        "json",
                        "--dir",
                        workspace,
                        "--model",
                        self.model,
                        prompt,
                    ],
                    workspace,
                    self.timeout,
                )
        except LLMProviderError:
            raise
        except OSError:
            raise LLMProviderError("the model provider is unavailable") from None
        return _text_events(stdout)


def _text_events(stdout: str) -> str:
    parts: list[str] = []
    for line in stdout.splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part")
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str):
            parts.append(text)
    answer = "".join(parts).strip()
    if not answer:
        raise LLMProviderError("the model provider is unavailable")
    return answer
