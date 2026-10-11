import unittest
from uuid import UUID

from app.storage.identity import (
    NAMESPACE,
    chunk_id,
    collection_name,
    content_hash,
    edge_id,
    embedding_config_fingerprint,
    file_id,
    frame_text,
    lexical_input,
    line_count,
    line_labels,
    line_spans,
    normalize_repository_url,
    repository_id,
    symbol_id,
)


class IdentityTests(unittest.TestCase):
    def test_namespace_is_uuid5_of_the_dns_namespace(self) -> None:
        self.assertEqual(NAMESPACE.version, 5)
        self.assertIsInstance(NAMESPACE, UUID)

    def test_shifted_text_boundaries_do_not_collide(self) -> None:
        left = frame_text("ab") + frame_text("c")
        right = frame_text("a") + frame_text("bc")
        self.assertNotEqual(left, right)

    def test_empty_text_frame_is_still_written(self) -> None:
        self.assertEqual(frame_text(""), bytes([0x01, 0, 0, 0, 0]))

    def test_repository_url_normalization(self) -> None:
        expected = "https://github.com/org/repo"
        self.assertEqual(normalize_repository_url("git@github.com:org/repo.git"), expected)
        self.assertEqual(normalize_repository_url("https://github.com/org/repo/"), expected)
        self.assertEqual(
            normalize_repository_url("https://user:token@GitHub.com/org/repo.git?x=1#frag"),
            expected,
        )

    def test_other_schemes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_repository_url("http://github.com/org/repo")

    def test_same_natural_key_reproduces_the_id(self) -> None:
        url = normalize_repository_url("https://github.com/org/repo.git")
        self.assertEqual(repository_id(url), repository_id(url))
        payload = b"abc\n"
        digest = content_hash(payload)
        repo = repository_id(url)
        self.assertEqual(file_id(repo, "a.py", digest), file_id(repo, "a.py", digest))

    def test_symbol_anchor_differs_from_file_anchor(self) -> None:
        repo = repository_id("https://github.com/org/repo")
        file = file_id(repo, "a.py", content_hash(b"abc"))
        span = content_hash(b"abc")
        symbol = symbol_id(file, "v1", "function", "read", "", 0)
        anchored = chunk_id("symbol", symbol, file, "v1", "v1", "symbol_body", 0, span)
        file_anchored = chunk_id("file", None, file, "v1", "v1", "symbol_body", 0, span)
        self.assertNotEqual(anchored, file_anchored)

    def test_line_rules(self) -> None:
        self.assertEqual(line_count(b""), 0)
        self.assertEqual(line_labels(b"", 0, 0), (None, None))
        abc = b"abc"
        self.assertEqual((line_spans(abc)[0].start_byte, line_spans(abc)[0].end_byte), (0, 3))
        self.assertEqual(line_count(abc), 1)
        ended = b"abc\n"
        self.assertEqual(line_count(ended), 1)
        self.assertEqual(
            (line_spans(ended)[0].start_byte, line_spans(ended)[0].end_byte),
            (0, 4),
        )
        self.assertEqual(line_labels(ended, 0, 3), (1, 1))
        self.assertEqual(line_labels(ended, 0, 4), (1, 1))
        self.assertEqual(line_count(b"abc\n\n"), 2)

    def test_lexical_input_replaces_invalid_utf8(self) -> None:
        encoded = lexical_input("pkg.read", b"a\xffb")
        self.assertEqual(encoded, "pkg.read\na\ufffdb".encode("utf-8"))

    def test_fingerprint_changes_when_model_revision_changes(self) -> None:
        first = embedding_config_fingerprint("all-MiniLM-L6-v2", "unversioned", 384)
        second = embedding_config_fingerprint("all-MiniLM-L6-v2", "abc", 384)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_collection_name_uses_a_model_hash(self) -> None:
        name = collection_name("all-MiniLM-L6-v2", 384)
        self.assertTrue(name.startswith("code_chunks__"))
        self.assertTrue(name.endswith("__384__cosine"))

    def test_edge_id_ignores_call_site_and_keeps_empty_frames(self) -> None:
        generation = repository_id("https://github.com/org/repo")
        source = repository_id("https://github.com/org/other")
        target = repository_id("https://github.com/org/third")
        once = edge_id("CALLS", generation, source, target, "read", "resolved")
        again = edge_id("CALLS", generation, source, target, "read", "resolved")
        other = edge_id("CALLS", generation, source, target, "write", "resolved")
        self.assertEqual(once, again)
        self.assertNotEqual(once, other)


if __name__ == "__main__":
    unittest.main()
