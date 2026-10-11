from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import json_result_text, run_cli

Runner = Callable[[list[str], str, float], str]


class ClaudeProvider(BaseLLMProvider):
    """Ask Claude Code for text only. Tools are off and the workspace is empty."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.model = model or os.getenv("CLAUDE_MODEL") or settings.claude_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("CLAUDE_TIMEOUT") or settings.claude_timeout)
        )
        self.executable = executable or os.getenv("CLAUDE_BIN") or "claude"
        self._runner = runner or run_cli

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        prompt = (
            f"{user_message}\n\n"
            "Reply with one JSON object only. "
            f"Keep the response within {max_output_tokens} tokens."
        )
        try:
            with tempfile.TemporaryDirectory(prefix="repona-claude-") as workspace:
                stdout = self._runner(
                    [
                        self.executable,
                        "--print",
                        "--output-format",
                        "json",
                        "--no-session-persistence",
                        "--permission-prompts",
                        "none",
                        "--tools",
                        "",
                        "--restricted",
                        "--system-prompt",
                        system_message,
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
        return json_result_text(stdout)
