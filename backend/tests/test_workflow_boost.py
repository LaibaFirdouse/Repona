import unittest

from app.retrieval.workflow_boost import loaded_needs_more_bodies, workflow_lexical_queries


class WorkflowBoostTests(unittest.TestCase):
    def test_extracts_tokens_from_question_not_a_fixed_list(self) -> None:
        queries = workflow_lexical_queries("How is IMPORT_RULES handled in exportRules()?")
        self.assertIn("IMPORT_RULES", queries)
        self.assertIn("exportRules", queries)
        self.assertNotIn("getServerSession", queries)

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
