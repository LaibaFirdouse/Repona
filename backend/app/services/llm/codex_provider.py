from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import run_cli

Runner = Callable[[list[str], str, float], str]


class CodexProvider(BaseLLMProvider):
    """Ask Codex for text only. The working directory is empty and read-only."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float | None = None,
        executable: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.model = model or os.getenv("CODEX_MODEL") or settings.codex_model
        self.timeout = float(
            timeout
            if timeout is not None
            else (os.getenv("CODEX_TIMEOUT") or settings.codex_timeout)
        )
        self.executable = executable or os.getenv("CODEX_BIN") or "codex"
        self._runner = runner or _run_codex

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
            with tempfile.TemporaryDirectory(prefix="repona-codex-") as workspace:
                text = self._runner(
                    [
                        self.executable,
                        "exec",
                        "--ephemeral",
                        "--skip-git-repo-check",
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--sandbox",
                        "read-only",
                        "--color",
                        "never",
                        "-C",
                        workspace,
                        "--model",
                        self.model,
                        "--output-last-message",
                        str(Path(workspace) / "last.txt"),
                        prompt,
                    ],
                    workspace,
                    self.timeout,
                )
        except LLMProviderError:
            raise
        except OSError:
            raise LLMProviderError("the model provider is unavailable") from None
        if not text.strip():
            raise LLMProviderError("the model provider is unavailable")
        return text


def _run_codex(command: list[str], cwd: str, timeout: float) -> str:
    run_cli(command, cwd, timeout)
    last_message = Path(cwd) / "last.txt"
    try:
        text = last_message.read_text(encoding="utf-8")
    except OSError:
        raise LLMProviderError("the model provider is unavailable") from None
    return text
