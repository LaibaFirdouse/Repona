import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.llm.base import LLMProviderError
from app.services.llm.cursor_provider import CursorProvider, _cursor_cli_ready


class CursorCliReadyTests(unittest.TestCase):
    def test_production_requires_an_api_key_without_bridge(self) -> None:
        settings = SimpleNamespace(app_env="production")
        with patch.dict(os.environ, {"CURSOR_API_KEY": ""}, clear=False), patch(
            "app.services.llm.cursor_provider.settings", settings
        ):
            self.assertFalse(_cursor_cli_ready())

    def test_production_accepts_an_api_key(self) -> None:
        settings = SimpleNamespace(app_env="production")
        with patch.dict(os.environ, {"CURSOR_API_KEY": "crsr_" + "a" * 32}, clear=False), patch(
            "app.services.llm.cursor_provider.settings", settings
        ):
            self.assertTrue(_cursor_cli_ready())


class CursorProviderGenerateTests(unittest.TestCase):
    def test_production_without_key_fails_before_cli(self) -> None:
        provider = CursorProvider(runner=lambda *_args: "should not run")
        settings = SimpleNamespace(app_env="production")
        with patch.dict(os.environ, {"CURSOR_BRIDGE_URL": "", "CURSOR_API_KEY": ""}, clear=False), patch(
            "app.services.llm.cursor_provider.settings", settings
        ):
            with self.assertRaises(LLMProviderError):
                provider.generate("system", "user question", 100)


if __name__ == "__main__":
    unittest.main()
