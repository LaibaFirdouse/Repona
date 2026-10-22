import unittest

from app.retrieval.query_plan import QueryPlanV1
from app.retrieval.query_router import parse_planner_json, route_question
from app.retrieval.query_terms import build_lexical_queries, extract_search_terms


class QueryRouterTests(unittest.TestCase):
    def test_fallback_uses_full_lanes_without_planner(self) -> None:
        request = route_question("Where is authenticate_user defined?", planner=None)
        self.assertIn("symbol", request.lanes)
        self.assertIn("lexical", request.lanes)
        self.assertFalse(request.used_llm_planner)
        self.assertEqual(request.intent, "exact_lookup")

    def test_planner_overrides_lanes(self) -> None:
        def planner(_question: str) -> QueryPlanV1:
            return QueryPlanV1(
                intent="exact_lookup",
                retrieval_lanes=["symbol", "lexical"],
                lexical_queries=["authenticate_user"],
            )

        request = route_question("Where is authenticate_user defined?", planner=planner)
        self.assertEqual(request.lanes, ["symbol", "lexical"])
        self.assertTrue(request.used_llm_planner)
        self.assertEqual(request.intent, "exact_lookup")

    def test_path_terms_in_fallback(self) -> None:
        request = route_question("Show src/flask/app.py", planner=None)
        self.assertTrue(any("/" in item for item in request.path_filters + extract_search_terms(request.question)))

    def test_planner_json_validation(self) -> None:
        plan = parse_planner_json(
            '{"intent":"debugging","search_terms":["Error"],"identifiers":[],"path_filters":[],'
            '"lexical_queries":["Error"],"semantic_query":"Error handling","retrieval_lanes":["symbol","vector"],'
            '"graph_relations":["calls"],"max_graph_hops":2,"source_targets":[],"needs_source_bodies":true}'
        )
        self.assertIsInstance(plan, QueryPlanV1)
        self.assertEqual(plan.intent, "debugging")
        self.assertEqual(plan.max_graph_hops, 2)

    def test_malformed_planner_json(self) -> None:
        self.assertIsNone(parse_planner_json("{not json"))

    def test_lexical_query_variants(self) -> None:
        queries = build_lexical_queries("How does Flask dispatch_request handle errors?")
        self.assertGreaterEqual(len(queries), 1)
        self.assertTrue(any("dispatch_request" in item or "Flask" in item for item in queries))


if __name__ == "__main__":
    unittest.main()
