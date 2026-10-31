import unittest

import app.models  # noqa: F401
from app.db.base import Base


REQUIRED_TABLES = {
    "users",
    "repository_members",
    "repository_credentials",
    "repositories",
    "source_revisions",
    "index_generations",
    "directories",
    "files",
    "symbols",
    "chunks",
    "generation_files",
    "generation_directories",
    "generation_chunks",
    "detected_technologies",
    "generation_pins",
}


class StorageSchemaTests(unittest.TestCase):
    def test_catalog_tables_exist(self) -> None:
        self.assertTrue(REQUIRED_TABLES <= set(Base.metadata.tables))

    def test_generation_attempt_is_unique(self) -> None:
        table = Base.metadata.tables["index_generations"]
        unique_names = {constraint.name for constraint in table.constraints}
        self.assertIn("uq_index_generations_attempt", unique_names)
        self.assertIn("uq_index_generations_pin_target", unique_names)

    def test_chunk_rows_do_not_store_bodies_or_vectors(self) -> None:
        columns = set(Base.metadata.tables["chunks"].columns.keys())
        self.assertNotIn("content", columns)
        self.assertNotIn("embedding", columns)
        self.assertNotIn("search_vector", columns)
        link_columns = set(Base.metadata.tables["generation_chunks"].columns.keys())
        self.assertIn("search_vector", link_columns)
        self.assertIn("embedding_input_hash", link_columns)

    def test_active_generation_is_the_only_serving_pointer(self) -> None:
        columns = set(Base.metadata.tables["repositories"].columns.keys())
        self.assertIn("active_generation_id", columns)
        self.assertNotIn("is_active", columns)
        self.assertIn("normalized_url", columns)

    def test_index_generation_stores_capability_report(self) -> None:
        columns = set(Base.metadata.tables["index_generations"].columns.keys())
        self.assertIn("capability_report_json", columns)


if __name__ == "__main__":
    unittest.main()
