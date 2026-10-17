"""Read-only views of indexed repositories and Q&A history."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.catalog import IndexGeneration, SourceRevision
from app.models.qa_history import RepositoryQaMessage
from app.models.repository import Repository, RepositoryMember


def _index_state(repository: Repository, generation: IndexGeneration | None) -> str:
    if repository.refresh_state == "running":
        return "indexing"
    if generation is None:
        return "wait"
    if generation.status == "ready":
        return "ready"
    if generation.status == "pending":
        return "indexing"
    return "wait"


def list_user_repositories(db: Session, user_id: str) -> list[dict]:
    rows = (
        db.query(Repository, IndexGeneration)
        .join(RepositoryMember, RepositoryMember.repository_id == Repository.id)
        .outerjoin(IndexGeneration, IndexGeneration.id == Repository.active_generation_id)
        .filter(RepositoryMember.user_id == user_id, RepositoryMember.status == "active")
        .order_by(desc(Repository.updated_at))
        .all()
    )
    items: list[dict] = []
    for repository, generation in rows:
        items.append(
            {
                "repository_id": repository.id,
                "normalized_url": repository.normalized_url,
                "tracked_branch": repository.tracked_branch,
                "commit_sha": _commit_for_generation(db, generation),
                "generation_id": repository.active_generation_id,
                "index_state": _index_state(repository, generation),
                "updated_at": repository.updated_at.isoformat() if repository.updated_at else None,
            }
        )
    return items


def _commit_for_generation(db: Session, generation: IndexGeneration) -> str | None:
    from app.models.catalog import SourceRevision

    revision = db.get(SourceRevision, generation.source_revision_id)
    return None if revision is None else revision.commit_sha


def list_qa_messages(db: Session, user_id: str, repository_id: str, limit: int = 200) -> list[RepositoryQaMessage]:
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
        return []
    return (
        db.query(RepositoryQaMessage)
        .filter(RepositoryQaMessage.user_id == user_id, RepositoryQaMessage.repository_id == repository_id)
        .order_by(RepositoryQaMessage.created_at)
        .limit(limit)
        .all()
    )


def record_qa_message(
    db: Session,
    *,
    user_id: str,
    repository_id: str,
    question: str,
    answer: str,
    citations: list[dict],
    generation_id: str | None,
    commit_sha: str | None,
) -> RepositoryQaMessage:
    row = RepositoryQaMessage(
        user_id=user_id,
        repository_id=repository_id,
        question=question,
        answer=answer,
        citations_json=json.dumps(citations),
        generation_id=generation_id,
        commit_sha=commit_sha,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
