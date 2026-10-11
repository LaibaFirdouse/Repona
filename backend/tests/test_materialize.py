import subprocess
import tempfile
import unittest
from pathlib import Path

from app.ingestion.materialize import publish_worktree, read_commit_blobs
from app.ingestion.validation import validate_worktree_bytes


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
            (repo / "note.txt").write_bytes(b"a\nb\n\0c\n")
            _git(repo, "add", "app.py", "note.txt")
            _git(repo, "commit", "-m", "add app")
            commit = _git(repo, "rev-parse", "HEAD").decode().strip()
            blobs = read_commit_blobs(repo / ".git", commit)
            by_path = {blob.relative_path: blob.payload for blob in blobs}
            self.assertEqual(set(by_path), {"app.py", "note.txt"})
            self.assertEqual(by_path["app.py"], b"def run():\n    return 1\n")
            self.assertEqual(by_path["note.txt"], b"a\nb\n\0c\n")
            destination = root / "snapshot"
            publish_worktree(repo / ".git", commit, destination)
            self.assertEqual((destination / "app.py").read_bytes(), by_path["app.py"])
            self.assertEqual((destination / "note.txt").read_bytes(), by_path["note.txt"])
            validate_worktree_bytes(repo / ".git", commit, destination, blobs)


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return completed.stdout


if __name__ == "__main__":
    unittest.main()
