from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import json_result_text, run_cli

Runner = Callable[[list[str], str, float], str]


class CursorProvider(BaseLLMProvider):
    """Ask a Cursor model for text only. The call has no workspace and no tools."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.model = model or os.getenv("CURSOR_MODEL") or settings.cursor_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("CURSOR_TIMEOUT") or settings.cursor_timeout)
        )
        self.executable = executable or os.getenv("CURSOR_AGENT_BIN") or "agent"
        self._runner = runner or run_cli

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")

        prompt = (
            f"{system_message}\n\n{user_message}\n\n"
            "Reply with one JSON object only. "
            f"Keep the response within {max_output_tokens} tokens."
        )
        try:
            with tempfile.TemporaryDirectory(prefix="repona-cursor-") as workspace:
                stdout = self._runner(
                    [
                        self.executable,
                        "--print",
                        "--mode",
                        "ask",
                        "--trust",
                        "--workspace",
                        workspace,
                        "--model",
                        self.model,
                        "--output-format",
                        "json",
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
