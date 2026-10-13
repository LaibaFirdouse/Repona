"""Host-side Cursor bridge.

The API sends the question and evidence packet. This process, running as the
already logged-in user, invokes the Cursor CLI. The CLI home stays on the host.
The CLI is started with --mode ask and --sandbox enabled. --force and --yolo
are not used. A smoke test returned from that sandbox; that does not prove
every filesystem or command action is blocked.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import settings
from app.services.llm.base import LLMProviderError
from app.services.llm.cli_runner import json_result_text, run_cli

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
REQUEST_FIELDS = frozenset({"system_message", "user_message", "max_output_tokens", "model"})
MAX_BODY_BYTES = 2_000_000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class BridgeRejected(Exception):
    """The request is not a question packet."""


def loopback_host(host: str) -> str:
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("the bridge listens on the local machine only")
    return host


def bridge_endpoint(url: str) -> str:
    """Accept only an HTTP loopback base URL. The packet never goes anywhere else."""
    from urllib.parse import urlparse

    parsed = urlparse(url.strip())
    host = parsed.hostname
    if (
        parsed.scheme != "http"
        or host not in LOOPBACK_HOSTS
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise LLMProviderError("the model provider is unavailable")
    netloc = f"[{host}]:{parsed.port}" if host == "::1" else f"{host}:{parsed.port}"
    return f"http://{netloc}/v1/generate"


def completion_prompt(system_message: str, user_message: str, max_output_tokens: int) -> str:
    return (
        f"{system_message}\n\n{user_message}\n\n"
        "Reply with one JSON object only. "
        f"Keep the response within {max_output_tokens} tokens."
    )


def ask_command(executable: str, model: str, workspace: str, prompt: str) -> list[str]:
    return [
        executable,
        "--print",
        "--mode",
        "ask",
        "--sandbox",
        "enabled",
        "--trust",
        "--workspace",
        workspace,
        "--model",
        model,
        "--output-format",
        "json",
        prompt,
    ]


def bridge_environment() -> dict[str, str]:
    """Give the CLI the host login environment, without the bridge credential."""
    env = os.environ.copy()
    env.pop("CURSOR_BRIDGE_TOKEN", None)
    env.pop("CURSOR_BRIDGE_URL", None)
    return env


def run_ask(command: list[str], cwd: str, timeout: float) -> str:
    return run_cli(command, cwd, timeout, env=bridge_environment())


def parse_packet(body: object, default_model: str) -> tuple[str, str, int, str]:
    if not isinstance(body, dict) or set(body) - REQUEST_FIELDS:
        raise BridgeRejected()
    system_message = body.get("system_message")
    user_message = body.get("user_message")
    max_output_tokens = body.get("max_output_tokens")
    model = body.get("model", default_model)
    if not isinstance(system_message, str) or not isinstance(user_message, str) or not user_message.strip():
        raise BridgeRejected()
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or not 1 <= max_output_tokens <= 8000
    ):
        raise BridgeRejected()
    if len(system_message.encode("utf-8")) + len(user_message.encode("utf-8")) > MAX_BODY_BYTES:
        raise BridgeRejected()
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        raise BridgeRejected()
    return system_message, user_message, max_output_tokens, model


def invoke_ask(
    executable: str,
    model: str,
    timeout: float,
    system_message: str,
    user_message: str,
    max_output_tokens: int,
    runner=run_ask,
) -> str:
    prompt = completion_prompt(system_message, user_message, max_output_tokens)
    with tempfile.TemporaryDirectory(prefix="repona-cursor-") as workspace:
        stdout = runner(ask_command(executable, model, workspace, prompt), workspace, timeout)
    return json_result_text(stdout)


class BridgeServer(HTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        token: str,
        executable: str,
        model: str,
        timeout: float,
        runner=run_ask,
    ) -> None:
        if len(token) < 16:
            raise ValueError("the bridge credential is missing")
        loopback_host(address[0])
        self.token = token
        self.executable = executable
        self.model = model
        self.cli_timeout = timeout
        self.runner = runner
        super().__init__(address, BridgeHandler)


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/v1/generate":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"error": "rejected"})
            return
        if length < 1 or length > MAX_BODY_BYTES:
            self._send(400, {"error": "rejected"})
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            self._send(400, {"error": "rejected"})
            return
        try:
            packet = parse_packet(body, self.server.model)
            result = invoke_ask(
                self.server.executable,
                packet[3],
                self.server.cli_timeout,
                packet[0],
                packet[1],
                packet[2],
                self.server.runner,
            )
        except BridgeRejected:
            self._send(400, {"error": "rejected"})
            return
        except (LLMProviderError, OSError):
            self._send(503, {"error": "the model provider is unavailable"})
            return
        self._send(200, {"result": result})

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return hmac.compare_digest(header, expected)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = loopback_host(os.getenv("CURSOR_BRIDGE_HOST", "127.0.0.1"))
    port = int(os.getenv("CURSOR_BRIDGE_PORT", "8765"))
    token = os.getenv("CURSOR_BRIDGE_TOKEN", "")
    executable = os.getenv("CURSOR_AGENT_BIN") or "agent"
    model = os.getenv("CURSOR_MODEL") or settings.cursor_model
    timeout = float(os.getenv("CURSOR_TIMEOUT") or settings.cursor_timeout)
    server = BridgeServer((host, port), token, executable, model, timeout)
    server.serve_forever()


if __name__ == "__main__":
    main()
