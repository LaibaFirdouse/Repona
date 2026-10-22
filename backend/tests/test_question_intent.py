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

    def test_generic_how_it_works_is_conceptual_not_trace(self) -> None:
        self.assertEqual(
            classify_intent("How does the main entry point work?"),
            "conceptual_search",
        )

    def test_http_routing_question_is_execution_trace(self) -> None:
        self.assertEqual(
            classify_intent(
                "When a client sends an HTTP request, how does the application pick which code runs?"
            ),
            "execution_trace",
        )

    def test_queue_trace_question(self) -> None:
        self.assertEqual(
            classify_intent("how exactly this queue works step by step by tracing through the code"),
            "execution_trace",
        )

    def test_exact_lookup(self) -> None:
        self.assertEqual(classify_intent("Where is authenticate_user defined?"), "exact_lookup")

    def test_architecture_overview(self) -> None:
        self.assertEqual(classify_intent("Tell me everything about this repo"), "architecture_question")

    def test_path_hints_from_question_only(self) -> None:
        hints = heuristic_path_filters("walk through include/ring.hpp enqueue logic")
        self.assertIn("include/ring.hpp", hints)

    def test_path_hints_no_invented_prefixes(self) -> None:
        hints = heuristic_path_filters("how exactly this queue works")
        self.assertEqual(hints, [])

    def test_heuristic_fallback_without_planner(self) -> None:
        request = route_question("Explain the core request flow", planner=None)
        self.assertEqual(request.intent, "execution_trace")
        self.assertIn("graph", request.lanes)

    def test_merge_upgrades_weak_planner_intent(self) -> None:
        plan = QueryPlanV1(intent="conceptual_search", path_filters=[])
        merged = merge_plan_with_heuristics(plan, "how exactly this queue works step by step", None)
        self.assertEqual(merged.intent, "execution_trace")
        self.assertIn("graph", merged.retrieval_lanes)


if __name__ == "__main__":
    unittest.main()
