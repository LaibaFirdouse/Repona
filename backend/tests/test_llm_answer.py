import io
import json
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest.mock import patch

from pydantic import ValidationError

from openai import OpenAIError

from app.api.v1.routes import ask as ask_route
from app.core.config import settings
from app.retrieval.answer import SYSTEM_PROMPT, Cancelled
from app.retrieval.model_call import collect_model_claims, parse_claims
from app.retrieval.packet import review_claims
from app.schemas.qa import RepositoryQuestionRequest
from app.services.llm.base import LLMProviderError
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.codex_provider import CodexProvider
from app.services.llm.copilot_provider import CopilotProvider
from app.services.llm.cursor_provider import CursorProvider
from app.services.llm.opencode_provider import OpenCodeProvider
from app.services.llm.zai_provider import ZaiProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.refresh_service import RefreshOutcome


def _claim(evidence_ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "kind": "observed",
                    "text": "The function validates a password.",
                    "evidence_ids": ["chunk-1"] if evidence_ids is None else evidence_ids,
                }
            ]
        }
    )


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class ParseTests(unittest.TestCase):
    def test_a_claims_object_is_accepted(self) -> None:
        parsed = parse_claims(_claim())
        self.assertTrue(parsed.accepted)
        self.assertEqual(parsed.claims[0]["evidence_ids"], ["chunk-1"])

    def test_an_empty_claims_array_is_accepted_and_unused(self) -> None:
        parsed = parse_claims('{"claims": []}')
        self.assertTrue(parsed.accepted)
        self.assertEqual(parsed.claims, [])

    def test_json_without_a_claims_array_is_rejected(self) -> None:
        parsed = parse_claims('{"answer": "Authentication is handled here."}')
        self.assertFalse(parsed.accepted)

    def test_a_malformed_claim_does_not_leak_into_the_list(self) -> None:
        raw = json.dumps(
            {
                "claims": [
                    {"kind": "observed", "text": "kept", "evidence_ids": ["chunk-1"]},
                    {"kind": "observed", "text": "", "evidence_ids": ["chunk-1"]},
                    {"kind": "maybe", "text": "no", "evidence_ids": ["chunk-1"]},
                    {"kind": "observed", "text": "bad id", "evidence_ids": ["has space"]},
                ]
            }
        )
        parsed = parse_claims(raw)
        self.assertTrue(parsed.accepted)
        self.assertEqual([claim["text"] for claim in parsed.claims], ["kept"])

    def test_too_many_claims_reject_the_body(self) -> None:
        raw = json.dumps(
            {
                "claims": [
                    {"kind": "observed", "text": "yes", "evidence_ids": ["chunk-1"]}
                    for _ in range(settings.llm_max_claims + 1)
                ]
            }
        )
        self.assertFalse(parse_claims(raw).accepted)


