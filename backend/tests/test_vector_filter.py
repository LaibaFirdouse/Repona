import unittest
from types import SimpleNamespace

from app.retrieval.vector_filter import vector_query_filter


class VectorFilterTests(unittest.TestCase):
    def test_small_repo_includes_file_filter(self) -> None:
        generation = SimpleNamespace(
            repository_id="repo-1",
            parser_version="p1",
            chunking_version="c1",
            embedding_config_fingerprint="fp1",
        )
        file_ids = ["f1", "f2"]
        filt = vector_query_filter(generation, file_ids, max_file_ids=256)
        keys = {item["key"] for item in filt["must"]}
        self.assertIn("file_id", keys)
        file_clause = next(item for item in filt["must"] if item["key"] == "file_id")
        self.assertEqual(file_clause["match"]["any"], file_ids)

    def test_large_repo_omits_file_filter(self) -> None:
        generation = SimpleNamespace(
            repository_id="repo-1",
            parser_version="p1",
            chunking_version="c1",
            embedding_config_fingerprint="fp1",
        )
        file_ids = [f"f{i}" for i in range(300)]
        filt = vector_query_filter(generation, file_ids, max_file_ids=256)
        keys = {item["key"] for item in filt["must"]}
        self.assertNotIn("file_id", keys)


if __name__ == "__main__":
    unittest.main()
