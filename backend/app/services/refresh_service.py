"""Run the live-refresh rules for indexing and for a question.

The question does not choose spans or call the model. That is the retrieval phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.ingestion.claims import insert_reader_pin
from app.ingestion.coordination import lease_deadline
from app.ingestion.materialize import read_commit_blobs
from app.ingestion.persist import GenerationWriteError
from app.ingestion.pipeline import complete_owned_refresh, ingest_commit
from app.ingestion.refresh import question_plan, versions_match
from app.ingestion.secrets import open_token, seal_token
from app.ingestion.upstream import fetch_branch, ls_remote, ls_remote_head
from app.ingestion.validation import ChecklistError, validate_worktree_bytes
from app.models.catalog import IndexGeneration, SourceRevision
from app.models.repository import Repository, RepositoryCredential, RepositoryMember, User
from app.storage.identity import IdentityError, embedding_config_fingerprint, normalize_repository_url, repository_id
from urllib.parse import urlsplit


EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_DIMENSIONS = 384
MODEL_REVISION = "unversioned"


class RefreshError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RefreshOutcome:
    action: str
    repository_id: str
    normalized_url: str
    tracked_branch: str
    commit_sha: str | None
    generation_id: str | None
    message: str
    pin_id: str | None = None


class RefreshService:
    def __init__(
        self,
        probe=None,
        probe_head=None,
        fetch=None,
        ingest=None,
        complete=None,
        embed=None,
        graph_session_factory=None,
        qdrant_url: str | None = None,
        credential_key: str | None = None,
        snapshot_root: Path | None = None,
    ) -> None:
        self.probe = probe or ls_remote
        self.probe_head = probe_head or ls_remote_head
        self.fetch = fetch or fetch_branch
        self.ingest = ingest or ingest_commit
        self.complete = complete or complete_owned_refresh
        self.embed = embed or _embed
        self.graph_session_factory = graph_session_factory
        self.qdrant_url = settings.qdrant_url if qdrant_url is None else qdrant_url
        self.credential_key = settings.credential_key if credential_key is None else credential_key
        self.snapshot_root = snapshot_root or (PROJECT_ROOT / "data" / "snapshots")

    def index(
        self,
        db: Session,
        repo_url: str,
        tracked_branch: str | None,
        user_id: str,
        access_token: str | None = None,
    ) -> RefreshOutcome:
        token = (access_token or "").strip()
        try:
            normalized = normalize_repository_url(repo_url)
        except IdentityError as error:
            raise RefreshError(400, "the repository URL must be https") from error
        requested_branch = (tracked_branch or "").strip()
        if requested_branch:
            result = self.probe(normalized, requested_branch, token)
            branch = requested_branch
        else:
            result = self.probe_head(normalized, token)
            branch = result.branch or ""
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
        if result.upstream == "unauthorized":
            if repository is not None:
                self._revoke(db, user_id, repository.id)
            if token:
                raise RefreshError(403, "the access token cannot read this repository")
            raise RefreshError(403, "this repository is not public")
        if result.upstream != "authorized" or not result.sha:
            raise RefreshError(503, "the repository host is unavailable")
        if not branch:
            raise RefreshError(400, "this repository has no default branch")
        if repository is not None and repository.tracked_branch != branch:
            raise RefreshError(400, "this repository is tracked on a different branch")
        repository = self._join(db, normalized, branch, user_id, token)
        return self._apply(db, repository, user_id, token, result.sha)

    def ask(self, db: Session, repository_id_value: str, user_id: str) -> RefreshOutcome:
        repository = db.get(Repository, repository_id_value)
        if repository is None:
            raise RefreshError(404, "repository was not found")
        member = self._member(db, user_id, repository.id)
        if member is None or member.status != "active":
            raise RefreshError(403, "this user is not an active member")
        credential = (
            db.query(RepositoryCredential)
            .filter(
                RepositoryCredential.user_id == user_id,
                RepositoryCredential.repository_id == repository.id,
            )
            .one_or_none()
        )
        token = "" if credential is None else open_token(credential.token_ciphertext, self._key())
        result = self.probe(repository.normalized_url, repository.tracked_branch, token)
        if result.upstream == "unauthorized":
            self._revoke(db, user_id, repository.id)
            if token:
                raise RefreshError(403, "the access token cannot read this repository")
            raise RefreshError(403, "this repository is not public")
        if result.upstream != "authorized":
            raise RefreshError(503, "the repository host is unavailable")
        return self._apply(db, repository, user_id, token, result.sha)

    def _apply(self, db: Session, repository: Repository, user_id: str, token: str, remote_sha: str) -> RefreshOutcome:
        fingerprint = embedding_config_fingerprint(EMBEDDING_MODEL, MODEL_REVISION, VECTOR_DIMENSIONS)
        generation, revision = self._active(db, repository)
        git_dir = _absolute(repository.git_dir)
        source_valid = _source_is_valid(git_dir, revision)
        now = datetime.utcnow()
        lease_held = bool(repository.refresh_lease_expires_at and repository.refresh_lease_expires_at > now)
        answer, build = question_plan(
            "authorized",
            remote_sha,
            None if revision is None else revision.commit_sha,
            generation is not None and generation.status == "ready" and generation.gc_claimed_at is None,
            versions_match(generation, fingerprint),
            source_valid,
            repository.refresh_state,
            lease_held,
        )
        if build == "start":
            self._run(db, repository, user_id, token, remote_sha, resume=False)
        elif build == "adopt":
            self._run(db, repository, user_id, token, remote_sha, resume=True)
        db.refresh(repository)
        generation, revision = self._active(db, repository)
        pinned_at = datetime.utcnow()
        if revision is not None and revision.commit_sha == remote_sha and generation is not None and generation.status == "ready":
            if not _source_is_valid(git_dir, revision):
                return self._outcome(repository, "wait", remote_sha, None, "The published work tree does not match the commit.")
            pin = insert_reader_pin(
                db,
                repository_id=repository.id,
                generation_id=generation.id,
                source_revision_id_value=revision.id,
                expires_at=pinned_at + timedelta(minutes=2),
                hard_deadline=lease_deadline(pinned_at),
            )
            if pin is None:
                return self._outcome(repository, "wait", remote_sha, None, "The active snapshot cannot be pinned.")
            db.commit()
            return self._outcome(
                repository,
                "serve",
                revision.commit_sha,
                generation.id,
                "The active generation matches this commit.",
                pin.id,
            )
        if answer == "serve" and generation is not None and revision is not None:
            return self._outcome(repository, "wait", remote_sha, None, "The active generation changed before it could be pinned.")
        return self._outcome(
            repository,
            "wait",
            remote_sha,
            None,
            "A newer commit is being indexed. The previous generation is not being served.",
        )

    def _run(self, db: Session, repository: Repository, user_id: str, token: str, remote_sha: str, resume: bool) -> None:
        git_dir = _absolute(repository.git_dir)
        fetched = self.fetch(git_dir, repository.normalized_url, repository.tracked_branch, token)
        if fetched != remote_sha:
            raise RefreshError(503, "the fetched commit does not match the branch head")

        def run(session) -> None:
            arguments = dict(
                db=db,
                repository_id=repository.id,
                owner=user_id,
                embed=self.embed,
                embedding_model=EMBEDDING_MODEL,
                vector_dimensions=VECTOR_DIMENSIONS,
                snapshot_root=self.snapshot_root,
                qdrant_url=self.qdrant_url,
                graph_session=session,
            )
            try:
                if resume:
                    from app.ingestion.pipeline import adopt_expired_refresh

                    adopt_expired_refresh(db, repository.id, user_id)
                    self.complete(git_dir=git_dir, **arguments)
                else:
                    self.ingest(git_dir=git_dir, commit_sha=remote_sha, model_revision=MODEL_REVISION, **arguments)
            except GenerationWriteError:
                return
            except ChecklistError as error:
                raise RefreshError(500, str(error)) from error

        self._with_graph(run)

    def _with_graph(self, run) -> None:
        if self.graph_session_factory is not None:
            session = self.graph_session_factory()
            try:
                run(session)
            finally:
                close = getattr(session, "close", None)
                if close is not None:
                    close()
            return
        if not settings.neo4j_password:
            raise RefreshError(503, "neo4j is not configured")
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        try:
            with driver.session() as session:
                run(session)
        finally:
            driver.close()

    def _join(self, db: Session, normalized: str, tracked_branch: str, user_id: str, access_token: str) -> Repository:
        user = db.get(User, user_id)
        if user is None:
            user = User(id=user_id)
            db.add(user)
            db.flush()
        repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
        if repository is None:
            host = urlsplit(normalized).hostname or ""
            repo_uuid = repository_id(normalized)
            repository = Repository(
                id=str(repo_uuid),
                normalized_url=normalized,
                provider="gitlab" if "gitlab" in host else "github",
                tracked_branch=tracked_branch,
                git_dir=str(PROJECT_ROOT / "data" / "git" / str(repo_uuid)),
            )
            db.add(repository)
            db.flush()
        member = self._member(db, user_id, repository.id)
        if member is None:
            existing_member = (
                db.query(RepositoryMember)
                .filter(RepositoryMember.repository_id == repository.id)
                .first()
            )
            db.add(
                RepositoryMember(
                    user_id=user_id,
                    repository_id=repository.id,
                    role="reader" if existing_member is not None else "owner",
                    status="active",
                )
            )
        else:
            member.status = "active"
            member.revoked_at = None
        credential = (
            db.query(RepositoryCredential)
            .filter(
                RepositoryCredential.user_id == user_id,
                RepositoryCredential.repository_id == repository.id,
            )
            .one_or_none()
        )
        if access_token:
            sealed = seal_token(access_token, self._key())
            if credential is None:
                db.add(
                    RepositoryCredential(
                        user_id=user_id,
                        repository_id=repository.id,
                        provider=repository.provider,
                        token_ciphertext=sealed,
                    )
                )
            else:
                credential.token_ciphertext = sealed
        elif credential is not None:
            db.delete(credential)
        db.commit()
        db.refresh(repository)
        return repository

    def _revoke(self, db: Session, user_id: str, repository_id_value: str) -> None:
        member = self._member(db, user_id, repository_id_value)
        if member is None or member.status == "revoked":
            return
        member.status = "revoked"
        member.revoked_at = datetime.utcnow()
        db.commit()

    def _member(self, db: Session, user_id: str, repository_id_value: str) -> RepositoryMember | None:
        return (
            db.query(RepositoryMember)
            .filter(
                RepositoryMember.user_id == user_id,
                RepositoryMember.repository_id == repository_id_value,
            )
            .one_or_none()
        )

    def _active(self, db: Session, repository: Repository):
        if repository.active_generation_id is None:
            return None, None
        generation = db.get(IndexGeneration, repository.active_generation_id)
        if generation is None:
            return None, None
        revision = db.get(SourceRevision, generation.source_revision_id)
        return generation, revision

    def _key(self) -> str:
        if not self.credential_key:
            raise RefreshError(500, "CREDENTIAL_KEY is not configured")
        return self.credential_key

    def _outcome(
        self,
        repository: Repository,
        action: str,
        commit_sha: str | None,
        generation_id: str | None,
        message: str,
        pin_id: str | None = None,
    ) -> RefreshOutcome:
        return RefreshOutcome(
            action=action,
            repository_id=repository.id,
            normalized_url=repository.normalized_url,
            tracked_branch=repository.tracked_branch,
            commit_sha=commit_sha,
            generation_id=generation_id,
            message=message,
            pin_id=pin_id,
        )


def _source_is_valid(git_dir: Path, revision: SourceRevision | None) -> bool:
    if revision is None or revision.snapshot_state != "ready" or not revision.snapshot_path:
        return False
    try:
        blobs = read_commit_blobs(git_dir, revision.commit_sha)
        validate_worktree_bytes(git_dir, revision.commit_sha, Path(revision.snapshot_path), blobs)
    except (ChecklistError, OSError, RuntimeError):
        return False
    return True


def _absolute(git_dir: str) -> Path:
    path = Path(git_dir)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _embed(text: str) -> list[float]:
    from app.services.embedding_service import EmbeddingService

    service = getattr(_embed, "service", None)
    if service is None:
        service = EmbeddingService(EMBEDDING_MODEL)
        setattr(_embed, "service", service)
    return service.generate_text_embedding(text)


def _embed_many(texts: list[str]) -> list[list[float]]:
    from app.services.embedding_service import EmbeddingService

    service = getattr(_embed, "service", None)
    if service is None:
        service = EmbeddingService(EMBEDDING_MODEL)
        setattr(_embed, "service", service)
    return service.generate_text_embeddings(texts)


_embed.many = _embed_many
