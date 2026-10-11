import os
import unittest
from unittest.mock import patch

from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.codex_provider import CodexProvider
from app.services.llm.copilot_provider import CopilotProvider
from app.services.llm.cursor_provider import CursorProvider
from app.services.llm.opencode_provider import OpenCodeProvider
from app.services.llm.zai_provider import ZaiProvider
from app.services.llm.llm_factory import LLMFactory
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider


class LLMFactoryTests(unittest.TestCase):
    def test_factory_uses_cursor_provider_when_configured(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "cursor", "CURSOR_MODEL": "composer-2.5-fast"}, clear=False):
            provider = LLMFactory.create_provider()

            self.assertIsInstance(provider, CursorProvider)
            self.assertEqual(provider.model, "composer-2.5-fast")

    def test_factory_uses_the_callers_model(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "cursor", "CURSOR_MODEL": "composer-2.5-fast"}, clear=False):
            provider = LLMFactory.create_provider("grok-4.7-high-fast")

            self.assertEqual(provider.model, "grok-4.7-high-fast")

    def test_factory_uses_claude_and_codex(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "claude", "CLAUDE_MODEL": "sonnet"}, clear=False):
            claude = LLMFactory.create_provider()
            self.assertIsInstance(claude, ClaudeProvider)
            self.assertEqual(claude.model, "sonnet")
        with patch.dict(os.environ, {"LLM_PROVIDER": "codex", "CODEX_MODEL": "gpt-5.6-luna"}, clear=False):
            codex = LLMFactory.create_provider("gpt-5.5")
            self.assertIsInstance(codex, CodexProvider)
            self.assertEqual(codex.model, "gpt-5.5")

    def test_factory_uses_opencode_zai_and_copilot(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "opencode", "OPENCODE_MODEL": "opencode/big-pickle"}, clear=False):
            self.assertIsInstance(LLMFactory.create_provider(), OpenCodeProvider)
        with patch.dict(os.environ, {"LLM_PROVIDER": "zai", "ZAI_MODEL": "glm-5.3", "ZAI_API_KEY": "secret"}, clear=False):
            zai = LLMFactory.create_provider()
            self.assertIsInstance(zai, ZaiProvider)
            self.assertEqual(zai.model, "glm-5.3")
        with patch.dict(os.environ, {"LLM_PROVIDER": "copilot", "COPILOT_MODEL": "auto"}, clear=False):
            copilot = LLMFactory.create_provider("gpt-5")
            self.assertIsInstance(copilot, CopilotProvider)
            self.assertEqual(copilot.model, "gpt-5")

    def test_factory_uses_ollama_provider_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://example.test",
                "OLLAMA_MODEL": "qwen2.5:1.5b",
            },
            clear=False,
        ):
            provider = LLMFactory.create_provider()

            self.assertIsInstance(provider, OllamaProvider)
            self.assertEqual(provider.base_url, "http://example.test")
            self.assertEqual(provider.model, "qwen2.5:1.5b")

    def test_factory_uses_openai_provider_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        ):
            provider = LLMFactory.create_provider()

            self.assertIsInstance(provider, OpenAIProvider)


if __name__ == "__main__":
    unittest.main()
