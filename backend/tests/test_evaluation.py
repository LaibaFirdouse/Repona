import unittest

from app.evaluation.record import case_record
from app.retrieval.packet import NO_CITED_CLAIM


class EvaluationRecordTests(unittest.TestCase):
    def test_a_needed_span_is_recorded_without_scoring_the_words(self) -> None:
        record = case_record(
            case_id="exact_function",
            question="issue_token",
            needed=[{"kind": "span", "qualified_name": "issue_token"}],
            packet_spans=[{"qualified_name": "issue_token", "text": "def issue_token():\n    return 1\n"}],
            packet_technologies=[],
            claims=[{"kind": "observed", "text": "issue_token returns 1.", "evidence_ids": ["chunk-1"]}],
            citations=[{"chunk_id": "chunk-1"}],
            citations_valid=True,
            message="The active generation matches this commit.",
            retrieval_seconds=0.2,
            model_seconds=1.5,
            action="serve",
        )
        self.assertTrue(record["needed_reached"])
        self.assertTrue(record["citation_ids_verify"])
        self.assertFalse(record["no_claim_line"])
        self.assertEqual(record["claims"][0]["text"], "issue_token returns 1.")
        self.assertNotIn("supported", record)

    def test_a_later_part_counts_only_when_its_marker_is_in_the_packet(self) -> None:
        record = case_record(
            case_id="split_symbol",
            question="split_report",
            needed=[{"kind": "span", "qualified_name": "split_report", "text_marker": "SPLIT_MARKER_TAIL"}],
            packet_spans=[{"qualified_name": "split_report", "text": "def split_report():\n    return 0\n"}],
            packet_technologies=[],
            claims=[],
            citations=[],
            citations_valid=False,
            message=NO_CITED_CLAIM,
            retrieval_seconds=0.1,
            model_seconds=0.4,
            action="serve",
        )
        self.assertFalse(record["needed_reached"])
        self.assertTrue(record["no_claim_line"])

    def test_a_question_with_no_needed_span_does_not_invent_one(self) -> None:
        record = case_record(
            case_id="unanswerable",
            question="Where is the payroll tax table calculated?",
            needed=[],
            packet_spans=[],
            packet_technologies=[],
            claims=[],
            citations=[],
            citations_valid=False,
            message=NO_CITED_CLAIM,
            retrieval_seconds=0.1,
            model_seconds=0.2,
            action="serve",
        )
        self.assertIsNone(record["needed_reached"])
        self.assertTrue(record["no_claim_line"])

    def test_a_wait_during_refresh_does_not_call_the_model(self) -> None:
        record = case_record(
            case_id="question_during_refresh",
            question="issue_token",
            needed=[],
            packet_spans=[],
            packet_technologies=[],
            claims=[],
            citations=[],
            citations_valid=False,
            message="A newer commit is being indexed. The previous generation is not being served.",
            retrieval_seconds=0.05,
            model_seconds=None,
            action="wait",
        )
        self.assertEqual(record["action"], "wait")
        self.assertIsNone(record["model_seconds"])
        self.assertFalse(record["no_claim_line"])


if __name__ == "__main__":
    unittest.main()
