import unittest

from app.retrieval.packet import dedupe_citations_by_path


class CitationDedupeTests(unittest.TestCase):
    def test_merges_same_path_line_ranges(self) -> None:
        raw = [
            {"path": "main.go", "start_line": 29, "end_line": 95, "chunk_id": "a"},
            {"path": "main.go", "start_line": 111, "end_line": 116, "chunk_id": "b"},
        ]
        out = dedupe_citations_by_path(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start_line"], 29)
        self.assertEqual(out[0]["end_line"], 116)


if __name__ == "__main__":
    unittest.main()
