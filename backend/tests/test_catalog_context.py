import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.retrieval.catalog_context import _dedupe_paths, catalog_implementation_chunk_choices
from app.retrieval.fusion import ChunkChoice


class CatalogContextTests(unittest.TestCase):
    def test_dedupe_paths_preserves_order(self) -> None:
        paths = _dedupe_paths(["include/a.hpp", "include/a.hpp", "src/b.py"], limit=8)
        self.assertEqual(paths, ["include/a.hpp", "src/b.py"])

    def test_generation_has_path_uses_query(self) -> None:
        from app.retrieval.catalog_context import generation_has_path

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.limit.return_value.first.return_value = (
            "file-id",
        )
        self.assertTrue(generation_has_path(db, "gen-1", "include/ring.hpp"))

    def test_implementation_choices_never_fall_back_to_unrelated_large_files(self) -> None:
        db = MagicMock()
        generation = SimpleNamespace(id="gen-1", parser_version="v1")
        with patch("app.retrieval.catalog_context._files_matching_terms", return_value=[]), patch(
            "app.retrieval.catalog_context._symbols_matching_terms", return_value=[]
        ):
            choices = catalog_implementation_chunk_choices(db, generation, "How does billing work?")
        self.assertEqual(choices, [])
        db.query.assert_not_called()

    def test_implementation_choices_fall_back_to_matching_symbols(self) -> None:
        db = MagicMock()
        generation = SimpleNamespace(id="gen-1", parser_version="v1")
        choice = ChunkChoice("chunk-1", "sym-1", "symbol_body", 0)
        with patch("app.retrieval.catalog_context._files_matching_terms", return_value=[]), patch(
            "app.retrieval.catalog_context._symbols_matching_terms", return_value=[SimpleNamespace(id="sym-1")]
        ), patch("app.retrieval.chunk_selection.pick_best_symbol_chunk", return_value=choice):
            choices = catalog_implementation_chunk_choices(db, generation, "How does billing work?")
        self.assertEqual(choices, [choice])


if __name__ == "__main__":
    unittest.main()
