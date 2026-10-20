import unittest

from app.ingestion.assemble import PROGRESSIVE_DEFER_KINDS, GenerationDraft, _chunk_records
from app.ingestion.chunking import Span, build_chunks


class ProgressiveIndexingTests(unittest.TestCase):
    def test_defer_kinds_include_symbol_spans(self) -> None:
        self.assertIn("symbol_body", PROGRESSIVE_DEFER_KINDS)
        self.assertIn("symbol_part", PROGRESSIVE_DEFER_KINDS)
        self.assertNotIn("file_overview", PROGRESSIVE_DEFER_KINDS)

    def test_assemble_splits_deferred_chunks(self) -> None:
        catalog = [
            type("C", (), {"kind": "file_overview", "id": "1"})(),
            type("C", (), {"kind": "symbol_body", "id": "2"})(),
        ]
        draft = GenerationDraft(chunks=[item for item in catalog])
        deferred = [chunk for chunk in draft.chunks if chunk.kind in PROGRESSIVE_DEFER_KINDS]
        draft.chunks = [chunk for chunk in draft.chunks if chunk.kind not in PROGRESSIVE_DEFER_KINDS]
        draft.deferred_chunks = deferred
        self.assertEqual(len(draft.chunks), 1)
        self.assertEqual(len(draft.deferred_chunks), 1)

    def test_chunk_records_can_target_one_symbol(self) -> None:
        payload = b"def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
        from app.ingestion.assemble import FileRecord, _symbol_records
        from app.ingestion.parse import parse_file

        from app.storage.identity import content_hash, file_id, logical_file_key

        repo = "a36afb8d-ee2b-5acb-919a-21822bb36f53"
        parsed = parse_file("sample.py", payload)
        file = FileRecord(
            id=str(file_id(repo, "sample.py", content_hash(payload))),
            directory_id="a36afb8d-ee2b-5acb-919a-21822bb36f54",
            logical_file_key=str(logical_file_key(repo, "sample.py")),
            relative_path="sample.py",
            language="python",
            extension=".py",
            line_count=4,
            byte_size=len(payload),
            content_hash=content_hash(payload),
            git_blob_id="blob",
            parse_status="parsed",
            parse_message=None,
        )
        symbols = _symbol_records(repo, file, parsed, payload)
        self.assertGreaterEqual(len(symbols), 2)
        only = symbols[0].id
        chunks = _chunk_records(
            file,
            symbols,
            parsed,
            payload,
            "fp",
            only_symbol_id=only,
            chunk_kinds=PROGRESSIVE_DEFER_KINDS,
        )
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.symbol_id == only for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
