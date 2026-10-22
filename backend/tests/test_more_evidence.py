import unittest

from app.retrieval.more_evidence import more_evidence_for_audit_recovery


class MoreEvidenceAuditRecoveryTests(unittest.TestCase):
    def test_recovery_payload_includes_question_lexical_and_paths(self) -> None:
        loaded = [
            {"relative_path": "app/layout.tsx", "chunk_kind": "symbol_part", "raw_bytes": 40},
            {"relative_path": "jest.config.ts", "chunk_kind": "symbol_part", "raw_bytes": 30},
        ]
        payload = more_evidence_for_audit_recovery("How does the main entry point work?", loaded)
        self.assertTrue(payload["lexical_queries"])
        paths = payload["path_contains"]
        self.assertTrue(any("layout" in item for item in paths))
        self.assertFalse(any("jest.config" in item for item in paths))


if __name__ == "__main__":
    unittest.main()
