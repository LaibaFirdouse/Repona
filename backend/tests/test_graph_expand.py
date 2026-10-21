import unittest

from app.retrieval.graph_expand import next_symbol_frontier, seed_symbol_anchors


class GraphExpandTests(unittest.TestCase):
    def test_seed_prefers_symbols(self) -> None:
        roles = {"file-1": "file", "sym-1": "symbol", "sym-2": "symbol"}
        seeded = seed_symbol_anchors(["file-1", "sym-1", "sym-2"], roles, limit=4)
        self.assertEqual(seeded, ["sym-1", "sym-2"])

    def test_next_frontier_follows_calls(self) -> None:
        rows = [
            {
                "anchor_id": "a",
                "other_id": "b",
                "relationship_type": "CALLS",
                "outgoing": True,
            }
        ]
        roles = {"a": "symbol", "b": "symbol"}
        self.assertEqual(next_symbol_frontier({"a"}, rows, roles, limit=8), ["b"])


if __name__ == "__main__":
    unittest.main()
