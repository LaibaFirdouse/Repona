import unittest
from unittest.mock import MagicMock

from app.models.repository import Repository
from app.services.repository_catalog import (
    _active_generation,
    _commit_sha_for_generation,
    _index_state,
)


class RepositoryCatalogTests(unittest.TestCase):
    def test_commit_for_missing_generation(self) -> None:
        self.assertIsNone(_commit_sha_for_generation(MagicMock(), None))

    def test_commit_for_missing_revision(self) -> None:
        db = MagicMock()
        db.get.return_value = None
        generation = MagicMock(source_revision_id="rev-1")
        self.assertIsNone(_commit_sha_for_generation(db, generation))

    def test_index_state_without_generation(self) -> None:
        repository = MagicMock(refresh_state="idle")
        self.assertEqual(_index_state(repository, None), "wait")

    def test_active_generation_clears_stale_pointer(self) -> None:
        db = MagicMock()
        db.get.return_value = None
        repository = Repository(
            id="repo-1",
            normalized_url="https://github.com/org/a",
            provider="github",
            tracked_branch="main",
            git_dir="/tmp/git",
            active_generation_id="missing-gen",
        )
        self.assertIsNone(_active_generation(db, repository))
        self.assertIsNone(repository.active_generation_id)


if __name__ == "__main__":
    unittest.main()
