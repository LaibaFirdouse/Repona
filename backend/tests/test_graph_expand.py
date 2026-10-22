import unittest
from unittest.mock import MagicMock

from app.retrieval.graph_expand import graph_neighbor_symbol_ids, next_symbol_frontier


class GraphExpandTests(unittest.TestCase):
    def test_next_symbol_frontier_follows_calls_outgoing(self) -> None:
        rows = [
            {
                "relationship_type": "CALLS",
                "anchor_id": "main-sym",
                "other_id": "helper-sym",
                "outgoing": True,
            }
        ]
        roles = {"main-sym": "symbol", "helper-sym": "symbol"}
        found = next_symbol_frontier({"main-sym"}, rows, roles, limit=4)
        self.assertEqual(found, ["helper-sym"])

    def test_graph_neighbor_returns_empty_without_seeds(self) -> None:
        session = MagicMock()
        db = MagicMock()
        self.assertEqual(graph_neighbor_symbol_ids(session, db, "gen-1", [], max_hops=2), [])


if __name__ == "__main__":
    unittest.main()
