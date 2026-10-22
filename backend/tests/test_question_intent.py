import unittest

from app.retrieval.question_intent import (
    classify_intent,
    heuristic_path_filters,
    merge_plan_with_heuristics,
)
from app.retrieval.query_plan import QueryPlanV1
from app.retrieval.query_router import route_question


class QuestionIntentTests(unittest.TestCase):
    def test_request_flow_is_execution_trace(self) -> None:
        self.assertEqual(classify_intent("Explain the core request flow"), "execution_trace")

    def test_queue_trace_question(self) -> None:
        self.assertEqual(
            classify_intent("how exactly this queue works step by step by tracing through the code"),
            "execution_trace",
        )

    def test_exact_lookup(self) -> None:
        self.assertEqual(classify_intent("Where is authenticate_user defined?"), "exact_lookup")

    def test_architecture_overview(self) -> None:
        self.assertEqual(classify_intent("Tell me everything about this repo"), "architecture_question")

    def test_bench_repo_path_hints(self) -> None:
        hints = heuristic_path_filters("how exactly this queue works")
        self.assertTrue(any("include" in item for item in hints))

    def test_http_path_hints(self) -> None:
        hints = heuristic_path_filters("Explain the HTTP request flow for POST /api/v1/ask")
        self.assertTrue(any("api" in item or "routes" in item for item in hints))

    def test_heuristic_fallback_without_planner(self) -> None:
        request = route_question("Explain the core request flow", planner=None)
        self.assertEqual(request.intent, "execution_trace")
        self.assertIn("graph", request.lanes)

    def test_merge_upgrades_weak_planner_intent(self) -> None:
        plan = QueryPlanV1(intent="conceptual_search", path_filters=[])
        merged = merge_plan_with_heuristics(plan, "how exactly this queue works step by step", None)
        self.assertEqual(merged.intent, "execution_trace")
        self.assertTrue(any("include" in p for p in merged.path_filters))


if __name__ == "__main__":
    unittest.main()
