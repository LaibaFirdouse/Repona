import unittest

from app.retrieval.more_evidence import _merge_choices
from app.retrieval.fusion import ChunkChoice


class MoreEvidenceTests(unittest.TestCase):
    def test_merge_dedupes_chunk_ids(self) -> None:
        a = ChunkChoice("1", "sym", "symbol_body", 0)
        b = ChunkChoice("2", "sym", "symbol_body", 1)
        c = ChunkChoice("1", "sym", "symbol_body", 0)
        merged = _merge_choices([a], [b, c], limit=8)
        self.assertEqual([item.chunk_id for item in merged], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
