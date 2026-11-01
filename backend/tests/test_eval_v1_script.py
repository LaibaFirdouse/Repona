"""Scoring logic of scripts/eval_v1.py and the shape of its question set."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location("eval_v1", ROOT / "scripts" / "eval_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eval_v1 = _load_script()


class ScoreTests(unittest.TestCase):
    def test_answer_citing_an_expected_file_is_a_hit(self) -> None:
        question = {"id": "q1", "question": "How?", "expected_files": ["src/pkg/signer.py"]}
        response = {"answer": "It signs.", "citations": [{"path": "src/pkg/signer.py", "start_line": 1, "end_line": 9}]}
        record = eval_v1.score(question, response, None)
        self.assertFalse(record["refused"])
        self.assertTrue(record["expected_hit"])
        self.assertEqual(record["cited_paths"], ["src/pkg/signer.py"])

    def test_refusal_line_is_refused_and_not_a_hit(self) -> None:
        question = {"id": "q1", "question": "How?", "expected_files": ["a.py"]}
        response = {"answer": "The repository evidence did not produce a cited claim.", "citations": []}
        record = eval_v1.score(question, response, None)
        self.assertTrue(record["refused"])
        self.assertFalse(record["expected_hit"])

    def test_absent_topic_answered_with_absence_is_ok(self) -> None:
        question = {"id": "q1", "question": "Email?", "expected_files": [], "expect_absent": True}
        response = {"answer": "The repository does not send email; it only indexes code.", "citations": []}
        record = eval_v1.score(question, response, None)
        self.assertTrue(record["absent_ok"])
        self.assertNotIn("expected_hit", record)

    def test_errors_are_never_hits(self) -> None:
        question = {"id": "q1", "question": "How?", "expected_files": ["a.py"]}
        record = eval_v1.score(question, None, "HTTP 503: unavailable")
        self.assertEqual(record["error"], "HTTP 503: unavailable")
        self.assertIsNone(record["refused"])
        self.assertFalse(record["expected_hit"])

    def test_path_matching_tolerates_prefixes(self) -> None:
        self.assertTrue(eval_v1._path_matches("repo/src/a.py", "src/a.py"))
        self.assertTrue(eval_v1._path_matches("src/a.py", "/src/a.py"))
        self.assertFalse(eval_v1._path_matches("src/ba.py", "a.py"))


class SummaryTests(unittest.TestCase):
    def test_rates_split_answerable_and_absent_questions(self) -> None:
        records = [
            {"expect_absent": False, "error": None, "refused": False, "expected_hit": True, "latency_seconds": 10.0},
            {"expect_absent": False, "error": None, "refused": True, "expected_hit": False, "latency_seconds": 20.0},
            {"expect_absent": True, "error": None, "refused": True, "absent_ok": True, "latency_seconds": 30.0},
        ]
        summary = eval_v1.summarize(records)
        self.assertEqual(summary["questions"], 3)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["answered"], "1/2")
        self.assertEqual(summary["expected_hit"], "1/2")
        self.assertEqual(summary["absent_ok"], "1/1")
        self.assertEqual(summary["latency_median_seconds"], 20.0)


class QuestionSetTests(unittest.TestCase):
    def test_question_set_is_well_formed(self) -> None:
        spec = json.loads((ROOT / "evaluation" / "v1-eval-questions.json").read_text(encoding="utf-8"))
        ids = [question["id"] for repo in spec["repositories"] for question in repo["questions"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(spec["repositories"]), 3)
        for repo in spec["repositories"]:
            self.assertTrue(repo["repo_url"].startswith("https://github.com/"))
            for question in repo["questions"]:
                if question.get("expect_absent"):
                    self.assertEqual(question["expected_files"], [], question["id"])
                else:
                    self.assertTrue(question["expected_files"], question["id"])


if __name__ == "__main__":
    unittest.main()
