"""Work-tree claims, reader pins, and garbage-collection steps."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.ingestion.coordination import (
    creation_recovery_action,
    gc_may_claim,
    gc_may_delete_worktree,
    lease_deadline,
    pin_may_insert,
    pin_may_renew,
)
from app.ingestion.materialize import read_commit_blobs
from app.ingestion.persist import GenerationWriteError
from app.ingestion.validation import ChecklistError, validate_worktree_bytes
from app.models.catalog import (
    DetectedTechnology,
    GenerationChunk,
    GenerationDirectory,
    GenerationFile,
    GenerationPin,
    IndexGeneration,
    SourceRevision,
)
from app.models.repository import Repository


def claim_snapshot(revision: SourceRevision, owner: str, snapshot_root: Path, now: datetime) -> str:
    if revision.snapshot_state in {"creating", "deleting"}:
        raise GenerationWriteError("a work tree claim is already held")
    if revision.snapshot_state == "ready" and revision.snapshot_path:
        return "reuse"
    publish = snapshot_root / revision.id
    creating = snapshot_root / f"{revision.id}.creating-{uuid4().hex}"
    revision.snapshot_state = "creating"
    revision.creating_path = str(creating)
    revision.publish_path = str(publish)
    revision.creation_owner = owner
    revision.creation_lease_expires_at = lease_deadline(now)
    return "create"


def publish_snapshot_claim(revision: SourceRevision, owner: str) -> None:
    if revision.snapshot_state != "creating" or revision.creation_owner != owner:
        raise GenerationWriteError("creation claim is no longer held")
    revision.snapshot_path = revision.publish_path
    revision.snapshot_state = "ready"
    revision.creating_path = None
    revision.publish_path = None
    revision.creation_owner = None
    revision.creation_lease_expires_at = None


def abort_snapshot_claim(revision: SourceRevision, owner: str) -> None:
    if revision.creation_owner != owner or revision.snapshot_state != "creating":
        return
    creating = Path(revision.creating_path) if revision.creating_path else None
    if creating is not None and creating.exists():
        shutil.rmtree(creating)
    revision.snapshot_state = "absent"
    revision.creating_path = None
    revision.publish_path = None
    revision.creation_owner = None
    revision.creation_lease_expires_at = None


def recover_creating_snapshot(revision: SourceRevision, owner: str, now: datetime, git_dir: Path, commit_sha: str) -> None:
    if revision.snapshot_state != "creating":
        return
    if revision.creation_lease_expires_at and revision.creation_lease_expires_at > now and revision.creation_owner != owner:
        raise GenerationWriteError("creation claim is still held")
    revision.creation_owner = owner
    revision.creation_lease_expires_at = lease_deadline(now)
    publish = Path(revision.publish_path) if revision.publish_path else None
    creating = Path(revision.creating_path) if revision.creating_path else None
    publish_exists = bool(publish and publish.exists())
    creating_exists = bool(creating and creating.exists())
    validation_passed = False
    if publish_exists and not creating_exists:
        try:
            blobs = read_commit_blobs(git_dir, commit_sha)
            validate_worktree_bytes(git_dir, commit_sha, publish, blobs)
            validation_passed = True
        except ChecklistError:
            validation_passed = False
    if creation_recovery_action(publish_exists, creating_exists, validation_passed) == "publish":
        revision.snapshot_path = str(publish)
        revision.snapshot_state = "ready"
    else:
        if creating_exists and creating is not None:
            shutil.rmtree(creating)
        if publish_exists and publish is not None and revision.snapshot_path is None:
            shutil.rmtree(publish)
        revision.snapshot_state = "absent"
        revision.snapshot_path = None
    revision.creating_path = None
    revision.publish_path = None
    revision.creation_owner = None
    revision.creation_lease_expires_at = None


def recover_deleting_snapshot(revision: SourceRevision, owner: str, now: datetime) -> None:
    if revision.snapshot_state != "deleting":
        return
    if revision.deletion_lease_expires_at and revision.deletion_lease_expires_at > now and revision.deletion_owner != owner:
        raise GenerationWriteError("deletion claim is still held")
    revision.deletion_owner = owner
    deleting = Path(revision.deleting_path) if revision.deleting_path else None
    if deleting is not None and deleting.exists():
        shutil.rmtree(deleting)
    if deleting is not None and deleting.exists():
        revision.deletion_lease_expires_at = lease_deadline(now)
        return
    revision.snapshot_state = "absent"
    revision.deleting_path = None
    revision.deletion_owner = None
    revision.deletion_lease_expires_at = None


def insert_reader_pin(
    db: Session,
    *,
    repository_id: str,
    generation_id: str,
    source_revision_id_value: str,
    expires_at: datetime,
    hard_deadline: datetime,
) -> GenerationPin | None:
    generation = db.query(IndexGeneration).filter(IndexGeneration.id == generation_id).with_for_update().one()
    revision = db.query(SourceRevision).filter(SourceRevision.id == source_revision_id_value).with_for_update().one()
    if not pin_may_insert(generation.gc_claimed_at, revision.snapshot_state, revision.snapshot_path, expires_at, hard_deadline):
        return None
    pin = GenerationPin(
        repository_id=repository_id,
        index_generation_id=generation_id,
        source_revision_id=source_revision_id_value,
        expires_at=expires_at,
        hard_deadline=hard_deadline,
    )
    db.add(pin)
    db.flush()
    return pin


def renew_reader_pin(db: Session, pin_id: str, new_expires_at: datetime, now: datetime) -> bool:
    pin = db.query(GenerationPin).filter(GenerationPin.id == pin_id).with_for_update().one()
    if now >= pin.expires_at or now >= pin.hard_deadline:
        db.delete(pin)
        return False
    generation = db.query(IndexGeneration).filter(IndexGeneration.id == pin.index_generation_id).with_for_update().one()
    revision = db.query(SourceRevision).filter(SourceRevision.id == pin.source_revision_id).with_for_update().one()
    if not pin_may_renew(generation.gc_claimed_at, revision.snapshot_state, new_expires_at, pin.hard_deadline):
        db.delete(pin)
        return False
    pin.expires_at = new_expires_at
    return True


def claim_garbage_collection(db: Session, repository_id: str, generation_id: str, owner: str, now: datetime) -> bool:
    repository = db.query(Repository).filter(Repository.id == repository_id).with_for_update().one()
    generation = db.query(IndexGeneration).filter(IndexGeneration.id == generation_id).with_for_update().one()
    revision = db.query(SourceRevision).filter(SourceRevision.id == generation.source_revision_id).with_for_update().one()
    unexpired = (
        db.query(GenerationPin)
        .filter(
            GenerationPin.index_generation_id == generation_id,
            GenerationPin.expires_at > now,
        )
        .count()
    )
    if not gc_may_claim(
        generation.status,
        generation.id,
        repository.active_generation_id,
        repository.refresh_generation_id,
        generation.gc_claimed_at,
        revision.snapshot_state,
        unexpired > 0,
    ):
        return False
    generation.gc_claimed_at = now
    db.query(GenerationFile).filter(GenerationFile.index_generation_id == generation_id).delete()
    db.query(GenerationChunk).filter(GenerationChunk.index_generation_id == generation_id).delete()
    db.query(GenerationDirectory).filter(GenerationDirectory.index_generation_id == generation_id).delete()
    db.query(DetectedTechnology).filter(DetectedTechnology.index_generation_id == generation_id).delete()
    other_live = (
        db.query(IndexGeneration)
        .filter(
            IndexGeneration.source_revision_id == revision.id,
            IndexGeneration.id != generation.id,
            IndexGeneration.status.in_(("pending", "ready")),
        )
        .count()
    )
    revision_unexpired = (
        db.query(GenerationPin)
        .filter(GenerationPin.source_revision_id == revision.id, GenerationPin.expires_at > now)
        .count()
    )
    active_or_refresh = revision.id in {
        _revision_id_for(db, repository.active_generation_id),
        _revision_id_for(db, repository.refresh_generation_id),
    }
    if gc_may_delete_worktree(revision.snapshot_path, other_live > 0, active_or_refresh, revision_unexpired > 0):
        revision.deleting_path = revision.snapshot_path
        revision.snapshot_path = None
        revision.deletion_owner = owner
        revision.deletion_lease_expires_at = lease_deadline(now)
        revision.snapshot_state = "deleting"
    return True


def finish_worktree_deletion(revision: SourceRevision) -> None:
    if revision.snapshot_state != "deleting" or not revision.deleting_path:
        return
    deleting = Path(revision.deleting_path)
    if deleting.exists():
        shutil.rmtree(deleting)
    if deleting.exists():
        return
    revision.snapshot_state = "absent"
    revision.deleting_path = None
    revision.deletion_owner = None
    revision.deletion_lease_expires_at = None


def _revision_id_for(db: Session, generation_id: str | None) -> str | None:
    if generation_id is None:
        return None
    generation = db.get(IndexGeneration, generation_id)
    if generation is None:
        return None
    return generation.source_revision_id
