"""Read indexed repository files from the active generation snapshot."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.models.catalog import File, GenerationFile, IndexGeneration, SourceRevision
from app.models.repository import Repository, RepositoryMember

MAX_FILE_BYTES = 512_000
MAX_LINES = 4000
CONTEXT_PAD = 8


class RepositoryFilesError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def list_repository_files(db: Session, user_id: str, repository_id: str) -> list[str]:
    repository = _member_repository(db, user_id, repository_id)
    generation, revision = _serving_generation(db, repository)
    rows = (
        db.query(File.relative_path)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            File.repository_id == repository.id,
        )
        .order_by(File.relative_path)
        .all()
    )
    return [row.relative_path for row in rows]


def read_repository_file(
    db: Session,
    user_id: str,
    repository_id: str,
    relative_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict:
    repository = _member_repository(db, user_id, repository_id)
    generation, revision = _serving_generation(db, repository)
    path = _safe_relative_path(relative_path)
    file_row = (
        db.query(File)
        .join(GenerationFile, GenerationFile.file_id == File.id)
        .filter(
            GenerationFile.index_generation_id == generation.id,
            File.repository_id == repository.id,
            File.relative_path == path,
        )
        .one_or_none()
    )
    if file_row is None:
        raise RepositoryFilesError(404, "file was not found")
    if revision.snapshot_path is None:
        raise RepositoryFilesError(409, "snapshot is not available")
    disk_path = Path(revision.snapshot_path) / path
    if not disk_path.is_file():
        raise RepositoryFilesError(404, "file was not found")
    if disk_path.stat().st_size > MAX_FILE_BYTES:
        raise RepositoryFilesError(413, "file is too large to preview")
    text = disk_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise RepositoryFilesError(413, "file is too large to preview")
    focus_start = start_line
    focus_end = end_line
    if focus_start is not None and focus_end is not None:
        focus_start = max(1, min(focus_start, len(lines)))
        focus_end = max(focus_start, min(focus_end, len(lines)))
    payload_lines = []
    for index, line in enumerate(lines, start=1):
        payload_lines.append({"n": index, "text": line})
    return {
        "path": path,
        "total_lines": len(lines),
        "focus_start": focus_start,
        "focus_end": focus_end,
        "lines": payload_lines,
    }


def _member_repository(db: Session, user_id: str, repository_id: str) -> Repository:
    member = (
        db.query(RepositoryMember)
        .filter(
            RepositoryMember.user_id == user_id,
            RepositoryMember.repository_id == repository_id,
            RepositoryMember.status == "active",
        )
        .one_or_none()
    )
    if member is None:
        raise RepositoryFilesError(404, "repository was not found")
    repository = db.get(Repository, repository_id)
    if repository is None:
        raise RepositoryFilesError(404, "repository was not found")
    return repository


def _serving_generation(db: Session, repository: Repository) -> tuple[IndexGeneration, SourceRevision]:
    if not repository.active_generation_id:
        raise RepositoryFilesError(409, "index is not ready")
    generation = db.get(IndexGeneration, repository.active_generation_id)
    if generation is None or generation.status != "ready":
        raise RepositoryFilesError(409, "index is not ready")
    revision = db.get(SourceRevision, generation.source_revision_id)
    if revision is None or revision.snapshot_state != "ready" or not revision.snapshot_path:
        raise RepositoryFilesError(409, "snapshot is not available")
    return generation, revision


def _safe_relative_path(relative_path: str) -> str:
    path = relative_path.strip().replace("\\", "/").lstrip("/")
    if not path or path.startswith("..") or "/../" in f"/{path}/":
        raise RepositoryFilesError(400, "invalid path")
    return path
