import unittest
from unittest.mock import MagicMock, patch

from app.ingestion.assemble import ChunkRecord
from app.ingestion.lazy_vectors import _generation_draft_for_records, ensure_chunk_vectors
from app.models.catalog import File, IndexGeneration, Symbol


def _chunk_record(chunk_id: str = "chunk-1", file_id: str = "file-1", symbol_id: str | None = "sym-1") -> ChunkRecord:
    return ChunkRecord(
        id=chunk_id,
        file_id=file_id,
        symbol_id=symbol_id,
        kind="symbol_body",
        ordinal=0,
        start_byte=0,
        end_byte=10,
        start_line=1,
        end_line=2,
        span_hash="span",
        context_prefix="",
        embedding_input_hash="hash-1",
        lexical_text="def write_edges(): pass",
    )


class LazyVectorsDraftTests(unittest.TestCase):
    def test_symbol_relative_path_comes_from_file_join(self) -> None:
        symbol = Symbol(
            id="sym-1",
            repository_id="repo",
            file_id="file-1",
            parent_symbol_id=None,
            identity_key="ik",
            kind="function",
            name="write_edges",
            qualified_name="write_edges",
            signature="()",
            ordinal=0,
            language="python",
            start_byte=0,
            end_byte=10,
            start_line=1,
            end_line=2,
            parser_version="pv",
        )
        file = File(
            id="file-1",
            repository_id="repo",
            directory_id="dir",
            logical_file_key="lfk",
            relative_path="backend/app/ingestion/graph_store.py",
            language="python",
            extension=".py",
            line_count=10,
            byte_size=100,
            content_hash="abc",
            git_blob_id="blob",
        )
        db = MagicMock()

        def query_side_effect(*models):
            chain = MagicMock()
            if models == (File,):
                chain.filter.return_value.all.return_value = [file]
            elif models and models[0] is Symbol:
                chain.join.return_value.filter.return_value.all.return_value = [(symbol, file)]
            return chain

        db.query.side_effect = query_side_effect
        draft = _generation_draft_for_records(db, [_chunk_record()])
        self.assertEqual(len(draft.symbols), 1)
        self.assertEqual(draft.symbols[0].relative_path, "backend/app/ingestion/graph_store.py")


class LazyEmbedLifecycleTests(unittest.TestCase):
    def test_ensure_chunk_vectors_marks_ready_after_upsert(self) -> None:
        db = MagicMock()
        generation = IndexGeneration(
            id="gen",
            repository_id="repo",
            qdrant_collection="col",
            vector_dimensions=2,
            embedding_config_fingerprint="fp",
        )
        record = _chunk_record()

        with patch("app.ingestion.lazy_vectors.chunks_missing_vectors", return_value=["chunk-1"]), patch(
            "app.ingestion.lazy_vectors.chunk_records_for_ids", return_value=[record]
        ), patch("app.ingestion.lazy_vectors.ensure_collection"), patch(
            "app.ingestion.lazy_vectors._generation_draft_for_records", return_value=MagicMock()
        ), patch(
            "app.ingestion.lazy_vectors.embed_chunks_with_reuse", return_value={"chunk-1": [0.1, 0.2]}
        ), patch("app.ingestion.lazy_vectors.upsert_missing_points", return_value=1), patch(
            "app.ingestion.lazy_vectors.mark_vectors_ready"
        ) as mark_ready:
            ready = ensure_chunk_vectors(
                db,
                generation=generation,
                repository_id="repo",
                chunk_ids=["chunk-1"],
                snapshot_root=MagicMock(),
                qdrant_url="http://qdrant",
                embed=lambda _t: [0.1, 0.2],
            )
        self.assertEqual(ready, ["chunk-1"])
        mark_ready.assert_called_once_with(db, "gen", ["chunk-1"])

    def test_semantic_reembed_when_qdrant_payload_mismatch(self) -> None:
        from app.ingestion.embedding_reuse import embed_chunks_with_reuse

        chunk = _chunk_record()
        payloads = {
            "pid": {
                "chunk_id": "chunk-1",
                "span_hash": "old",
                "embedding_config_fingerprint": "fp",
                "embedding_input_hash": "hash-1",
                "repository_id": "repo",
            }
        }
        calls = {"embed": 0}

        def embed(_text):
            calls["embed"] += 1
            return [0.9, 0.1]

        with patch("app.ingestion.embedding_reuse.fetch_point_payloads", return_value=payloads), patch(
            "app.ingestion.embedding_reuse.fetch_point_vectors", return_value={}
        ), patch("app.ingestion.embedding_reuse.point_id", return_value="pid"):
            vectors = embed_chunks_with_reuse("http://q", "col", "repo", "fp", embed, [chunk], lambda: None, stats={})
        self.assertEqual(vectors["chunk-1"], [0.9, 0.1])
        self.assertEqual(calls["embed"], 1)


if __name__ == "__main__":
    unittest.main()
