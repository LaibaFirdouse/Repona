import unittest
from unittest.mock import MagicMock

from app.retrieval.fusion import LaneHit
from app.retrieval.hydrate import file_ids_for_chunk_hits


class HydrateTests(unittest.TestCase):
    def test_file_ids_empty_when_no_chunk_hits(self) -> None:
        db = MagicMock()
        self.assertEqual(file_ids_for_chunk_hits(db, "gen-1", []), [])
        db.query.assert_not_called()

    def test_file_ids_skips_symbol_only_hits(self) -> None:
        db = MagicMock()
        hits = [LaneHit("sym-1", None, 1)]
        self.assertEqual(file_ids_for_chunk_hits(db, "gen-1", hits), [])


if __name__ == "__main__":
    unittest.main()
