from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.qa import RepositoryQuestionRequest, RepositoryQuestionResponse
from app.services.refresh_service import RefreshError, RefreshService

router = APIRouter(tags=["repository qa"])
refresh_service = RefreshService()


@router.post("/ask", response_model=RepositoryQuestionResponse)
def ask_repository_question(
    request: RepositoryQuestionRequest,
    db: Session = Depends(get_db),
) -> RepositoryQuestionResponse:
    try:
        outcome = refresh_service.ask(db, request.repository_id, request.user_id)
    except RefreshError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return RepositoryQuestionResponse(
        repository_id=outcome.repository_id,
        question=request.question,
        action=outcome.action,
        generation_id=outcome.generation_id,
        commit_sha=outcome.commit_sha,
        message=outcome.message,
    )
