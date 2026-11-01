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

    def test_sort_keeps_retrieval_order_among_code_spans(self) -> None:
        # Retrieval rank wins over chunk kind and size: a small, highly ranked span is not pushed
        # behind a large, lower-ranked function body.
        spans = [
            {"chunk_id": "rank1", "relative_path": "app/rules.py", "chunk_kind": "non_code", "text": "RULES = {}", "raw_bytes": 40},
            {"chunk_id": "rank2", "relative_path": "app/huge.py", "chunk_kind": "symbol_body", "text": "def huge(): ...", "raw_bytes": 9000},
            {"chunk_id": "rank3", "relative_path": "app/imports.py", "chunk_kind": "import_block", "text": "import os", "raw_bytes": 20},
        ]
        ordered = sort_loaded_spans(spans, overview=False, terms=["huge"])
        self.assertEqual([span["chunk_id"] for span in ordered], ["rank1", "rank2", "rank3"])

    def test_docs_sink_only_when_not_an_overview(self) -> None:
        spans = [
            {"chunk_id": "doc", "relative_path": "README.md", "chunk_kind": "file_overview", "text": "About", "raw_bytes": 100},
            {"chunk_id": "code", "relative_path": "app/main.py", "chunk_kind": "symbol_body", "text": "def main(): ...", "raw_bytes": 100},
        ]
        self.assertEqual([s["chunk_id"] for s in sort_loaded_spans(spans, overview=False, terms=[])], ["code", "doc"])
        self.assertEqual([s["chunk_id"] for s in sort_loaded_spans(spans, overview=True, terms=[])], ["doc", "code"])

    def test_symbol_parts_are_coalesced_in_source_order(self) -> None:
        spans = [
            {"chunk_id": "p2", "symbol_id": "s1", "relative_path": "a.py", "chunk_kind": "symbol_part", "source_start_byte": 200},
            {"chunk_id": "other", "symbol_id": "s2", "relative_path": "b.py", "chunk_kind": "symbol_body", "source_start_byte": 0},
            {"chunk_id": "p1", "symbol_id": "s1", "relative_path": "a.py", "chunk_kind": "symbol_part", "source_start_byte": 0},
        ]
        ordered = sort_loaded_spans(spans, overview=False, terms=[])
        self.assertEqual([span["chunk_id"] for span in ordered], ["p1", "p2", "other"])

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
