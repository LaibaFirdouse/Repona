"""Remove one repository from Postgres, Qdrant, Neo4j, and on-disk git/snapshots."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.ingestion.coordination import refresh_lease_held
from app.ingestion.vector_store import VectorStoreError, delete_points_for_repository
from app.models.catalog import (
    Chunk,
    DetectedTechnology,
    Directory,
    File,
    GenerationChunk,
    GenerationDirectory,
    GenerationFile,
    GenerationPin,
    IndexGeneration,
    SourceRevision,
    Symbol,
)
from app.models.qa_history import RepositoryQaMessage
from app.models.repository import Repository, RepositoryCredential, RepositoryMember
class RepositoryDeleteError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def delete_repository(db: Session, user_id: str, repository_id: str) -> None:
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
        raise RepositoryDeleteError(404, "repository was not found")

    repository = db.get(Repository, repository_id)
    if repository is None:
        raise RepositoryDeleteError(404, "repository was not found")

    now = datetime.utcnow()
    if refresh_lease_held(repository.refresh_lease_expires_at, repository.refresh_heartbeat_at, now):
        raise RepositoryDeleteError(409, "wait until indexing finishes before deleting this repository")
    purge_repository(db, repository_id, ignore_refresh_lease=False)


def purge_repository(db: Session, repository_id: str, *, ignore_refresh_lease: bool = False) -> None:
    repository = db.get(Repository, repository_id)
    if repository is None:
        return

    now = datetime.utcnow()
    if not ignore_refresh_lease and refresh_lease_held(
        repository.refresh_lease_expires_at,
        repository.refresh_heartbeat_at,
        now,
    ):
        return

    generations = db.query(IndexGeneration).filter(IndexGeneration.repository_id == repository_id).all()
    collection_names = {row.qdrant_collection for row in generations if row.qdrant_collection}
    git_dir = Path(repository.git_dir)
    snapshot_paths = {
        Path(row.snapshot_path)
        for row in db.query(SourceRevision).filter(SourceRevision.repository_id == repository_id)
        if row.snapshot_path
    }
    for row in db.query(SourceRevision).filter(SourceRevision.repository_id == repository_id):
        if row.deleting_path:
            snapshot_paths.add(Path(row.deleting_path))

    repository.active_generation_id = None
    repository.refresh_generation_id = None
    repository.refresh_state = "idle"
    repository.refresh_owner = None
    repository.refresh_lease_expires_at = None
    repository.refresh_heartbeat_at = None
    db.flush()

    db.query(RepositoryQaMessage).filter(RepositoryQaMessage.repository_id == repository_id).delete()
    db.query(GenerationPin).filter(GenerationPin.repository_id == repository_id).delete()
    db.query(GenerationChunk).filter(GenerationChunk.repository_id == repository_id).delete()
    db.query(GenerationFile).filter(GenerationFile.repository_id == repository_id).delete()
    db.query(GenerationDirectory).filter(GenerationDirectory.repository_id == repository_id).delete()
    db.query(DetectedTechnology).filter(DetectedTechnology.repository_id == repository_id).delete()
    db.query(IndexGeneration).filter(IndexGeneration.repository_id == repository_id).delete()
    db.query(Chunk).filter(Chunk.repository_id == repository_id).delete()
    db.query(Symbol).filter(Symbol.repository_id == repository_id).update({Symbol.parent_symbol_id: None})
    db.query(Symbol).filter(Symbol.repository_id == repository_id).delete()
    db.query(File).filter(File.repository_id == repository_id).delete()
    db.query(Directory).filter(Directory.repository_id == repository_id).update(
        {Directory.parent_directory_id: None}
    )
    db.query(Directory).filter(Directory.repository_id == repository_id).delete()
    db.query(SourceRevision).filter(SourceRevision.repository_id == repository_id).delete()
    db.query(RepositoryCredential).filter(RepositoryCredential.repository_id == repository_id).delete()
    db.query(RepositoryMember).filter(RepositoryMember.repository_id == repository_id).delete()
    db.delete(repository)
    db.commit()

    _delete_qdrant_points(collection_names, repository_id)
    _delete_graph_repository(repository_id)
    _delete_path(git_dir)
    for path in snapshot_paths:
        _delete_path(path)


def _delete_qdrant_points(collections: set[str], repository_id: str) -> None:
    url = settings.qdrant_url.rstrip("/")
    for name in collections:
        try:
            delete_points_for_repository(url, name, repository_id)
        except VectorStoreError:
            continue


def _delete_graph_repository(repository_id: str) -> None:
    if not settings.neo4j_password:
        return
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        with driver.session() as session:
            session.run(
                """
                MATCH (n)
                WHERE n.id = $repository_id
                   OR n.repository_id = $repository_id
                DETACH DELETE n
                """,
                repository_id=repository_id,
            )
    finally:
        driver.close()


def _delete_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
