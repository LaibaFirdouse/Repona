import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.core.config import PROJECT_ROOT
from app.ingestion.host_paths import canonical_git_dir, repair_repository_data_paths, reset_expired_refresh
from app.models.repository import Repository


def _empty_revision_query() -> MagicMock:
    query = MagicMock()
    query.filter.return_value.all.return_value = []
    return query


class HostPathTests(unittest.TestCase):
    def test_repair_git_dir_from_foreign_path(self) -> None:
        repo = Repository(
            id="883913fc-f20f-5a89-b6c8-19a7f56e7e1d",
            normalized_url="https://github.com/org/repo",
            provider="github",
            tracked_branch="main",
            git_dir="/home/dev/other/data/git/883913fc-f20f-5a89-b6c8-19a7f56e7e1d",
        )
        db = MagicMock()
        db.query.return_value = _empty_revision_query()
        changed = repair_repository_data_paths(db, repo)
        self.assertTrue(changed)
        self.assertEqual(repo.git_dir, str(canonical_git_dir(repo.id)))

    def test_reset_expired_refresh(self) -> None:
        now = datetime(2026, 10, 17, 12, 0, 0)
        repo = Repository(
            id="r1",
            normalized_url="https://github.com/org/a",
            provider="github",
            tracked_branch="main",
            git_dir=str(PROJECT_ROOT / "data" / "git" / "r1"),
            refresh_state="running",
            refresh_owner="tester",
            refresh_lease_expires_at=now + timedelta(minutes=10),
            refresh_heartbeat_at=now - timedelta(minutes=5),
        )
        db = MagicMock()
        db.query.return_value = _empty_revision_query()
        self.assertTrue(reset_expired_refresh(db, repo, now=now))
        self.assertEqual(repo.refresh_state, "idle")


if __name__ == "__main__":
    unittest.main()
