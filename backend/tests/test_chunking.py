import unittest
from types import SimpleNamespace

from app.ingestion.chunking import Span, build_chunks, split_on_newlines
from app.ingestion.inclusion import (
    BINARY_SNIFF_BYTES,
    MAX_INDEXED_FILE_BYTES,
    is_included,
    is_indexable_payload,
    is_indexed_blob,
)
from app.ingestion.versions import MAX_CHUNK_BYTES


class InclusionTests(unittest.TestCase):
    def test_includes_source_and_named_manifests(self) -> None:
        self.assertTrue(is_included("src/app.py", "100644"))
        self.assertTrue(is_included("cmd/main.go", "100644"))
        self.assertTrue(is_included("src/lib.rs", "100644"))
        self.assertTrue(is_included("Main.java", "100644"))
        self.assertTrue(is_included("src/app.CPP", "100644"))
        self.assertTrue(is_included("Makefile", "100644"))
        self.assertTrue(is_included("Dockerfile", "100755"))
        self.assertTrue(is_included("requirements.txt", "100644"))
        self.assertTrue(is_included("pom.xml", "100644"))
        self.assertTrue(is_included("Cargo.toml", "100644"))
        self.assertTrue(is_included("pyproject.toml", "100644"))

    def test_includes_docs_and_config_text(self) -> None:
        for path in (
            "notes.txt",
            "requirements-dev.txt",
            "docs/index.rst",
            "docs/guide.mdx",
            "setup.cfg",
            "alembic.ini",
            "README",
            "Procfile",
            "Jenkinsfile",
            "Dockerfile.hosted",
            "deploy/api.dockerfile",
        ):
            self.assertTrue(is_included(path, "100644"), path)

    def test_skips_vendored_virtualenv_and_build_output(self) -> None:
        for path in (
            "vendor/github.com/pkg/errors/errors.go",
            "third_party/lib/util.c",
            ".venv/lib/python3.12/site-packages/x.py",
            "venv/bin/activate",
            "target/debug/build.rs",
            "web/.next/server/page.js",
            "pkg/__pycache__/mod.py",
        ):
            self.assertFalse(is_included(path, "100644"), path)

    def test_skips_generated_and_minified_files(self) -> None:
        for path in (
            "static/app.min.js",
            "static/site.MIN.CSS",
            "static/app.js.map",
            "proto/orders_pb2.py",
            "proto/orders_pb2_grpc.py",
            "api/orders.pb.go",
        ):
            self.assertFalse(is_included(path, "100644"), path)
        self.assertTrue(is_included("static/app.js", "100644"))
        self.assertTrue(is_included("proto/orders.py", "100644"))

    def test_payload_size_cap_and_binary_sniffing(self) -> None:
        self.assertTrue(is_indexable_payload(b"print('hi')\n"))
        self.assertTrue(is_indexable_payload(b"x" * MAX_INDEXED_FILE_BYTES))
        self.assertFalse(is_indexable_payload(b"x" * (MAX_INDEXED_FILE_BYTES + 1)))
        self.assertFalse(is_indexable_payload(b"\x89PNG\r\n\x1a\n\x00\x00"))
        # Only the first 8 KB are sniffed.
        self.assertTrue(is_indexable_payload(b"a" * BINARY_SNIFF_BYTES + b"\x00"))

    def test_indexed_blob_needs_both_path_and_content_rules(self) -> None:
        text = SimpleNamespace(relative_path="src/app.py", mode="100644", payload=b"x = 1\n")
        binary = SimpleNamespace(relative_path="src/app.py", mode="100644", payload=b"\x00\x01")
        vendored = SimpleNamespace(relative_path="vendor/app.py", mode="100644", payload=b"x = 1\n")
        self.assertTrue(is_indexed_blob(text))
        self.assertFalse(is_indexed_blob(binary))
        self.assertFalse(is_indexed_blob(vendored))

    def test_skips_other_files_lockfiles_and_vendor_trees(self) -> None:
        self.assertFalse(is_included("logo.png", "100644"))
        self.assertFalse(is_included("go.sum", "100644"))
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

    def test_default_chunk_limit_fits_the_embedding_window(self) -> None:
        self.assertLessEqual(MAX_CHUNK_BYTES, 2048)
        payload = b"".join(b"line %04d of a long function body\n" % index for index in range(200))
        symbol = (("function", "f", "", 0), "function", Span(0, len(payload)))
        parts = [chunk for chunk in build_chunks(payload, [symbol], []) if chunk.kind == "symbol_part"]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(part.end_byte - part.start_byte <= MAX_CHUNK_BYTES for part in parts))

    def test_class_keeps_only_its_header_and_methods_are_not_duplicated(self) -> None:
        header = b'class Signer:\n    """Signs values."""\n    digest = "sha1"\n\n'
        sign = b"    def sign(self, value):\n        return value\n\n"
        verify = b"    def verify(self, value):\n        return True\n"
        payload = header + sign + verify
        sign_start = len(header)
        verify_start = sign_start + len(sign)
        chunks = build_chunks(
            payload,
            [
                (("class", "Signer", "", 0), "class", Span(0, len(payload))),
                (("method", "Signer.sign", "", 0), "method", Span(sign_start + 4, verify_start - 1)),
                (("method", "Signer.verify", "", 0), "method", Span(verify_start + 4, len(payload))),
            ],
            [],
        )
        by_symbol = {chunk.symbol_ordinal_key[1]: chunk for chunk in chunks if chunk.symbol_ordinal_key}
        class_text = payload[by_symbol["Signer"].start_byte : by_symbol["Signer"].end_byte]
        self.assertEqual(class_text, b'class Signer:\n    """Signs values."""\n    digest = "sha1"\n')
        self.assertNotIn(b"def sign", class_text)
        self.assertIn(b"def sign", payload[by_symbol["Signer.sign"].start_byte : by_symbol["Signer.sign"].end_byte])
        # Blank lines and indentation between members are not chunks of their own.
        self.assertFalse(any(chunk.kind == "non_code" for chunk in chunks))

    def test_class_without_members_keeps_its_whole_body(self) -> None:
        payload = b"class Config:\n    debug = False\n"
        chunks = build_chunks(payload, [(("class", "Config", "", 0), "class", Span(0, len(payload)))], [])
        self.assertEqual([(chunk.kind, chunk.start_byte, chunk.end_byte) for chunk in chunks], [("symbol_body", 0, len(payload))])

    def test_nested_functions_stay_inside_the_outer_function(self) -> None:
        payload = b"def outer():\n    def inner():\n        return 1\n    return inner()\n"
        inner_start = payload.index(b"def inner")
        inner_end = payload.index(b"    return inner")
        chunks = build_chunks(
            payload,
            [
                (("function", "outer", "", 0), "function", Span(0, len(payload))),
                (("function", "outer.inner", "", 0), "function", Span(inner_start, inner_end)),
            ],
            [],
        )
        outer = next(chunk for chunk in chunks if chunk.symbol_ordinal_key and chunk.symbol_ordinal_key[1] == "outer")
        self.assertEqual((outer.start_byte, outer.end_byte), (0, len(payload)))

    def test_brace_only_gaps_are_not_chunks(self) -> None:
        payload = b"class A {\n  run() {\n    go();\n  }\n}\n\nconst x = 1;\n"
        run_start = payload.index(b"run()")
        run_end = payload.index(b"  }\n}") + 3
        class_end = payload.index(b"}\n\n") + 1
        chunks = build_chunks(
            payload,
            [
                (("class", "A", "", 0), "class", Span(0, class_end)),
                (("method", "A.run", "", 0), "method", Span(run_start, run_end)),
            ],
            [],
        )
        gap_texts = [payload[chunk.start_byte : chunk.end_byte] for chunk in chunks if chunk.kind == "non_code"]
        self.assertEqual(gap_texts, [b"const x = 1;\n"])
        self.assertEqual([chunk.ordinal for chunk in chunks if chunk.kind == "non_code"], [0])

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
