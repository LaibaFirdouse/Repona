import unittest

from app.retrieval.answer_turn import parse_answer_turn


class AnswerTurnTests(unittest.TestCase):
    def test_legacy_claims_object(self) -> None:
        turn = parse_answer_turn('{"claims":[{"kind":"observed","text":"Hello","evidence_ids":["E1"]}]}')
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(turn.status, "final")
        self.assertEqual(turn.claims[0]["text"], "Hello")

    def test_need_evidence(self) -> None:
        turn = parse_answer_turn(
            '{"status":"need_evidence","more_evidence":{"note":"Need README","lexical_queries":["readme"],'
            '"path_contains":["README.md"]}}'
        )
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(turn.status, "need_evidence")
        self.assertEqual(turn.more_evidence["lexical_queries"], ["readme"])


if __name__ == "__main__":
    unittest.main()
