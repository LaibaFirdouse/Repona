import unittest

from app.retrieval.answer_prose import (
    meta_reasoning_question,
    sanitize_answer_prose,
)
from app.retrieval.packet import render_answer


class AnswerProseTests(unittest.TestCase):
    def test_sanitize_removes_evidence_tags(self) -> None:
        raw = "Core logic lives in include/ring.hpp (E4) and batch paths (E8, E10)."
        cleaned = sanitize_answer_prose(raw)
        self.assertNotIn("E4", cleaned)
        self.assertNotIn("E8", cleaned)
        self.assertIn("ring.hpp", cleaned)

    def test_render_answer_sanitizes_claims(self) -> None:
        text = render_answer(
            [{"kind": "observed", "text": "See ring.hpp (E6) for queues.", "evidence_ids": ["E1"]}],
        )
        self.assertNotIn("E6", text)
        self.assertIn("ring.hpp", text)

    def test_code_blocks_keep_their_indentation(self) -> None:
        raw = (
            "The guard returns early (E2):\n\n"
            "```python\n"
            "def sign(self, value):\n"
            "    if not value:\n"
            "        return None\n"
            "    return value + E1\n"
            "```\n"
        )
        cleaned = sanitize_answer_prose(raw)
        self.assertIn("    if not value:\n        return None\n", cleaned)
        # Code is left exactly as written, even text that looks like an evidence label.
        self.assertIn("return value + E1", cleaned)
        self.assertNotIn("(E2)", cleaned)

    def test_inline_code_and_identifier_like_labels_survive(self) -> None:
        cleaned = sanitize_answer_prose("Ruff rule E501 is disabled; see `config[E12]` in setup.cfg (E3).")
        self.assertIn("E501", cleaned)
        self.assertIn("`config[E12]`", cleaned)
        self.assertNotIn("(E3)", cleaned)

    def test_wording_is_not_rewritten(self) -> None:
        raw = "The retry policy is not part of the excerpts shown here, so it cannot be described."
        self.assertEqual(sanitize_answer_prose(raw), raw)

    def test_markdown_list_indentation_is_kept(self) -> None:
        raw = "Steps:\n  - parse the request\n  - call  the handler"
        self.assertEqual(sanitize_answer_prose(raw), "Steps:\n  - parse the request\n  - call the handler")

    def test_meta_reasoning_detector(self) -> None:
        self.assertTrue(meta_reasoning_question("okay how did u come to above conclusion"))
        self.assertFalse(meta_reasoning_question("what does ring.hpp implement?"))


if __name__ == "__main__":
    unittest.main()
