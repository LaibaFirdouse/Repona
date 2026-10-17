from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import client_user_id
from app.db.session import get_db
from app.schemas.repository import RepositoryCreateRequest, RepositoryCreateResponse
from app.services.refresh_service import RefreshError, RefreshService

router = APIRouter(tags=["repository"])
refresh_service = RefreshService()


@router.post("/repository", response_model=RepositoryCreateResponse, status_code=201)
def create_repository(
    request: RepositoryCreateRequest,
    db: Session = Depends(get_db),
) -> RepositoryCreateResponse:
    try:
        outcome = refresh_service.index(
            db,
            str(request.repo_url),
            request.tracked_branch,
            client_user_id(db, request.user_id),
            request.access_token,
        )
    except RefreshError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return RepositoryCreateResponse(
        repository_id=outcome.repository_id,
        normalized_url=outcome.normalized_url,
        tracked_branch=outcome.tracked_branch,
        commit_sha=outcome.commit_sha,
        generation_id=outcome.generation_id,
        action=outcome.action,
        message=outcome.message,
    )
