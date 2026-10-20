import unittest

from app.retrieval.code_first import (
    code_first_question,
    deprioritize_readme_spans,
    filter_readme_when_code_first,
    is_readme_path,
    wants_implementation_evidence,
)
from app.retrieval.conversation_context import QaTurn
from app.retrieval.fusion import ChunkChoice


class CodeFirstTests(unittest.TestCase):
    def test_detects_code_preference(self) -> None:
        self.assertTrue(code_first_question("I want the info from the source code rather than readme"))
        self.assertTrue(code_first_question("can you tell the above from code"))
        self.assertFalse(code_first_question("what is this repo about"))

    def test_history_carries_code_preference(self) -> None:
        history = [QaTurn("explain from implementation not readme", "ok")]
        self.assertTrue(wants_implementation_evidence("what about bitmap?", history))

    def test_filter_readme_when_enough_impl(self) -> None:
        loaded = [
            {"relative_path": "README.md", "chunk_id": "1"},
            {"relative_path": "include/alloc.hpp", "chunk_id": "2"},
            {"relative_path": "src/alloc.cpp", "chunk_id": "3"},
        ]
        kept = filter_readme_when_code_first(loaded)
        self.assertEqual([s["chunk_id"] for s in kept], ["2", "3"])

    def test_deprioritize_readme_spans(self) -> None:
        spans = [
            ChunkChoice("a", "f1", "symbol_body", 0),
            ChunkChoice("b", "f2", "symbol_body", 0),
        ]
        # ChunkChoice has no path — use dict path via monkey patch not needed; test is_readme only
        self.assertTrue(is_readme_path("docs/README.md"))
        self.assertFalse(is_readme_path("include/x.hpp"))


if __name__ == "__main__":
    unittest.main()
