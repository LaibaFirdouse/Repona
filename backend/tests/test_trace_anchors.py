import unittest

from app.retrieval.trace_anchors import question_wants_trace_anchors


class TraceAnchorIntentTests(unittest.TestCase):
    def test_main_entry_question_wants_anchors(self) -> None:
        self.assertTrue(question_wants_trace_anchors("How does the main entry point work?", "conceptual_search"))

    def test_execution_trace_always_wants_anchors(self) -> None:
        self.assertTrue(question_wants_trace_anchors("explain indexing", "execution_trace"))

    def test_unrelated_lookup_does_not(self) -> None:
        self.assertFalse(question_wants_trace_anchors("Where is foo defined?", "exact_lookup"))


if __name__ == "__main__":
    unittest.main()
