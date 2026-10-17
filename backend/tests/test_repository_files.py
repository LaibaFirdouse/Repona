import unittest

from app.services.repository_files import _safe_relative_path, RepositoryFilesError


class SafePathTests(unittest.TestCase):
    def test_rejects_traversal(self) -> None:
        with self.assertRaises(RepositoryFilesError):
            _safe_relative_path("../etc/passwd")

    def test_normalizes_leading_slash(self) -> None:
        self.assertEqual(_safe_relative_path("/src/main.py"), "src/main.py")


if __name__ == "__main__":
    unittest.main()