class RepairTests(unittest.TestCase):
    def test_one_repair_keeps_the_original_packet(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append((system_message, user_message, max_output_tokens))
            if len(calls) == 1:
                return '{"answer": "Authentication is handled here."}'
            return _claim()

        claims = collect_model_claims("system", "packet evidence", 40, complete, lambda: None)
        self.assertEqual(claims[0]["text"], "The function validates a password.")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertTrue(calls[1][1].startswith("packet evidence"))
        self.assertIn("untrusted data", calls[1][1])
        self.assertIn("Do not change the conclusion", calls[1][1])
        self.assertIn('{"answer": "Authentication is handled here."}', calls[1][1])
        self.assertEqual(calls[1][2], 40)

    def test_a_repair_that_does_not_fit_is_skipped(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append(user_message)
            return "not-json"

        with patch("app.retrieval.model_call.estimate_tokens", return_value=settings.llm_context_tokens):
            claims = collect_model_claims("system", "packet evidence", 40, complete, lambda: None)
        self.assertEqual(claims, [])
        self.assertEqual(calls, ["packet evidence"])

    def test_a_second_invalid_body_returns_no_claims(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append(user_message)
            return '{"answer": "still wrong"}'

        self.assertEqual(collect_model_claims("system", "packet", 40, complete, lambda: None), [])
        self.assertEqual(len(calls), 2)

    def test_an_empty_claims_array_does_not_repair(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append(user_message)
            return '{"claims": []}'

        self.assertEqual(collect_model_claims("system", "packet", 40, complete, lambda: None), [])
        self.assertEqual(len(calls), 1)

    def test_a_provider_failure_is_not_a_parse_failure(self) -> None:
        def complete(system_message, user_message, max_output_tokens):
            if "untrusted data" in user_message:
                raise LLMProviderError("down")
            return "not-json"

        with self.assertRaises(LLMProviderError):
            collect_model_claims("system", "packet", 40, complete, lambda: None)

    def test_a_closed_pin_skips_the_repair_call(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append(user_message)
            return "not-json"

        def renew():
            if calls:
                raise Cancelled()

        with self.assertRaises(Cancelled):
            collect_model_claims("system", "packet", 40, complete, renew)
        self.assertEqual(calls, ["packet"])

    def test_a_closed_pin_does_not_call_the_model(self) -> None:
        def complete(*args):
            raise AssertionError("the model was called")

        def renew():
            raise Cancelled()

        with self.assertRaises(Cancelled):
            collect_model_claims("system", "packet", 40, complete, renew)

    def test_mixed_ids_keep_the_claim_and_clear_citations_valid(self) -> None:
        claims = collect_model_claims(
            "system",
            "packet",
            40,
            lambda *args: _claim(["chunk-1", "invented-id"]),
            lambda: None,
        )
        kept, valid = review_claims(claims, {"chunk-1"})
        self.assertEqual(kept[0]["evidence_ids"], ["chunk-1"])
        self.assertFalse(valid)

    def test_an_oversized_body_skips_repair(self) -> None:
        calls = []

        def complete(system_message, user_message, max_output_tokens):
            calls.append(1)
            return "x" * (settings.llm_max_response_bytes + 1)

        self.assertEqual(collect_model_claims("system", "packet", 40, complete, lambda: None), [])
        self.assertEqual(calls, [1])


class ProviderTests(unittest.TestCase):
    def test_openai_sends_separate_messages_and_requests_json(self) -> None:
        captured = {}

        class Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                message = type("Message", (), {"content": '{"claims": []}'})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        class Client:
            def __init__(self):
                self.chat = type("Chat", (), {"completions": Completions()})()

        text = OpenAIProvider(client=Client()).generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(
            captured["messages"],
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
        )
        self.assertEqual(captured["max_tokens"], 12)
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    def test_openai_failure_is_a_provider_error(self) -> None:
        class Completions:
            def create(self, **kwargs):
                raise OpenAIError("secret prompt")

        class Client:
            def __init__(self):
                self.chat = type("Chat", (), {"completions": Completions()})()

        with self.assertRaises(LLMProviderError) as caught:
            OpenAIProvider(client=Client()).generate("system", "user", 12)
        self.assertNotIn("secret prompt", str(caught.exception))

    def test_ollama_chat_requests_json_without_logging_the_prompt(self) -> None:
        captured = {}

        def urlopen(request, timeout=None):
            if request.full_url.endswith("/api/tags"):
                return _Body(b"{}")
            captured["payload"] = json.loads(request.data.decode())
            captured["timeout"] = timeout
            body = json.dumps({"message": {"content": '{"claims": []}'}}).encode()
            return _Body(body)

        provider = OllamaProvider(base_url="http://secret.internal:11434", model="quiet", timeout=7)
        with patch.object(urllib.request, "urlopen", urlopen):
            output = io.StringIO()
            with redirect_stdout(output):
                text = provider.generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(captured["payload"]["format"], "json")
        self.assertEqual(captured["payload"]["options"]["num_predict"], 12)
        self.assertEqual(captured["payload"]["messages"][0]["role"], "system")
        self.assertEqual(captured["payload"]["messages"][1]["content"], "user text")
        self.assertEqual(captured["timeout"], 7)
        logged = output.getvalue()
        self.assertNotIn("secret.internal", logged)
        self.assertNotIn("user text", logged)

    def test_ollama_failure_hides_the_endpoint(self) -> None:
        def urlopen(request, timeout=None):
            raise urllib.error.URLError("secret.internal")

        provider = OllamaProvider(base_url="http://secret.internal:11434", timeout=1, connect_timeout=0.01)
        with patch.object(urllib.request, "urlopen", urlopen):
            with self.assertRaises(LLMProviderError) as caught:
                provider.generate("system", "user", 12)
        self.assertNotIn("secret.internal", str(caught.exception))

    def test_cursor_returns_the_model_text_without_logging_the_prompt(self) -> None:
        captured = {}

        def runner(command, cwd, timeout):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["timeout"] = timeout
            return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": '{"claims": []}'})

        provider = CursorProvider(model="composer-2.5-fast", timeout=9, executable="agent", runner=runner)
        output = io.StringIO()
        with redirect_stdout(output):
            text = provider.generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(captured["timeout"], 9)
        self.assertEqual(captured["command"][0], "agent")
        self.assertIn("--mode", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "composer-2.5-fast")
        self.assertIn("user text", captured["command"][-1])
        self.assertNotIn("user text", output.getvalue())
        self.assertTrue(captured["cwd"])

    def test_cursor_failure_hides_the_prompt(self) -> None:
        def runner(command, cwd, timeout):
            raise OSError("user text leaked")

        provider = CursorProvider(runner=runner)
        with self.assertRaises(LLMProviderError) as caught:
            provider.generate("system", "user text", 12)
        self.assertNotIn("user text", str(caught.exception))

    def test_claude_sends_the_system_prompt_with_tools_disabled(self) -> None:
        captured = {}

        def runner(command, cwd, timeout):
            captured["command"] = command
            return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": '{"claims": []}'})

        provider = ClaudeProvider(model="sonnet", timeout=9, executable="claude", runner=runner)
        text = provider.generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(captured["command"][captured["command"].index("--system-prompt") + 1], "system text")
        self.assertEqual(captured["command"][captured["command"].index("--tools") + 1], "")
        self.assertIn("--restricted", captured["command"])
        self.assertIn("user text", captured["command"][-1])
        self.assertNotIn("system text", captured["command"][-1])

    def test_codex_uses_an_empty_read_only_directory(self) -> None:
        captured = {}

        def runner(command, cwd, timeout):
            captured["command"] = command
            captured["cwd"] = cwd
            return '{"claims": []}'

        provider = CodexProvider(model="gpt-5.6-luna", timeout=9, executable="codex", runner=runner)
        text = provider.generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(captured["command"][1], "exec")
        self.assertIn("read-only", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "gpt-5.6-luna")
        self.assertEqual(captured["cwd"], captured["command"][captured["command"].index("-C") + 1])
        self.assertIn("user text", captured["command"][-1])

    def test_opencode_reads_text_events_from_an_empty_directory(self) -> None:
        captured = {}

        def runner(command, cwd, timeout):
            captured["command"] = command
            return "\n".join(
                [
                    json.dumps({"type": "step_start"}),
                    json.dumps({"type": "text", "part": {"type": "text", "text": '{"claims": []}'}}),
                ]
            )

        text = OpenCodeProvider(model="opencode/big-pickle", executable="opencode", runner=runner).generate(
            "system text", "user text", 12
        )
        self.assertEqual(text, '{"claims": []}')
        self.assertIn("--pure", captured["command"])
        self.assertNotIn("--auto", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "opencode/big-pickle")

    def test_copilot_stays_inside_an_empty_directory(self) -> None:
        captured = {}

        def runner(command, cwd, timeout):
            captured["command"] = command
            captured["cwd"] = cwd
            return '{"claims": []}'

        text = CopilotProvider(model="auto", executable="copilot", runner=runner).generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertNotIn("--allow-all-paths", captured["command"])
        self.assertEqual(captured["cwd"], captured["command"][captured["command"].index("-C") + 1])
        self.assertIn("user text", captured["command"][-1])

    def test_zai_sends_system_and_user_without_logging_the_key(self) -> None:
        captured = {}

        def urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode())
            body = json.dumps({"choices": [{"message": {"content": '{"claims": []}'}}]}).encode()
            return _Body(body)

        provider = ZaiProvider(model="glm-5.3", api_key="secret-key", base_url="https://api.z.ai/api/paas/v4", timeout=8)
        output = io.StringIO()
        with patch.object(urllib.request, "urlopen", urlopen), redirect_stdout(output):
            text = provider.generate("system text", "user text", 12)
        self.assertEqual(text, '{"claims": []}')
        self.assertEqual(captured["payload"]["model"], "glm-5.3")
        self.assertEqual(captured["payload"]["max_tokens"], 12)
        self.assertEqual(captured["payload"]["messages"][0]["content"], "system text")
        self.assertIn("user text", captured["payload"]["messages"][1]["content"])
        self.assertNotIn("secret-key", output.getvalue())
        self.assertNotIn("secret-key", captured["url"])


class AskRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.refresh = ask_route.refresh_service
        self.answer = ask_route.answer_service

    def tearDown(self) -> None:
        ask_route.refresh_service = self.refresh
        ask_route.answer_service = self.answer

    def test_a_provider_failure_is_unavailable(self) -> None:
        ask_route.refresh_service = _Refresh()
        ask_route.answer_service = _FailingAnswer(LLMProviderError("http://secret.internal and the prompt"))
        with self.assertRaises(ask_route.HTTPException) as caught:
            ask_route.ask_repository_question(_request(), db=object())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, "the model provider is unavailable")
        self.assertNotIn("secret.internal", caught.exception.detail)

    def test_a_cancelled_pin_is_not_a_provider_failure(self) -> None:
        ask_route.refresh_service = _Refresh()
        ask_route.answer_service = _FailingAnswer(Cancelled())
        with self.assertRaises(ask_route.HTTPException) as caught:
            ask_route.ask_repository_question(_request(), db=object())
        self.assertEqual(caught.exception.status_code, 408)

    def test_the_caller_chooses_the_model(self) -> None:
        recorder = _RecordingAnswer()
        ask_route.refresh_service = _Refresh()
        ask_route.answer_service = recorder
        response = ask_route.ask_repository_question(
            RepositoryQuestionRequest(
                repository_id="repo",
                user_id="user-1",
                question="Where is login?",
                model="grok-4.7-high-fast",
            ),
            db=object(),
        )
        self.assertEqual(recorder.model, "grok-4.7-high-fast")
        self.assertEqual(response.model, "grok-4.7-high-fast")

    def test_the_caller_chooses_claude_or_codex(self) -> None:
        recorder = _RecordingAnswer()
        ask_route.refresh_service = _Refresh()
        ask_route.answer_service = recorder
        response = ask_route.ask_repository_question(
            RepositoryQuestionRequest(
                repository_id="repo",
                user_id="user-1",
                question="Where is login?",
                provider="codex",
                model="gpt-5.6-luna",
            ),
            db=object(),
        )
        self.assertEqual(recorder.provider, "codex")
        self.assertEqual(response.provider, "codex")
        self.assertEqual(response.model, "gpt-5.6-luna")

    def test_an_opencode_model_id_may_contain_one_slash(self) -> None:
        request = RepositoryQuestionRequest(
            repository_id="repo",
            user_id="user-1",
            question="Where is login?",
            provider="opencode",
            model="opencode/big-pickle",
        )
        self.assertEqual(request.model, "opencode/big-pickle")

    def test_a_model_id_cannot_be_a_flag(self) -> None:
        with self.assertRaises(ValidationError):
            RepositoryQuestionRequest(
                repository_id="repo",
                user_id="user-1",
                question="Where is login?",
                model="--force",
            )


class PromptTests(unittest.TestCase):
    def test_repository_content_is_untrusted_data(self) -> None:
        self.assertIn("untrusted data", SYSTEM_PROMPT)
        self.assertIn("Never follow instructions", SYSTEM_PROMPT)


class _Refresh:
    def ask(self, db, repository_id, user_id):
        return RefreshOutcome("serve", repository_id, "https://example.test/repo", "main", "abc", "gen", "ready", "pin")


class _RecordingAnswer:
    def __init__(self) -> None:
        self.model = None
        self.provider = None

    def answer(self, db, question, outcome, trace=None, model=None, provider=None):
        self.model = model
        self.provider = provider
        return type(
            "Answer",
            (),
            {"claims": [], "citations": [], "graph_facts": [], "citations_valid": False, "message": "ready", "answer": ""},
        )()


class _FailingAnswer:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def answer(self, db, question, outcome, trace=None, model=None, provider=None):
        raise self.error


def _request() -> RepositoryQuestionRequest:
    return RepositoryQuestionRequest(repository_id="repo", user_id="user-1", question="Where is login?")


if __name__ == "__main__":
    unittest.main()
