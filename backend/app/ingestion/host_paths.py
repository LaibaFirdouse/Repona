"""Keep repository git and snapshot paths under this process's data directory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.ingestion.coordination import refresh_lease_held
from app.models.catalog import IndexGeneration, SourceRevision
from app.models.repository import Repository


def canonical_git_dir(repository_id: str) -> Path:
    return PROJECT_ROOT / "data" / "git" / repository_id


def canonical_snapshot_dir(snapshot_name: str) -> Path:
    return PROJECT_ROOT / "data" / "snapshots" / snapshot_name


def repair_repository_data_paths(db: Session, repository: Repository) -> bool:
    """Point catalog rows at PROJECT_ROOT/data when paths came from another host."""
    changed = False
    canonical_git = canonical_git_dir(repository.id)
    stored_git = Path(repository.git_dir)
    if stored_git.resolve() != canonical_git.resolve():
        if not stored_git.exists() or not str(stored_git).startswith(str(PROJECT_ROOT)):
            repository.git_dir = str(canonical_git)
            changed = True
    for revision in db.query(SourceRevision).filter(SourceRevision.repository_id == repository.id):
        if revision.snapshot_path:
            target = canonical_snapshot_dir(Path(revision.snapshot_path).name)
            if Path(revision.snapshot_path) != target:
                revision.snapshot_path = str(target)
                changed = True
        if revision.snapshot_state == "ready" and revision.snapshot_path:
            if not Path(revision.snapshot_path).exists():
                revision.snapshot_state = "absent"
                revision.snapshot_path = None
                revision.creating_path = None
                revision.publish_path = None
                changed = True
    return changed


def reset_expired_refresh(db: Session, repository: Repository, now: datetime | None = None) -> bool:
    """Clear a stale running refresh so a new index can start."""
    current = now or datetime.utcnow()
    if repository.refresh_state != "running":
        return False
    if refresh_lease_held(repository.refresh_lease_expires_at, repository.refresh_heartbeat_at, current):
        return False
    repository.refresh_state = "idle"
    repository.refresh_generation_id = None
    repository.refresh_owner = None
    repository.refresh_lease_expires_at = None
    repository.refresh_heartbeat_at = None
    pending = (
        db.query(IndexGeneration)
        .filter(
            IndexGeneration.repository_id == repository.id,
            IndexGeneration.status == "pending",
        )
        .all()
    )
    for generation in pending:
        generation.status = "failed"
        generation.error_message = "refresh lease expired before completion"
        generation.finished_at = current
    return True
