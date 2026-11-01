import json
import unittest

from app.core.config import settings
from app.retrieval.answer_turn import FINAL_ONLY_SUFFIX, parse_answer_turn


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

    def test_extra_claims_are_trimmed_not_rejected(self) -> None:
        claims = [
            {"kind": "observed", "text": f"step {index}", "evidence_ids": ["E1"]}
            for index in range(settings.llm_max_claims + 3)
        ]
        for body in ({"status": "final", "claims": claims}, {"claims": claims}):
            turn = parse_answer_turn(json.dumps(body))
            self.assertIsNotNone(turn)
            assert turn is not None
            self.assertEqual(len(turn.claims), settings.llm_max_claims)
            self.assertEqual(turn.claims[-1]["text"], f"step {settings.llm_max_claims - 1}")

    def test_a_preamble_before_the_json_is_tolerated(self) -> None:
        raw = 'Sure. {"status":"final","claims":[{"kind":"observed","text":"Hello","evidence_ids":["E1"]}]}'
        turn = parse_answer_turn(raw)
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(turn.claims[0]["text"], "Hello")

    def test_final_only_suffix_states_the_cap(self) -> None:
        self.assertIn(f"at most {settings.llm_max_claims} claims", FINAL_ONLY_SUFFIX)
        self.assertNotIn("need_evidence", FINAL_ONLY_SUFFIX)


if __name__ == "__main__":
    unittest.main()
