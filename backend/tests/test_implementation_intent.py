import unittest

from app.retrieval.chunk_selection import (
    deprioritize_harness_spans,
    wants_code_trace,
    wants_domain_implementation,
    wants_substantive_implementation,
)
from app.retrieval.code_first import wants_implementation_evidence
from app.retrieval.evidence_scoring import retrieval_prefers_implementation
from app.retrieval.query_router import RetrievalRequest


def _req(**kwargs) -> RetrievalRequest:
    defaults = {
        "question": "q",
        "intent": "conceptual_search",
        "identifiers": [],
        "path_filters": [],
        "lexical_queries": [],
        "semantic_query": "",
        "lanes": ["symbol", "lexical", "vector"],
        "max_graph_hops": 1,
        "needs_source_bodies": True,
    }
    defaults.update(kwargs)
    return RetrievalRequest(**defaults)


class ImplementationIntentTests(unittest.TestCase):
    def test_how_exactly_triggers_substantive(self) -> None:
        self.assertTrue(wants_substantive_implementation("how exactly ingestion works here", []))

    def test_trace_question(self) -> None:
        self.assertTrue(wants_code_trace("trace the code for this behaviour", []))

    def test_queue_question_is_domain_implementation(self) -> None:
        self.assertTrue(wants_domain_implementation("how exactly this queue works step by step", []))

    def test_trace_question_triggers_code_first_evidence(self) -> None:
        self.assertTrue(
            wants_implementation_evidence("how exactly this queue works step by step by tracing through the code", [])
        )

    def test_harness_spans_sort_after_implementation(self) -> None:
        class _Span:
            def __init__(self, path: str) -> None:
                self.relative_path = path

        spans = [_Span("Makefile"), _Span("include/ring.hpp"), _Span("benchmark/run_benchmark.cpp")]
        ordered = deprioritize_harness_spans(spans)
        self.assertEqual(ordered[0].relative_path, "include/ring.hpp")

    def test_planner_execution_trace(self) -> None:
        self.assertTrue(
            retrieval_prefers_implementation(
                "explain indexing",
                [],
                retrieval=_req(intent="execution_trace"),
                preview_mostly_non_body=False,
                code_first=False,
                substantive=False,
            )
        )

    def test_thin_preview_signal(self) -> None:
        self.assertTrue(
            retrieval_prefers_implementation(
                "explain indexing",
                [],
                retrieval=_req(),
                preview_mostly_non_body=True,
                code_first=False,
                substantive=False,
            )
        )

    def test_overview_questions_skip_implementation_mode(self) -> None:
        self.assertFalse(
            retrieval_prefers_implementation(
                "tell me everything about this repo",
                [],
                retrieval=_req(intent="architecture_question"),
                preview_mostly_non_body=False,
                code_first=False,
                substantive=False,
                overview_question=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
