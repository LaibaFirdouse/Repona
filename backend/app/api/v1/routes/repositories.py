from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.qa import Citation
from app.schemas.repository import RepositoryListResponse, RepositorySummary, QaHistoryItem, QaHistoryResponse
from app.services.repository_catalog import list_qa_messages, list_user_repositories

router = APIRouter(tags=["repository"])


@router.get("/repositories", response_model=RepositoryListResponse)
def list_repositories(
    user_id: str = Query(..., min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> RepositoryListResponse:
    rows = list_user_repositories(db, user_id)
    return RepositoryListResponse(repositories=[RepositorySummary(**row) for row in rows])


@router.get("/repositories/{repository_id}/messages", response_model=QaHistoryResponse)
def repository_messages(
    repository_id: str,
    user_id: str = Query(..., min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> QaHistoryResponse:
    known = list_user_repositories(db, user_id)
    if not any(item["repository_id"] == repository_id for item in known):
        raise HTTPException(status_code=404, detail="repository was not found")
    rows = list_qa_messages(db, user_id, repository_id)
    messages: list[QaHistoryItem] = []
    for row in rows:
        try:
            raw_citations = json.loads(row.citations_json or "[]")
        except json.JSONDecodeError:
            raw_citations = []
        citations = [Citation(**item) for item in raw_citations if isinstance(item, dict)]
        messages.append(
            QaHistoryItem(
                id=row.id,
                question=row.question,
                answer=row.answer,
                citations=citations,
                commit_sha=row.commit_sha,
                created_at=row.created_at.isoformat(),
            )
        )
    return QaHistoryResponse(repository_id=repository_id, messages=messages)
