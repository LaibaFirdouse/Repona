import subprocess
import tempfile
import unittest
from pathlib import Path

from app.ingestion.materialize import publish_worktree, read_commit_blobs


class MaterializeTests(unittest.TestCase):
    def test_commit_bytes_are_copied_into_the_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "dev@example.com")
            _git(repo, "config", "user.name", "Dev")
            (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            _git(repo, "add", "app.py")
            _git(repo, "commit", "-m", "add app")
            commit = _git(repo, "rev-parse", "HEAD").decode().strip()
            blobs = read_commit_blobs(repo / ".git", commit)
            self.assertEqual(len(blobs), 1)
            self.assertEqual(blobs[0].relative_path, "app.py")
            self.assertEqual(blobs[0].payload, b"def run():\n    return 1\n")
            destination = root / "snapshot"
            publish_worktree(repo / ".git", commit, destination)
            self.assertEqual((destination / "app.py").read_bytes(), blobs[0].payload)


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return completed.stdout


if __name__ == "__main__":
    unittest.main()
