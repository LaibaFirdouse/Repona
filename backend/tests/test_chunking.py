import unittest

from app.ingestion.chunking import Span, build_chunks, split_on_newlines
from app.ingestion.inclusion import is_included


class InclusionTests(unittest.TestCase):
    def test_includes_source_and_named_manifests(self) -> None:
        self.assertTrue(is_included("src/app.py", "100644"))
        self.assertTrue(is_included("Dockerfile", "100755"))
        self.assertTrue(is_included("requirements.txt", "100644"))

    def test_skips_other_text_lockfiles_and_vendor_trees(self) -> None:
        self.assertFalse(is_included("notes.txt", "100644"))
        self.assertFalse(is_included("package-lock.json", "100644"))
        self.assertFalse(is_included("node_modules/pkg/index.js", "100644"))
        self.assertFalse(is_included("build/app.js", "100644"))
        self.assertFalse(is_included("link.py", "120000"))


class ChunkingTests(unittest.TestCase):
    def test_empty_payload_has_no_chunk(self) -> None:
        self.assertEqual(build_chunks(b"", [], []), [])

    def test_valid_file_without_declarations_is_file_overview(self) -> None:
        chunks = build_chunks(b"hello\n", [], [])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].kind, "file_overview")
        self.assertEqual((chunks[0].start_byte, chunks[0].end_byte), (0, 6))

    def test_small_symbol_is_one_body_and_leftover_is_non_code(self) -> None:
        payload = b"def a():\n    return 1\n\nrest\n"
        symbol = (( "function", "a", "", 0), "function", Span(0, 22))
        chunks = build_chunks(payload, [symbol], [])
        kinds = [chunk.kind for chunk in chunks]
        self.assertIn("symbol_body", kinds)
        self.assertIn("non_code", kinds)
        body = next(chunk for chunk in chunks if chunk.kind == "symbol_body")
        self.assertEqual(body.ordinal, 0)
        self.assertEqual(body.anchor, "symbol")

    def test_large_symbol_is_parts_that_cover_the_span_once(self) -> None:
        payload = b"ab\ncd\nef"
        symbol = (("function", "a", "", 0), "function", Span(0, len(payload)))
        chunks = build_chunks(payload, [symbol], [], limit=3)
        parts = [chunk for chunk in chunks if chunk.kind == "symbol_part"]
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0].start_byte, 0)
        self.assertEqual(parts[-1].end_byte, len(payload))
        for left, right in zip(parts, parts[1:]):
            self.assertEqual(left.end_byte, right.start_byte)
        covered = b"".join(payload[part.start_byte : part.end_byte] for part in parts)
        self.assertEqual(covered, payload)

    def test_long_line_stays_one_part(self) -> None:
        payload = b"abcdef\n"
        parts = split_on_newlines(payload, 0, len(payload), limit=4)
        self.assertEqual([(part.start_byte, part.end_byte) for part in parts], [(0, 7)])

    def test_nested_method_is_not_also_non_code(self) -> None:
        payload = b"class A:\n    def b():\n        return 1\n"
        class_end = len(payload)
        method = Span(9, class_end)
        chunks = build_chunks(
            payload,
            [
                (("class", "A", "", 0), "class", Span(0, class_end)),
                (("method", "A.b", "", 0), "method", method),
            ],
            [],
        )
        self.assertFalse(any(chunk.kind == "non_code" for chunk in chunks))

    def test_import_ordinals_are_per_file_and_may_overlap_a_symbol(self) -> None:
        payload = b"def a():\n    import os\n    return os\n"
        chunks = build_chunks(
            payload,
            [(("function", "a", "", 0), "function", Span(0, len(payload)))],
            [Span(12, 22), Span(4, 8)],
        )
        imports = [chunk for chunk in chunks if chunk.kind == "import_block"]
        self.assertEqual([chunk.ordinal for chunk in imports], [0, 1])
        self.assertLess(imports[0].start_byte, imports[1].start_byte)
        self.assertTrue(all(chunk.symbol_ordinal_key is None for chunk in imports))


if __name__ == "__main__":
    unittest.main()
