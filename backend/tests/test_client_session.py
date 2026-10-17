import unittest

from app.services.client_session import resolve_client_id, valid_client_id


class ClientSessionTests(unittest.TestCase):
    def test_valid_client_id_accepts_uuid(self) -> None:
        value = "550e8400-e29b-41d4-a716-446655440000"
        self.assertTrue(valid_client_id(value))

    def test_valid_client_id_rejects_tester(self) -> None:
        self.assertFalse(valid_client_id("tester"))

    def test_resolve_prefers_cookie(self) -> None:
        cookie = "550e8400-e29b-41d4-a716-446655440000"
        query = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        self.assertEqual(resolve_client_id(cookie, query), cookie)

    def test_resolve_uses_query_when_no_cookie(self) -> None:
        query = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        self.assertEqual(resolve_client_id(None, query), query)


if __name__ == "__main__":
    unittest.main()
