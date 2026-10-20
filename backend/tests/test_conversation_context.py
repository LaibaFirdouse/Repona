import unittest

from app.retrieval.conversation_context import (
    QaTurn,
    format_answer_history,
    format_planner_user,
    normalize_turns,
    retrieval_question_text,
)


class ConversationContextTests(unittest.TestCase):
    def test_retrieval_question_includes_prior_turns(self) -> None:
        history = [
            QaTurn("What does main.go do?", "It starts the HTTP server."),
        ]
        text = retrieval_question_text("What about tests?", history)
        self.assertIn("main.go", text)
        self.assertIn("Follow-up question: What about tests?", text)

    def test_normalize_turns_caps_count(self) -> None:
        history = [QaTurn(f"Q{i}", f"A{i}") for i in range(20)]
        kept = normalize_turns(history)
        self.assertLessEqual(len(kept), 8)
        self.assertEqual(kept[0].question, "Q12")

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
