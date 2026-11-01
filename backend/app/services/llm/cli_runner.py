from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess

from app.services.llm.base import LLMProviderError

logger = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 500
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"
    r"|bearer\s+\S+"
    r"|(?:sk|crsr)[-_][A-Za-z0-9][A-Za-z0-9_\-]{7,}"
)


def run_cli(command: list[str], cwd: str, timeout: float, env: dict[str, str] | None = None) -> str:
    """Run a local model CLI. The prompt stays in the command and out of errors and logs.

    Callers still get a generic "unavailable" error, but the reason is logged: program name, exit
    code or timeout, and a redacted tail of stderr. Before, an expired login, an unknown flag and
    a timeout were indistinguishable. On timeout the whole process group is killed, so helper
    processes the CLI started (node, shells) do not outlive the request.
    """
    program = os.path.basename(command[0]) if command else "?"
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except OSError as error:
        logger.warning("model_cli_failed program=%s reason=start_error error=%s", program, type(error).__name__)
        raise LLMProviderError("the model provider is unavailable") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        process.communicate()
        logger.warning("model_cli_failed program=%s reason=timeout timeout_seconds=%s", program, timeout)
        raise LLMProviderError("the model provider is unavailable") from None
    if process.returncode != 0:
        logger.warning(
            "model_cli_failed program=%s reason=exit_code exit_code=%s stderr=%s",
            program,
            process.returncode,
            _redacted_tail(stderr),
        )
        raise LLMProviderError("the model provider is unavailable")
    return stdout


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        process.kill()


def _redacted_tail(text: str | None) -> str:
    tail = (text or "").strip()[-_STDERR_TAIL_CHARS:]
    return _SECRET.sub("[redacted]", tail).replace("\n", " | ")


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
