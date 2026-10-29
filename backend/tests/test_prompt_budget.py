"""Provider selection must not imply an unrelated model's tokenizer."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.retrieval.packet import (
    CONSERVATIVE_BYTES_PER_TOKEN,
    conservative_byte_tokens,
    ollama_prompt_fits,
    pack_evidence,
    reset_qwen_tokenizer_cache,
)
from app.retrieval.prompt_budget import (
    OPENAI_REPLY_PRIMING_TOKENS,
    OPENAI_TOKENS_PER_MESSAGE,
    VERIFIED_QWEN_OLLAMA_MODELS,
    ollama_model_uses_qwen_tokenizer,
    resolve_prompt_budget,
)


class PromptBudgetStrategyTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_qwen_tokenizer_cache()

    def test_cursor_uses_conservative_bytes_and_never_loads_qwen(self) -> None:
        budget = resolve_prompt_budget("cursor", "composer-2.5-fast")
        self.assertEqual(budget.strategy, "conservative_bytes")
        self.assertFalse(budget.exact)
        self.assertEqual(budget.margin_tokens, 256)
        text = "a" * 12
        self.assertEqual(budget.count_text(text), conservative_byte_tokens(text))
        with patch("app.retrieval.packet._qwen") as qwen:
            budget.count_text("hello")
            budget.count_chat("system", "user")
            qwen.assert_not_called()

    def test_cursor_pack_and_repair_both_count_system_message(self) -> None:
        """Final pack check must not drop the system role that repair and the provider use."""
        budget = resolve_prompt_budget("cursor", "composer-2.5-fast")
        system = "SYSTEM instructions and format suffixes"
        user = "question plus evidence packet"
        pack_tokens = budget.pack_counter(system_prefix=system)(user)
        chat_tokens, margin = budget.measure_chat(system, user)
        self.assertEqual(pack_tokens, chat_tokens)
        self.assertEqual(margin, budget.margin_tokens)
        self.assertEqual(pack_tokens, conservative_byte_tokens(system + "\n" + user))
        # Omitting system would under-count relative to the provider call.
        self.assertGreater(pack_tokens, conservative_byte_tokens(user))
        self.assertEqual(
            budget.repair_exceeds(system, user, 10),
            not budget.fits(chat_tokens, 10, margin),
        )

    def test_cursor_path_does_not_import_transformers(self) -> None:
        budget = resolve_prompt_budget("cursor")
        import builtins

        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "transformers" or name.startswith("transformers."):
                raise AssertionError("Cursor budgeting must not import transformers")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            budget.count_text("evidence packet")
            budget.count_chat("system", "user")
            self.assertTrue(budget.fits(100, 800))

    def test_openai_with_tiktoken_counts_chat_framing(self) -> None:
        encoding = MagicMock()
        encoding.encode.side_effect = lambda text: list(range(len(text or "")))

        with patch("app.retrieval.prompt_budget._tiktoken_encoding", return_value=encoding):
            budget = resolve_prompt_budget("openai", "gpt-4o-mini")
            self.assertEqual(budget.strategy, "tiktoken")
            self.assertFalse(budget.exact)
            self.assertEqual(budget.margin_tokens, 0)
            tokens, margin = budget.measure_chat("sys", "user!")
            self.assertEqual(margin, 0)
            # 3 reply priming + 2 messages * 3 framing + len("sys") + len("user!")
            expected = (
                OPENAI_REPLY_PRIMING_TOKENS
                + OPENAI_TOKENS_PER_MESSAGE
                + len("sys")
                + OPENAI_TOKENS_PER_MESSAGE
                + len("user!")
            )
            self.assertEqual(tokens, expected)
            self.assertEqual(encoding.encode.call_count, 2)

        with patch("app.retrieval.packet._qwen") as qwen:
            with patch("app.retrieval.prompt_budget._tiktoken_encoding", return_value=encoding):
                resolve_prompt_budget("openai", "gpt-4o-mini").count_chat("a", "b")
            qwen.assert_not_called()

    def test_openai_without_tiktoken_uses_conservative_margin(self) -> None:
        with patch("app.retrieval.prompt_budget._tiktoken_encoding", return_value=None):
            budget = resolve_prompt_budget("openai", "gpt-4o-mini")
        self.assertEqual(budget.strategy, "conservative_bytes")
        self.assertFalse(budget.exact)
        self.assertEqual(budget.margin_tokens, 256)
        tokens, margin = budget.measure_chat("system", "user")
        self.assertEqual(tokens, conservative_byte_tokens("system\nuser"))
        self.assertEqual(margin, 256)

    def test_openai_tiktoken_runtime_failure_applies_conservative_margin(self) -> None:
        encoding = MagicMock()
        encoding.encode.side_effect = RuntimeError("encode failed")
        with patch("app.retrieval.prompt_budget._tiktoken_encoding", return_value=encoding):
            budget = resolve_prompt_budget("openai", "gpt-4o-mini")
            self.assertEqual(budget.strategy, "tiktoken")
            self.assertEqual(budget.margin_tokens, 0)
            tokens, margin = budget.measure_chat("system", "user")
        self.assertEqual(tokens, conservative_byte_tokens("system\nuser"))
        self.assertEqual(margin, 256)
        # pack_counter inflates tokens so a fixed margin_tokens=0 still reserves safety.
        counter = budget.pack_counter(system_prefix="system")
        self.assertEqual(counter("user"), tokens + 256)

    def test_ollama_verified_qwen_model_uses_qwen_tokenizer(self) -> None:
        self.assertIn("qwen2.5:1.5b", VERIFIED_QWEN_OLLAMA_MODELS)
        self.assertTrue(ollama_model_uses_qwen_tokenizer("qwen2.5:1.5b"))
        budget = resolve_prompt_budget("ollama", "qwen2.5:1.5b")
        self.assertEqual(budget.strategy, "qwen_chat_template")
        self.assertTrue(budget.exact)
        self.assertEqual(budget.margin_tokens, 256)
        with patch("app.retrieval.prompt_budget.qwen_prompt_tokens", return_value=22) as counted:
            self.assertEqual(budget.count_chat("Reply with a JSON object.", "Say hi."), 22)
            counted.assert_called_once()

    def test_ollama_other_model_does_not_use_qwen(self) -> None:
        self.assertFalse(ollama_model_uses_qwen_tokenizer("llama3.2:3b"))
        budget = resolve_prompt_budget("ollama", "llama3.2:3b")
        self.assertEqual(budget.strategy, "conservative_bytes")
        self.assertFalse(budget.exact)
        with patch("app.retrieval.packet._qwen") as qwen:
            self.assertEqual(budget.count_chat("system", "user"), conservative_byte_tokens("system\nuser"))
            qwen.assert_not_called()
        with patch("app.retrieval.prompt_budget.qwen_prompt_tokens") as counted:
            ollama_prompt_fits("system", "user", 800, model="llama3.2:3b")
            counted.assert_not_called()

    def test_qwen_tokenizer_failure_falls_back_to_conservative(self) -> None:
        budget = resolve_prompt_budget("ollama", "qwen2.5:1.5b")
        with patch(
            "app.retrieval.prompt_budget.qwen_prompt_tokens",
            side_effect=OSError("huggingface hub unavailable"),
        ):
            tokens, margin = budget.measure_chat("system", "user body")
        self.assertEqual(tokens, conservative_byte_tokens("system\nuser body"))
        self.assertEqual(margin, 256)
        self.assertTrue(budget.repair_exceeds("system", "user body", budget.context_tokens))

    def test_pack_and_repair_share_the_same_budget_policy(self) -> None:
        self.assertEqual(CONSERVATIVE_BYTES_PER_TOKEN, 3)
        cursor = resolve_prompt_budget("cursor")
        with patch("app.retrieval.prompt_budget._tiktoken_encoding", return_value=None):
            openai_approx = resolve_prompt_budget("openai", "gpt-4o-mini")
        qwen = resolve_prompt_budget("ollama", "qwen2.5:1.5b")
        other = resolve_prompt_budget("ollama", "mistral")

        self.assertEqual(cursor.margin_tokens, 256)
        self.assertEqual(openai_approx.strategy, "conservative_bytes")
        self.assertEqual(openai_approx.margin_tokens, 256)
        self.assertEqual(qwen.margin_tokens, 256)
        self.assertEqual(other.margin_tokens, 256)

        system, user = "system prompt", "user packet"
        for budget in (cursor, openai_approx, other):
            tokens, margin = budget.measure_chat(system, user)
            # Pack counter with system_prefix matches repair's measure_chat.
            pack_tokens = budget.pack_counter(system_prefix=system)(user)
            self.assertEqual(pack_tokens, tokens + max(0, margin - budget.margin_tokens))
            self.assertEqual(pack_tokens, tokens)
            exceeds = budget.repair_exceeds(system, user, budget.context_tokens)
            self.assertEqual(exceeds, not budget.fits(tokens, budget.context_tokens, margin))

        with patch("app.retrieval.prompt_budget.qwen_prompt_tokens", return_value=100):
            tokens, margin = qwen.measure_chat(system, user)
            pack_tokens = qwen.pack_counter(system_prefix=system)(user)
            self.assertEqual(tokens, 100)
            self.assertEqual(margin, 256)
            self.assertEqual(pack_tokens, 100)
            self.assertFalse(qwen.repair_exceeds(system, user, 10))
            self.assertTrue(qwen.repair_exceeds(system, user, qwen.context_tokens))

    def test_evidence_packing_uses_budget_counter_and_margin(self) -> None:
        spans = [{"id": "a", "raw": 4, "text": "abcd"}, {"id": "b", "raw": 4, "text": "efgh"}]
        budget = resolve_prompt_budget("cursor")
        packed = pack_evidence(
            "qqqq",
            spans,
            [],
            [],
            source_byte_cap=100,
            input_byte_cap=10_000,
            context_tokens=2,
            output_tokens=1,
            margin_tokens=budget.margin_tokens,
            count_tokens=budget.count_text,
            span_text=lambda span: (span["raw"], span["text"]),
            edge_text=lambda _edge: "",
            technology_text=lambda _item: "",
        )
        self.assertEqual(packed.spans, [])

    def test_verified_qwen_template_count_when_tokenizer_available(self) -> None:
        try:
            from app.retrieval.packet import qwen_prompt_tokens

            self.assertEqual(qwen_prompt_tokens("Reply with a JSON object.", "Say hi."), 22)
        except Exception as error:
            self.skipTest(f"Qwen tokenizer unavailable: {error}")


if __name__ == "__main__":
    unittest.main()
