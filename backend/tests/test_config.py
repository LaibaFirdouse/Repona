import unittest

from app.core.config import normalize_database_url


class DatabaseUrlNormalizationTests(unittest.TestCase):
    def test_neon_postgresql_url_uses_psycopg_driver(self) -> None:
        url = "postgresql://user:pass@host.example/neondb?sslmode=require"
        self.assertEqual(
            normalize_database_url(url),
            "postgresql+psycopg://user:pass@host.example/neondb?sslmode=require",
        )

    def test_postgres_scheme_is_normalized(self) -> None:
        self.assertTrue(
            normalize_database_url("postgres://localhost/db").startswith("postgresql+psycopg://")
        )


if __name__ == "__main__":
    unittest.main()
