"""A question serves the active index: no remote probe for public repos, no re-index, no full re-read."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.repository import Repository
from app.services.refresh_service import RefreshError, RefreshService

MODULE = "app.services.refresh_service"


def _repository(**overrides):
    values = dict(
        id="repo-1",
        normalized_url="https://github.com/org/repo",
        tracked_branch="main",
        git_dir="data/git/repo-1",
        refresh_state="idle",
        refresh_lease_expires_at=None,
        refresh_heartbeat_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _generation(**overrides):
    values = dict(id="gen-1", status="ready", gc_claimed_at=None, source_revision_id="rev-1")
    values.update(overrides)
    return SimpleNamespace(**values)


def _revision(**overrides):
    values = dict(id="rev-1", commit_sha="a" * 40, snapshot_state="ready", snapshot_path="/data/snapshots/rev-1")
    values.update(overrides)
    return SimpleNamespace(**values)


def _db(repository, credential=None):
    db = MagicMock()
    db.get.side_effect = lambda model, key: repository if model is Repository else None
    db.query.return_value.filter.return_value.one_or_none.return_value = credential
    return db


class AskServesTheActiveIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = MagicMock()
        self.service = RefreshService(probe=self.probe, credential_key="credential-key")
        self.member = patch.object(RefreshService, "_member", return_value=SimpleNamespace(status="active"))
        self.repair = patch(f"{MODULE}.repair_repository_data_paths", return_value=False)
        self.pin = patch(f"{MODULE}.insert_reader_pin", return_value=SimpleNamespace(id="pin-1"))
        self.member.start()
        self.repair.start()
        self.insert_pin = self.pin.start()
        self.addCleanup(patch.stopall)

    def test_public_repository_is_served_without_probe_or_reindex(self) -> None:
        repository = _repository()
        with patch.object(self.service, "_active", return_value=(_generation(), _revision())), patch.object(
            self.service, "_run"
        ) as run, patch(f"{MODULE}._source_is_valid") as full_validation:
            outcome = self.service.ask(_db(repository), "repo-1", "user-1")
        self.assertEqual(outcome.action, "serve")
        self.assertEqual(outcome.pin_id, "pin-1")
        self.assertEqual(outcome.generation_id, "gen-1")
        self.assertEqual(outcome.commit_sha, "a" * 40)
        self.probe.assert_not_called()
        run.assert_not_called()
        full_validation.assert_not_called()

    def test_a_running_first_index_answers_wait(self) -> None:
        repository = _repository(refresh_state="running")
        with patch.object(self.service, "_active", return_value=(None, None)):
            outcome = self.service.ask(_db(repository), "repo-1", "user-1")
        self.assertEqual(outcome.action, "wait")
        self.assertIn("still being indexed", outcome.message)
        self.insert_pin.assert_not_called()

    def test_an_unindexed_repository_asks_for_an_index(self) -> None:
        with patch.object(self.service, "_active", return_value=(None, None)):
            outcome = self.service.ask(_db(_repository()), "repo-1", "user-1")
        self.assertEqual(outcome.action, "wait")
        self.assertIn("Index it first", outcome.message)

    def test_a_snapshot_that_is_not_ready_is_not_served(self) -> None:
        revision = _revision(snapshot_state="creating")
        with patch.object(self.service, "_active", return_value=(_generation(), revision)):
            outcome = self.service.ask(_db(_repository()), "repo-1", "user-1")
        self.assertEqual(outcome.action, "wait")
        self.insert_pin.assert_not_called()

    def test_private_repository_checks_access_and_revokes_on_unauthorized(self) -> None:
        self.probe.return_value = SimpleNamespace(upstream="unauthorized", sha=None)
        credential = SimpleNamespace(token_ciphertext="sealed")
        with patch(f"{MODULE}.open_token", return_value="token-value"), patch.object(
            self.service, "_revoke"
        ) as revoke, patch.object(self.service, "_active", return_value=(_generation(), _revision())):
            with self.assertRaises(RefreshError) as raised:
                self.service.ask(_db(_repository(), credential), "repo-1", "user-1")
        self.assertEqual(raised.exception.status_code, 403)
        revoke.assert_called_once()
        self.assertEqual(self.probe.call_args.args[2], "token-value")
        self.insert_pin.assert_not_called()

    def test_private_repository_with_access_is_served(self) -> None:
        self.probe.return_value = SimpleNamespace(upstream="authorized", sha="b" * 40)
        credential = SimpleNamespace(token_ciphertext="sealed")
        with patch(f"{MODULE}.open_token", return_value="token-value"), patch.object(
            self.service, "_active", return_value=(_generation(), _revision())
        ):
            outcome = self.service.ask(_db(_repository(), credential), "repo-1", "user-1")
        # A newer upstream commit does not stop the indexed one from being served.
        self.assertEqual(outcome.action, "serve")
        self.assertEqual(outcome.commit_sha, "a" * 40)


class IndexValidatesTheSnapshotOnceTests(unittest.TestCase):
    def _apply(self, plan, revisions):
        service = RefreshService(probe=MagicMock(), credential_key="credential-key")
        with patch(f"{MODULE}._source_is_valid", return_value=True) as full_validation, patch(
            f"{MODULE}.question_plan", return_value=plan
        ), patch(f"{MODULE}.versions_match", return_value=True), patch(
            f"{MODULE}.refresh_lease_held", return_value=False
        ), patch(
            f"{MODULE}.insert_reader_pin", return_value=SimpleNamespace(id="pin-1")
        ), patch.object(
            service, "_active", side_effect=[(_generation(), revision) for revision in revisions]
        ), patch.object(service, "_run"):
            outcome = service._apply(MagicMock(), _repository(), "user-1", "", "a" * 40)
        return outcome, full_validation.call_count

    def test_an_unchanged_ready_commit_is_validated_once(self) -> None:
        outcome, validations = self._apply(("serve", "none"), [_revision(), _revision()])
        self.assertEqual(outcome.action, "serve")
        self.assertEqual(validations, 1)

    def test_a_new_build_is_validated_again(self) -> None:
        outcome, validations = self._apply(("wait", "start"), [_revision(id="rev-0"), _revision(id="rev-1")])
        self.assertEqual(outcome.action, "serve")
        self.assertEqual(validations, 2)


if __name__ == "__main__":
    unittest.main()
