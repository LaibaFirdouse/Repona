"""Model CLI runner diagnostics and the multi-threaded Cursor bridge."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.services.llm.base import LLMProviderError
from app.services.llm.cli_runner import run_cli
from app.services.llm.cursor_bridge import BridgeServer


class RunCliTests(unittest.TestCase):
    def test_stdout_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            out = run_cli([sys.executable, "-c", "print('hello')"], cwd, timeout=30)
        self.assertEqual(out.strip(), "hello")

    def test_failure_logs_exit_code_and_redacted_stderr_but_not_the_prompt(self) -> None:
        prompt = "SECRET PROMPT TEXT"
        script = "import sys; sys.stderr.write('login expired token=abc123def\\n'); sys.exit(3)"
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertLogs("app.services.llm.cli_runner", level="WARNING") as logs:
                with self.assertRaises(LLMProviderError) as raised:
                    run_cli([sys.executable, "-c", script, prompt], cwd, timeout=30)
        line = logs.output[0]
        self.assertIn("exit_code=3", line)
        self.assertIn("login expired", line)
        self.assertIn("[redacted]", line)
        self.assertNotIn("abc123def", line)
        self.assertNotIn(prompt, line)
        self.assertEqual(str(raised.exception), "the model provider is unavailable")

    def test_timeout_is_logged_and_the_process_does_not_outlive_it(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            started = time.monotonic()
            with self.assertLogs("app.services.llm.cli_runner", level="WARNING") as logs:
                with self.assertRaises(LLMProviderError):
                    run_cli([sys.executable, "-c", "import time; time.sleep(30)"], cwd, timeout=0.5)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10)
        self.assertIn("reason=timeout", logs.output[0])

    def test_a_missing_program_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertLogs("app.services.llm.cli_runner", level="WARNING") as logs:
                with self.assertRaises(LLMProviderError):
                    run_cli([os.path.join(cwd, "no-such-cli")], cwd, timeout=5)
        self.assertIn("reason=start_error", logs.output[0])


class BridgeConcurrencyTests(unittest.TestCase):
    def test_bridge_is_a_threading_server(self) -> None:
        self.assertTrue(issubclass(BridgeServer, ThreadingHTTPServer))

    def test_two_questions_run_at_the_same_time(self) -> None:
        # Both CLI runs must be in flight together to pass the barrier; a single-threaded bridge
        # would run them one after the other and the barrier would time out.
        barrier = threading.Barrier(2, timeout=5)

        def runner(command, cwd, timeout):
            barrier.wait()
            return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})

        server = BridgeServer(("127.0.0.1", 0), "bridge-token-value", "agent", "composer-2.5-fast", 9, runner)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        results: list[int] = []

        def ask() -> None:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/generate",
                data=json.dumps({"system_message": "s", "user_message": "u", "max_output_tokens": 8}).encode(),
                method="POST",
                headers={"Authorization": "Bearer bridge-token-value", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    results.append(response.status)
            except urllib.error.HTTPError as error:
                results.append(error.code)

        clients = [threading.Thread(target=ask) for _ in range(2)]
        try:
            for client in clients:
                client.start()
            for client in clients:
                client.join(timeout=15)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(sorted(results), [200, 200])

    def test_concurrent_cli_runs_are_capped(self) -> None:
        server = BridgeServer(
            ("127.0.0.1", 0), "bridge-token-value", "agent", "composer-2.5-fast", 9, max_concurrent=1
        )
        try:
            self.assertTrue(server.cli_slots.acquire(blocking=False))
            self.assertFalse(server.cli_slots.acquire(blocking=False))
            server.cli_slots.release()
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
