import os
import unittest
from unittest.mock import patch

from app.services.llm.llm_factory import LLMFactory
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider


class LLMFactoryTests(unittest.TestCase):
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
