from __future__ import annotations

import json
import subprocess

from app.services.llm.base import LLMProviderError


def run_cli(command: list[str], cwd: str, timeout: float) -> str:
    """Run a local model CLI. The prompt stays in the command and out of errors."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LLMProviderError("the model provider is unavailable") from None
    if completed.returncode != 0:
        raise LLMProviderError("the model provider is unavailable")
    return completed.stdout


def json_result_text(stdout: str) -> str:
    raw = stdout.strip()
    start = raw.rfind("\n{")
    if start >= 0:
        raw = raw[start + 1 :]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise LLMProviderError("the model provider is unavailable") from None
    if not isinstance(payload, dict) or payload.get("is_error") or payload.get("subtype") == "error":
        raise LLMProviderError("the model provider is unavailable")
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise LLMProviderError("the model provider is unavailable")
    return result
