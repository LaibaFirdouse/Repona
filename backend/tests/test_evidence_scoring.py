import unittest

from app.retrieval.evidence_scoring import (
    is_documentation_path,
    resolve_span_limit,
    sort_loaded_spans,
)
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


class EvidenceScoringTests(unittest.TestCase):
    def test_documentation_path(self) -> None:
        self.assertTrue(is_documentation_path("docs/HOSTING.md"))
        self.assertTrue(is_documentation_path("README.md"))
        self.assertFalse(is_documentation_path("backend/app/ingestion/pipeline.py"))

    def test_sort_prefers_symbol_body_over_readme(self) -> None:
        spans = [
            {
                "chunk_id": "1",
                "relative_path": "README.md",
                "chunk_kind": "file_overview",
                "text": "Repona overview",
                "raw_bytes": 8000,
            },
            {
                "chunk_id": "2",
                "relative_path": "backend/app/ingestion/pipeline.py",
                "chunk_kind": "symbol_body",
                "text": "def ingest_commit",
                "raw_bytes": 4000,
            },
        ]
        ordered = sort_loaded_spans(spans, overview=False, terms=["ingest"])
        self.assertEqual(ordered[0]["chunk_id"], "2")

    def test_trace_intent_widens_per_file(self) -> None:
        _, per_file = resolve_span_limit(
            _req(intent="execution_trace"),
            overview_question=False,
            code_first=False,
            prefer_impl=False,
        )
        self.assertGreaterEqual(per_file, 3)


if __name__ == "__main__":
    unittest.main()
