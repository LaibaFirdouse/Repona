from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable

from app.core.config import settings
from app.services.llm.base import BaseLLMProvider, LLMProviderError
from app.services.llm.cli_runner import json_result_text, run_cli
from app.services.llm.cursor_bridge import ask_command, bridge_endpoint, completion_prompt, cursor_cli_environment

Runner = Callable[[list[str], str, float], str]


def _default_cli_runner(command: list[str], cwd: str, timeout: float) -> str:
    return run_cli(command, cwd, timeout, env=cursor_cli_environment())


def _cursor_cli_ready() -> bool:
    if os.getenv("CURSOR_API_KEY", "").strip():
        return True
    if settings.app_env != "production":
        return True
    return False


class CursorProvider(BaseLLMProvider):
    """Ask a Cursor model for text.

    When CURSOR_BRIDGE_URL is set, the packet goes to the host bridge. Otherwise
    the CLI runs in this process using CURSOR_API_KEY or a local agent login,
    in ask mode with --sandbox enabled, inside an empty temporary directory.
    --force and --yolo are not used. The sandbox smoke test does not prove
    that every filesystem or command action is blocked.
    """

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
        self._runner = runner or _default_cli_runner

    def generate(self, system_message: str, user_message: str, max_output_tokens: int) -> str:
        if not user_message.strip() or max_output_tokens < 1:
            raise LLMProviderError("the model provider is unavailable")
        bridge = os.getenv("CURSOR_BRIDGE_URL", "").strip()
        if bridge:
            return self._from_bridge(bridge, system_message, user_message, max_output_tokens)
        if not _cursor_cli_ready():
            raise LLMProviderError("the model provider is unavailable")

        prompt = completion_prompt(system_message, user_message, max_output_tokens)
        try:
            with tempfile.TemporaryDirectory(prefix="repona-cursor-") as workspace:
                stdout = self._runner(
                    ask_command(self.executable, self.model, workspace, prompt),
                    workspace,
                    self.timeout,
                )
        except LLMProviderError:
            raise
        except OSError:
            raise LLMProviderError("the model provider is unavailable") from None

        return json_result_text(stdout)

    def _from_bridge(self, bridge: str, system_message: str, user_message: str, max_output_tokens: int) -> str:
        token = os.getenv("CURSOR_BRIDGE_TOKEN", "")
        if len(token) < 16:
            raise LLMProviderError("the model provider is unavailable")
        endpoint = bridge_endpoint(bridge)
        payload = json.dumps(
            {
                "system_message": system_message,
                "user_message": user_message,
                "max_output_tokens": max_output_tokens,
                "model": self.model,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout + 30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, UnicodeError, TimeoutError):
            raise LLMProviderError("the model provider is unavailable") from None
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, str) or not result.strip():
            raise LLMProviderError("the model provider is unavailable")
        return result
