import unittest
from unittest.mock import patch

from app.ingestion.assemble import ChunkRecord
from app.ingestion.embedding_reuse import embed_chunks_with_reuse


def _chunk(chunk_id: str, lexical: str = "hello", span_hash: str = "sh") -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        file_id="file",
        symbol_id=None,
        kind="file_overview",
        ordinal=0,
        start_byte=0,
        end_byte=5,
        start_line=1,
        end_line=1,
        span_hash=span_hash,
        context_prefix="",
        embedding_input_hash=f"hash-{chunk_id}",
        lexical_text=lexical,
    )


class EmbeddingReuseTests(unittest.TestCase):
    def test_reuses_existing_qdrant_vector(self) -> None:
        chunk = _chunk("c1")
        payloads = {"pid": {"chunk_id": "c1", "span_hash": "sh", "embedding_config_fingerprint": "fp", "embedding_input_hash": "hash-c1", "repository_id": "repo"}}
        vectors = {"pid": [0.1, 0.2]}
        calls = {"embed": 0}

        def embed(_text):
            calls["embed"] += 1
            return [1.0]

        with patch("app.ingestion.embedding_reuse.fetch_point_payloads", return_value=payloads), patch(
            "app.ingestion.embedding_reuse.fetch_point_vectors", return_value=vectors
        ), patch("app.ingestion.embedding_reuse.point_id", return_value="pid"):
            result = embed_chunks_with_reuse("http://q", "col", "repo", "fp", embed, [chunk], lambda: None, stats={})
        self.assertEqual(result["c1"], [0.1, 0.2])
        self.assertEqual(calls["embed"], 0)

    def test_mismatch_forces_reembed(self) -> None:
        chunk = _chunk("c1", span_hash="new")
        payloads = {"pid": {"chunk_id": "c1", "span_hash": "old", "embedding_config_fingerprint": "fp", "embedding_input_hash": "hash-c1", "repository_id": "repo"}}

        def embed(_text):
            return [0.5]

        with patch("app.ingestion.embedding_reuse.fetch_point_payloads", return_value=payloads), patch(
            "app.ingestion.embedding_reuse.fetch_point_vectors", return_value={}
        ), patch("app.ingestion.embedding_reuse.point_id", return_value="pid"):
            result = embed_chunks_with_reuse("http://q", "col", "repo", "fp", embed, [chunk], lambda: None)
        self.assertEqual(result["c1"], [0.5])


if __name__ == "__main__":
    unittest.main()
