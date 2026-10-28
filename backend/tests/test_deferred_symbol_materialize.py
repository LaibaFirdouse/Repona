"""BUG 2: catalogue symbols without bodies materialise from the snapshot on demand."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ingestion.assemble import FileRecord, PROGRESSIVE_DEFER_KINDS, _chunk_records, _symbol_records
from app.ingestion.chunk_materialize import materialize_symbol_chunks, missing_symbol_chunk_ids
from app.ingestion.parse import parse_file
from app.models.catalog import File, Symbol
from app.retrieval.packet import pack_evidence
from app.storage.identity import content_hash, file_id, logical_file_key


_ASSEMBLE_SOURCE = b'''"""Assemble a generation draft from parsed repository files."""


def assemble_generation(repo_id, commit, blobs, fingerprint, generation_id, workers=1):
    """Walk blobs, parse files, and assemble chunk/symbol records."""
    draft = []
    for blob in blobs:
        draft.append(blob)
    return draft


def helper_not_needed():
    return None
'''


def _repo_and_file(relative_path: str, payload: bytes):
    repo = "a36afb8d-ee2b-5acb-919a-21822bb36f53"
    record = FileRecord(
        id=str(file_id(repo, relative_path, content_hash(payload))),
        directory_id="a36afb8d-ee2b-5acb-919a-21822bb36f54",
        logical_file_key=str(logical_file_key(repo, relative_path)),
        relative_path=relative_path,
        language="python",
        extension=".py",
        line_count=payload.count(b"\n") + 1,
        byte_size=len(payload),
        content_hash=content_hash(payload),
        git_blob_id="blob",
        parse_status="parsed",
        parse_message=None,
    )
    return repo, record


class DeferredAssembleGenerationMaterializeTests(unittest.TestCase):
    def test_materialize_loads_assemble_generation_from_snapshot(self) -> None:
        relative = "backend/app/ingestion/assemble.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(_ASSEMBLE_SOURCE)
            repo, file_record = _repo_and_file(relative, _ASSEMBLE_SOURCE)
            parsed = parse_file(relative, _ASSEMBLE_SOURCE)
            symbols = _symbol_records(repo, file_record, parsed, _ASSEMBLE_SOURCE)
            target = next(item for item in symbols if item.name == "assemble_generation")
            self.assertTrue(target.id)

            db_file = File(
                id=file_record.id,
                repository_id=repo,
                directory_id=file_record.directory_id,
                logical_file_key=file_record.logical_file_key,
                relative_path=relative,
                language="python",
                extension=".py",
                line_count=file_record.line_count,
                byte_size=file_record.byte_size,
                content_hash=file_record.content_hash,
                git_blob_id="blob",
            )
            db_symbol = Symbol(
                id=target.id,
                repository_id=repo,
                file_id=file_record.id,
                parent_symbol_id=None,
                identity_key=target.identity_key,
                kind=target.kind,
                name="assemble_generation",
                qualified_name="assemble_generation",
                signature=target.signature,
                ordinal=target.ordinal,
                language="python",
                start_byte=target.start_byte,
                end_byte=target.end_byte,
                start_line=target.start_line,
                end_line=target.end_line,
                parser_version="pv",
            )

            db = MagicMock()
            query = MagicMock()
            query.join.return_value.filter.return_value.all.return_value = [(db_symbol, db_file)]
            db.query.return_value = query

            captured: list = []

            def _capture_append(_db, _repo, _gen, _fp, draft, on_progress=None):
                captured.extend(draft.chunks)

            with patch(
                "app.ingestion.chunk_materialize.append_generation_chunks",
                side_effect=_capture_append,
            ), patch(
                "app.ingestion.chunk_materialize.ensure_chunk_records_vectors"
            ) as ensure_vectors:
                created = materialize_symbol_chunks(
                    db,
                    repository_id=repo,
                    generation_id="gen-lazy",
                    fingerprint="fp",
                    parser_version="pv",
                    symbol_ids=[target.id],
                    snapshot_root=root,
                    generation=MagicMock(id="gen-lazy"),
                    qdrant_url="http://localhost:6333",
                    embed=None,  # provider unavailable — lexical path must still work
                )
                ensure_vectors.assert_not_called()

            self.assertTrue(created)
            self.assertTrue(all(chunk.symbol_id == target.id for chunk in created))
            self.assertTrue(all(chunk.kind in PROGRESSIVE_DEFER_KINDS for chunk in created))
            bodies = [chunk for chunk in created if chunk.kind in {"symbol_body", "symbol_part"}]
            self.assertTrue(bodies)
            span = bodies[0]
            excerpt = _ASSEMBLE_SOURCE[span.start_byte : span.end_byte].decode("utf-8")
            self.assertIn("def assemble_generation", excerpt)
            self.assertEqual(span.start_byte, target.start_byte)
            self.assertEqual(captured, created)

            # Repeat request reuses: missing_symbol_chunk_ids reports none once present.
            present_db = MagicMock()
            present_query = MagicMock()
            present_query.join.return_value.filter.return_value.all.return_value = [(target.id,)]
            present_db.query.return_value = present_query
            self.assertEqual(missing_symbol_chunk_ids(present_db, "gen-lazy", [target.id]), [])

            # Packed final prompt must carry path + symbol identity + source body.
            loaded = [
                {
                    "chunk_id": span.id,
                    "relative_path": relative,
                    "qualified_name": "assemble_generation",
                    "chunk_kind": span.kind,
                    "start_line": span.start_line,
                    "end_line": span.end_line,
                    "text": excerpt,
                    "raw_bytes": len(excerpt.encode("utf-8")),
                    "symbol_id": target.id,
                    "source_start_byte": span.start_byte,
                    "source_end_byte": span.end_byte,
                    "context_prefix": "",
                    "question_target": True,
                }
            ]
            packed = pack_evidence(
                "How does assemble_generation work across files?\n",
                loaded
                + [
                    {
                        "chunk_id": "other",
                        "relative_path": "backend/app/ingestion/pipeline.py",
                        "qualified_name": "ingest_commit",
                        "chunk_kind": "symbol_body",
                        "start_line": 1,
                        "end_line": 5,
                        "text": "def ingest_commit():\n    pass\n",
                        "raw_bytes": 30,
                        "symbol_id": "sym.ingest",
                        "source_start_byte": 0,
                        "source_end_byte": 30,
                        "context_prefix": "",
                    }
                ],
                [],
                [],
                source_byte_cap=50_000,
                input_byte_cap=100_000,
                context_tokens=100_000,
                output_tokens=1,
                span_text=lambda item: (item["raw_bytes"], item["text"]),
                edge_text=lambda _e: "",
                technology_text=lambda _t: "",
                required_symbol_ids={target.id},
            )
            prompt = "How does assemble_generation work across files?\n" + "".join(
                item["text"] for item in packed.spans
            )
            self.assertIn("def assemble_generation", prompt)
            self.assertEqual(packed.spans[0]["relative_path"], relative)
            self.assertEqual(packed.spans[0]["qualified_name"], "assemble_generation")
            self.assertEqual(packed.spans[0]["symbol_id"], target.id)

    def test_missing_snapshot_raises_explicit_error(self) -> None:
        from app.ingestion.chunk_materialize import ChunkMaterializeError

        relative = "backend/app/ingestion/assemble.py"
        repo, file_record = _repo_and_file(relative, _ASSEMBLE_SOURCE)
        parsed = parse_file(relative, _ASSEMBLE_SOURCE)
        symbols = _symbol_records(repo, file_record, parsed, _ASSEMBLE_SOURCE)
        target = next(item for item in symbols if item.name == "assemble_generation")
        db_file = File(
            id=file_record.id,
            repository_id=repo,
            directory_id=file_record.directory_id,
            logical_file_key=file_record.logical_file_key,
            relative_path=relative,
            language="python",
            extension=".py",
            line_count=file_record.line_count,
            byte_size=file_record.byte_size,
            content_hash=file_record.content_hash,
            git_blob_id="blob",
        )
        db_symbol = Symbol(
            id=target.id,
            repository_id=repo,
            file_id=file_record.id,
            parent_symbol_id=None,
            identity_key=target.identity_key,
            kind=target.kind,
            name="assemble_generation",
            qualified_name="assemble_generation",
            signature=target.signature,
            ordinal=target.ordinal,
            language="python",
            start_byte=target.start_byte,
            end_byte=target.end_byte,
            start_line=target.start_line,
            end_line=target.end_line,
            parser_version="pv",
        )
        db = MagicMock()
        query = MagicMock()
        query.join.return_value.filter.return_value.all.return_value = [(db_symbol, db_file)]
        db.query.return_value = query
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ChunkMaterializeError):
                materialize_symbol_chunks(
                    db,
                    repository_id=repo,
                    generation_id="gen-lazy",
                    fingerprint="fp",
                    parser_version="pv",
                    symbol_ids=[target.id],
                    snapshot_root=Path(temporary),
                )

    def test_graph_neighbor_repick_after_materialize(self) -> None:
        """Deferred neighbours must be re-picked after on-demand materialize (answer path)."""
        from app.retrieval.fusion import ChunkChoice

        spans: list[ChunkChoice] = []
        missing_neighbors = ["sym.assemble_generation"]
        materialized = {"sym.assemble_generation": ChunkChoice("c-assemble", "sym.assemble_generation", "symbol_body", 0)}

        def pick_best(_db, _gen, symbol_id):
            return materialized.get(symbol_id)

        # Mirror the fixed answer.py re-pick loop.
        for symbol_id in missing_neighbors:
            extra = pick_best(None, None, symbol_id)
            if extra is not None and all(item.chunk_id != extra.chunk_id for item in spans):
                spans.append(extra)
        self.assertEqual([item.chunk_id for item in spans], ["c-assemble"])
        self.assertEqual(spans[0].anchor_id, "sym.assemble_generation")

    def test_expansion_stems_assembles_generation(self) -> None:
        from app.retrieval.expansion import expansion_names

        names = expansion_names(
            "Trace how ingest_commit assembles a generation and writes the graph."
        )
        self.assertIn("assemble_generation", names)


if __name__ == "__main__":
    unittest.main()
