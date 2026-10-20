import unittest
from types import SimpleNamespace

from app.retrieval.chunk_selection import (
    is_meta_config_path,
    limit_spans_per_file,
    representative_chunk,
    wants_substantive_implementation,
)
from app.retrieval.conversation_context import QaTurn


def _chunk(kind: str, size: int, ordinal: int = 0) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, start_byte=0, end_byte=size, ordinal=ordinal, id=f"{kind}-{size}")


class ChunkSelectionTests(unittest.TestCase):
    def test_prefers_largest_symbol_body_over_imports(self) -> None:
        chunks = [
            _chunk("import_block", 200),
            _chunk("symbol_body", 4000, 1),
            _chunk("symbol_body", 800, 0),
        ]
        pick = representative_chunk(chunks)
        assert pick is not None
        self.assertEqual(pick.kind, "symbol_body")
        self.assertEqual(pick.end_byte, 4000)

    def test_meta_config_paths(self) -> None:
        self.assertTrue(is_meta_config_path("package.json"))
        self.assertTrue(is_meta_config_path("README.md"))
        self.assertFalse(is_meta_config_path("app/api/plans/route.ts"))

    def test_substantive_workflow_question(self) -> None:
        self.assertTrue(wants_substantive_implementation("tell me the end to end workflow", []))
        self.assertTrue(
            wants_substantive_implementation(
                "how are import rules and export rules implemented in code",
                [],
            )
        )
        self.assertTrue(
            wants_substantive_implementation(
                "explain the workflows",
                [QaTurn("how is monitor implemented", "")],
            )
        )

    def test_limit_spans_per_file(self) -> None:
        loaded = [
            {"relative_path": "main.go", "chunk_id": "1"},
            {"relative_path": "main.go", "chunk_id": "2"},
            {"relative_path": "main.go", "chunk_id": "3"},
            {"relative_path": "api.go", "chunk_id": "4"},
        ]
        kept = limit_spans_per_file(loaded, max_per_file=2)
        self.assertEqual(len(kept), 3)


if __name__ == "__main__":
    unittest.main()
