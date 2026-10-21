import unittest

from app.retrieval.chunk_selection import wants_code_trace, wants_substantive_implementation
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


if __name__ == "__main__":
    unittest.main()
