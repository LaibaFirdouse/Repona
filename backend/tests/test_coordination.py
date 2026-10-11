import unittest
from datetime import datetime, timedelta

from app.ingestion.coordination import (
    classify_git_failure,
    creation_recovery_action,
    gc_may_claim,
    gc_may_delete_worktree,
    pin_may_insert,
    refresh_may_activate,
    serving_action,
)
from app.ingestion.technologies import technologies_for_manifest
from app.ingestion.validation import edge_set_matches, generation_link_is_valid, point_is_valid
from app.storage.identity import point_id


class TechnologyTests(unittest.TestCase):
    def test_manifests_cite_dependency_lines_only(self) -> None:
        requirements = technologies_for_manifest("requirements.txt", b"fastapi==0.1\n# comment\n-r other.txt\n", "file-1")
        self.assertEqual([(row.name, row.evidence_text) for row in requirements], [("fastapi", "fastapi==0.1")])
        package = technologies_for_manifest(
            "package.json",
            b'{"name":"app","dependencies":{"left-pad":"1.0.0"}}\n',
            "file-2",
        )
        self.assertEqual([row.name for row in package], ["left-pad"])
        self.assertNotIn("name", [row.name for row in package])
        pyproject = technologies_for_manifest(
            "pyproject.toml",
            b'[project]\ndependencies = ["sqlalchemy>=2"]\n[tool.poetry.dependencies]\npython = "^3.12"\nrequests = "^2"\n',
            "file-3",
        )
        self.assertEqual({row.name for row in pyproject}, {"sqlalchemy", "requests"})


class CoordinationTests(unittest.TestCase):
    def test_serving_actions(self) -> None:
        self.assertEqual(serving_action("authorized", "abc", "abc"), "serve")
        self.assertEqual(serving_action("authorized", "def", "abc"), "wait")
        self.assertEqual(serving_action("unauthorized", None, "abc"), "revoke")
        self.assertEqual(serving_action("unavailable", None, "abc"), "unavailable")
        self.assertEqual(classify_git_failure(401, False, False), "unauthorized")
        self.assertEqual(classify_git_failure(503, False, False), "unavailable")
        self.assertEqual(classify_git_failure(None, False, True), "unavailable")

    def test_pin_and_gc_rules(self) -> None:
        now = datetime(2026, 10, 11, 12, 0, 0)
        later = now + timedelta(minutes=5)
        deadline = now + timedelta(minutes=10)
        self.assertTrue(pin_may_insert(None, "ready", "/snap", later, deadline))
        self.assertFalse(pin_may_insert(now, "ready", "/snap", later, deadline))
        self.assertFalse(pin_may_insert(None, "creating", "/snap", later, deadline))
        self.assertTrue(
            gc_may_claim("retired", "gen", "other", None, None, "ready", False)
        )
        self.assertFalse(gc_may_claim("ready", "gen", None, None, None, "ready", False))
        self.assertFalse(gc_may_claim("retired", "gen", "gen", None, None, "ready", False))
        self.assertTrue(gc_may_delete_worktree("/snap", False, False, False))
        self.assertFalse(gc_may_delete_worktree("/snap", True, False, False))

    def test_creation_recovery_and_activation_lease(self) -> None:
        self.assertEqual(creation_recovery_action(True, False, True), "publish")
        self.assertEqual(creation_recovery_action(True, True, True), "discard")
        self.assertEqual(creation_recovery_action(False, True, False), "discard")
        now = datetime(2026, 10, 11, 12, 0, 0)
        self.assertTrue(
            refresh_may_activate("running", "gen", "gen", "worker", "worker", 2, 2, now + timedelta(minutes=1), now)
        )
        self.assertFalse(
            refresh_may_activate("running", "gen", "gen", "worker", "worker", 2, 1, now + timedelta(minutes=1), now)
        )
        self.assertFalse(
            refresh_may_activate("running", "gen", "gen", "worker", "worker", 2, 2, now - timedelta(seconds=1), now)
        )

    def test_point_and_edge_checks(self) -> None:
        chunk = "11111111-1111-4111-8111-111111111111"
        span_hash = "ab" * 32
        input_hash = "cd" * 32
        fingerprint = "ef" * 32
        payload = {
            "chunk_id": chunk,
            "repository_id": "repo",
            "file_id": "file",
            "parser_version": "v1",
            "chunking_version": "v1",
            "span_hash": span_hash,
            "embedding_input_hash": input_hash,
            "embedding_config_fingerprint": fingerprint,
        }
        self.assertTrue(
            point_is_valid(
                payload,
                chunk_id_value=chunk,
                repository_id="repo",
                file_id_value="file",
                parser_version="v1",
                chunking_version="v1",
                span_hash=span_hash,
                embedding_input_hash=input_hash,
                fingerprint=fingerprint,
                point_identifier=str(point_id(chunk, input_hash, fingerprint)),
                collection_size=384,
                collection_distance="Cosine",
                expected_size=384,
            )
        )
        self.assertFalse(
            generation_link_is_valid(
                link_exists=True,
                link_fingerprint="nope",
                link_input_hash="cd",
                payload_fingerprint="ef",
                payload_input_hash="cd",
                generation_fingerprint="ef",
                file_linked=True,
            )
        )
        self.assertTrue(edge_set_matches({"a", "b"}, {"b", "a"}))
        self.assertFalse(edge_set_matches({"a"}, {"a", "b"}))


if __name__ == "__main__":
    unittest.main()
