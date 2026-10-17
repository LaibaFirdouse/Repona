import unittest
from types import SimpleNamespace

from app.services.repository_catalog import _index_state


class RepositoryCatalogTests(unittest.TestCase):
    def test_index_state_ready(self) -> None:
        repo = SimpleNamespace(refresh_state="idle")
        gen = SimpleNamespace(status="ready")
        self.assertEqual(_index_state(repo, gen), "ready")

    def test_index_state_indexing_when_refresh_running(self) -> None:
        repo = SimpleNamespace(refresh_state="running")
        gen = SimpleNamespace(status="ready")
        self.assertEqual(_index_state(repo, gen), "indexing")


if __name__ == "__main__":
    unittest.main()
