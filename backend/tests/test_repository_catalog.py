import unittest
from unittest.mock import MagicMock

from app.services.repository_catalog import _commit_for_generation, _index_state


class RepositoryCatalogTests(unittest.TestCase):
    def test_commit_for_missing_generation(self) -> None:
        self.assertIsNone(_commit_for_generation(MagicMock(), None))

    def test_commit_for_missing_revision(self) -> None:
        db = MagicMock()
        db.get.return_value = None
        generation = MagicMock(source_revision_id="rev-1")
        self.assertIsNone(_commit_for_generation(db, generation))
        db.get.assert_called_once()

    def test_index_state_without_generation(self) -> None:
        repository = MagicMock(refresh_state="idle")
        self.assertEqual(_index_state(repository, None), "wait")


if __name__ == "__main__":
    unittest.main()
