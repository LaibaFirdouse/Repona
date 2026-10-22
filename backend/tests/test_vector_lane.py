import unittest
from unittest.mock import patch

from app.retrieval.fusion import LaneHit
from app.retrieval.query_router import RetrievalRequest
from app.retrieval.vector_lane import should_retry_vector_lane, should_skip_vector_lane


def _request(**kwargs) -> RetrievalRequest:
    defaults = {
        "question": "how does ingestion work",
        "intent": "conceptual_search",
        "identifiers": [],
        "path_filters": [],
        "lexical_queries": ["ingestion"],
        "semantic_query": "ingestion pipeline",
        "lanes": ["symbol", "lexical", "vector", "graph"],
        "max_graph_hops": 1,
        "needs_source_bodies": True,
    }
    defaults.update(kwargs)
    return RetrievalRequest(**defaults)


class VectorLaneTests(unittest.TestCase):
    def test_skip_vector_lane_when_no_ready_vectors(self) -> None:
        class _Session:
            pass

        with patch("app.retrieval.vector_lane.count_vector_ready", return_value=0):
            self.assertTrue(should_skip_vector_lane(_Session(), "gen"))

        with patch("app.retrieval.vector_lane.count_vector_ready", return_value=3):
            self.assertFalse(should_skip_vector_lane(_Session(), "gen"))

    def test_retry_when_vector_empty_and_lazy_on(self) -> None:
        self.assertTrue(
            should_retry_vector_lane(
                _request(),
                [],
                lazy_embed_on_query=True,
                snapshot_available=True,
            )
        )

    def test_no_retry_when_hits_exist(self) -> None:
        self.assertFalse(
            should_retry_vector_lane(
                _request(),
                [LaneHit("a", "c1", 1)],
                lazy_embed_on_query=True,
                snapshot_available=True,
            )
        )

    def test_no_retry_when_vector_lane_off(self) -> None:
        self.assertFalse(
            should_retry_vector_lane(
                _request(lanes=["symbol", "lexical"]),
                [],
                lazy_embed_on_query=True,
                snapshot_available=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
