import subprocess
import unittest
from datetime import datetime

from app.ingestion.refresh import question_plan, versions_match
from app.ingestion.secrets import open_token, seal_token
from app.ingestion.upstream import ls_remote, ls_remote_head
from app.schemas.repository import RepositoryCreateRequest
from app.ingestion.versions import (
    CHUNKING_VERSION,
    INCLUSION_RULES_VERSION,
    LEXICAL_INDEX_VERSION,
    PARSER_VERSION,
)


class QuestionPlanTests(unittest.TestCase):
    def test_same_commit_is_served_while_a_rebuild_lease_is_held(self) -> None:
        answer, build = question_plan("authorized", "a" * 40, "a" * 40, True, False, True, "running", True)
        self.assertEqual((answer, build), ("serve", "none"))

    def test_same_commit_and_versions_do_not_start_a_build(self) -> None:
        answer, build = question_plan("authorized", "a" * 40, "a" * 40, True, True, True, "idle", False)
        self.assertEqual((answer, build), ("serve", "none"))

    def test_new_commit_waits_and_starts_when_idle(self) -> None:
        answer, build = question_plan("authorized", "b" * 40, "a" * 40, True, True, True, "idle", False)
        self.assertEqual((answer, build), ("wait", "start"))

    def test_new_commit_does_not_start_a_second_refresh(self) -> None:
        answer, build = question_plan("authorized", "b" * 40, "a" * 40, True, True, True, "running", True)
        self.assertEqual((answer, build), ("wait", "none"))

    def test_expired_lease_is_adopted_for_the_same_generation(self) -> None:
        answer, build = question_plan("authorized", "b" * 40, "a" * 40, True, True, True, "running", False)
        self.assertEqual((answer, build), ("wait", "adopt"))

    def test_unauthorized_revokes_and_does_not_build(self) -> None:
        self.assertEqual(question_plan("unauthorized", None, "a" * 40, True, True, True, "idle", False), ("revoke", "none"))

    def test_unavailable_does_not_revoke_or_build(self) -> None:
        self.assertEqual(question_plan("unavailable", None, "a" * 40, True, True, True, "idle", False), ("unavailable", "none"))

    def test_invalid_work_tree_is_not_served(self) -> None:
        answer, build = question_plan("authorized", "a" * 40, "a" * 40, True, True, False, "idle", False)
        self.assertEqual((answer, build), ("wait", "start"))

    def test_versions_match_requires_every_indexing_version(self) -> None:
        generation = type(
            "Generation",
            (),
            {
                "status": "ready",
                "gc_claimed_at": None,
                "parser_version": PARSER_VERSION,
                "chunking_version": CHUNKING_VERSION,
                "lexical_index_version": LEXICAL_INDEX_VERSION,
                "inclusion_rules_version": INCLUSION_RULES_VERSION,
                "embedding_config_fingerprint": "fp",
            },
        )()
        self.assertTrue(versions_match(generation, "fp"))
        generation.chunking_version = "v2"
        self.assertFalse(versions_match(generation, "fp"))
        generation.chunking_version = CHUNKING_VERSION
        generation.gc_claimed_at = datetime.utcnow()
        self.assertFalse(versions_match(generation, "fp"))


class UpstreamTests(unittest.TestCase):
    def test_token_is_passed_only_as_a_request_header(self) -> None:
        seen = []

        def runner(command, check, capture_output, timeout):
            seen.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"ab" * 20 + b"\trefs/heads/dev\n", stderr=b"")

        probe = ls_remote("https://github.com/org/repo", "dev", "secret-token", runner=runner)
        self.assertEqual(probe.upstream, "authorized")
        self.assertEqual(probe.sha, "ab" * 20)
        rendered = " ".join(seen[0])
        self.assertNotIn("secret-token@", rendered)
        self.assertIn("https://github.com/org/repo", seen[0])
        self.assertIn("credential.helper=", seen[0])
        self.assertTrue(any(part.startswith("http.extraHeader=Authorization: Bearer ") for part in seen[0]))

    def test_public_url_is_fetched_without_a_token(self) -> None:
        seen = []

        def runner(command, check, capture_output, timeout):
            seen.append(command)
            stdout = b"ref: refs/heads/main\tHEAD\n" + b"cd" * 20 + b"\tHEAD\n"
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

        probe = ls_remote_head("https://github.com/org/repo", "", runner=runner)
        self.assertEqual(probe.upstream, "authorized")
        self.assertEqual(probe.branch, "main")
        self.assertEqual(probe.sha, "cd" * 20)
        self.assertFalse(any("Authorization" in part for part in seen[0]))
        self.assertIn("--symref", seen[0])

    def test_fetch_takes_only_the_current_commit(self) -> None:
        from pathlib import Path
        import tempfile

        from app.ingestion.upstream import fetch_branch

        seen = []

        def runner(command, check, capture_output, timeout):
            seen.append(command)
            if command[:2] == ["git", "init"]:
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            if "rev-parse" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"ab" * 20 + b"\n", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            sha = fetch_branch(Path(directory), "https://github.com/org/repo", "canary", "", runner=runner)
        self.assertEqual(sha, "ab" * 20)
        fetch = next(command for command in seen if "fetch" in command)
        self.assertIn("--depth", fetch)
        self.assertIn("1", fetch)
        self.assertFalse(any("Authorization" in part for part in fetch))

    def test_missing_public_repository_is_unauthorized(self) -> None:
        def runner(command, check, capture_output, timeout):
            return subprocess.CompletedProcess(
                command,
                128,
                stdout=b"",
                stderr=b"fatal: could not read Username for 'https://github.com': terminal prompts disabled",
            )

        probe = ls_remote("https://github.com/org/private", "main", "", runner=runner)
        self.assertEqual(probe.upstream, "unauthorized")

    def test_authentication_failure_is_unauthorized(self) -> None:
        def runner(command, check, capture_output, timeout):
            return subprocess.CompletedProcess(command, 128, stdout=b"", stderr=b"Authentication failed")

        probe = ls_remote("https://github.com/org/repo", "dev", "secret-token", runner=runner)
        self.assertEqual(probe.upstream, "unauthorized")
        self.assertIsNone(probe.sha)

    def test_timeout_is_unavailable(self) -> None:
        def runner(command, check, capture_output, timeout):
            raise subprocess.TimeoutExpired(command, timeout)

        probe = ls_remote("https://github.com/org/repo", "dev", "secret-token", runner=runner)
        self.assertEqual((probe.upstream, probe.sha), ("unavailable", None))


class PublicRepositoryRequestTests(unittest.TestCase):
    def test_a_public_github_url_does_not_require_a_token_or_branch(self) -> None:
        request = RepositoryCreateRequest(
            repo_url="https://github.com/pallets/itsdangerous",
            user_id="local-user",
        )
        self.assertIsNone(request.access_token)
        self.assertIsNone(request.tracked_branch)


class SecretTests(unittest.TestCase):
    def test_sealed_token_round_trips(self) -> None:
        sealed = seal_token("ghp_example", "test-key")
        self.assertNotIn("ghp_example", sealed)
        self.assertEqual(open_token(sealed, "test-key"), "ghp_example")


if __name__ == "__main__":
    unittest.main()
