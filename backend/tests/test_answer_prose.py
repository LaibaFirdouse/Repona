import unittest

from app.retrieval.answer_prose import (
    filter_audit_claims,
    meta_reasoning_question,
    prose_is_retrieval_audit,
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

    def test_meta_reasoning_detector(self) -> None:
        self.assertTrue(meta_reasoning_question("okay how did u come to above conclusion"))
        self.assertFalse(meta_reasoning_question("what does ring.hpp implement?"))

    def test_retrieval_audit_detector(self) -> None:
        audit = (
            "Given only truncated exports, a faithful walkthrough cannot be stated from "
            "the repository material provided here."
        )
        self.assertTrue(prose_is_retrieval_audit(audit))
        self.assertFalse(prose_is_retrieval_audit("The root layout wraps children in app/layout.tsx."))

    def test_filter_audit_claims_drops_meta_only(self) -> None:
        kept = filter_audit_claims(
            [
                {
                    "kind": "observed",
                    "text": "The retrieved lines only show incomplete default export statements.",
                    "evidence_ids": ["E1"],
                },
                {
                    "kind": "observed",
                    "text": "app/layout.tsx exports a root layout component.",
                    "evidence_ids": ["E2"],
                },
            ]
        )
        self.assertEqual(len(kept), 1)
        self.assertIn("layout.tsx", kept[0]["text"])


if __name__ == "__main__":
    unittest.main()
