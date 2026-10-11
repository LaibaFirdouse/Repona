from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import run_cli

Runner = Callable[[list[str], str, float], str]


class CopilotProvider(BaseLLMProvider):
    """Ask GitHub Copilot for text only. The working directory is empty."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.model = model or os.getenv("COPILOT_MODEL") or settings.copilot_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("COPILOT_TIMEOUT") or settings.copilot_timeout)
        )
        self.executable = executable or os.getenv("COPILOT_BIN") or "copilot"
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
            with tempfile.TemporaryDirectory(prefix="repona-copilot-") as workspace:
                stdout = self._runner(
                    [
                        self.executable,
                        "--silent",
                        "--output-format",
                        "text",
                        "--allow-all-tools",
                        "--model",
                        self.model,
                        "-C",
                        workspace,
                        "--prompt",
                        prompt,
                    ],
                    workspace,
                    self.timeout,
                )
        except LLMProviderError:
            raise
        except OSError:
            raise LLMProviderError("the model provider is unavailable") from None
        if not stdout.strip():
            raise LLMProviderError("the model provider is unavailable")
        return stdout.strip()
