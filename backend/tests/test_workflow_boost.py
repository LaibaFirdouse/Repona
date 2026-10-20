import unittest

from app.retrieval.workflow_boost import loaded_needs_more_bodies, workflow_lexical_queries


class WorkflowBoostTests(unittest.TestCase):
    def test_extracts_snake_and_phrases(self) -> None:
        queries = workflow_lexical_queries("How is IMPORT_RULES handled with chrome.storage?")
        self.assertIn("IMPORT_RULES", queries)
        self.assertTrue(any("storage" in q.lower() or "chrome" in q.lower() for q in queries))

    def test_loaded_needs_more_bodies(self) -> None:
        thin = [
            {"chunk_kind": "import_block", "raw_bytes": 80},
            {"chunk_kind": "file_overview", "raw_bytes": 50},
        ]
        self.assertTrue(loaded_needs_more_bodies(thin))
        rich = [{"chunk_kind": "symbol_body", "raw_bytes": 1200} for _ in range(5)]
        self.assertFalse(loaded_needs_more_bodies(rich))


if __name__ == "__main__":
    unittest.main()
