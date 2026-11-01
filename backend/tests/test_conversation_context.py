import unittest

from app.core.config import settings
from app.retrieval.conversation_context import (
    QaTurn,
    format_answer_history,
    format_planner_user,
    normalize_turns,
    retrieval_question_text,
)


class ConversationContextTests(unittest.TestCase):
    def test_short_follow_up_borrows_the_previous_question_not_the_answer(self) -> None:
        history = [
            QaTurn("What does main.go do?", "It starts the HTTP server."),
        ]
        text = retrieval_question_text("What about tests?", history)
        self.assertTrue(text.startswith("What about tests?"))
        self.assertIn("main.go", text)
        self.assertNotIn("HTTP server", text)
        self.assertNotIn("Assistant", text)
        self.assertNotIn("User asked", text)

    def test_self_contained_question_is_searched_alone(self) -> None:
        history = [QaTurn("What does main.go do?", "It starts the HTTP server.")]
        question = "How does Signer.sign derive the signing key?"
        self.assertEqual(retrieval_question_text(question, history), question)

    def test_only_the_latest_previous_question_is_borrowed(self) -> None:
        history = [
            QaTurn("How is routing configured in router.go?", "Routes are registered."),
            QaTurn("Where is rate limiting done?", "In middleware/rate_limit.py."),
        ]
        text = retrieval_question_text("And tests?", history)
        self.assertIn("rate limiting", text)
        self.assertNotIn("router.go", text)

    def test_no_history_returns_the_question(self) -> None:
        self.assertEqual(retrieval_question_text("  What about tests?  ", []), "What about tests?")

    def test_normalize_turns_caps_count(self) -> None:
        history = [QaTurn(f"Q{i}", f"A{i}") for i in range(20)]
        kept = normalize_turns(history)
        cap = settings.answer_conversation_max_turns
        self.assertEqual(len(kept), cap)
        self.assertEqual(kept[0].question, f"Q{20 - cap}")

    def test_normalize_turns_truncates_long_answers(self) -> None:
        limit = settings.answer_conversation_answer_chars
        kept = normalize_turns([QaTurn("Q", "x" * (limit + 500))])
        self.assertLessEqual(len(kept[0].answer), limit + 1)
        self.assertTrue(kept[0].answer.endswith("…"))

    def test_format_answer_history_labels_session_context(self) -> None:
        block = format_answer_history([QaTurn("Hi", "Hello")])
        self.assertIn("trusted session context", block)
        self.assertIn("User: Hi", block)

    def test_planner_user_includes_history(self) -> None:
        user = format_planner_user(
            "And the handlers?",
            [QaTurn("Where is routing?", "In router.go.")],
        )
        self.assertIn("Conversation so far:", user)
        self.assertIn("Current question", user)
        self.assertIn("And the handlers?", user)


if __name__ == "__main__":
    unittest.main()
