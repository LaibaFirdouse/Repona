import unittest
from unittest.mock import MagicMock

from app.retrieval.catalog_context import _dedupe_paths


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


if __name__ == "__main__":
    unittest.main()
