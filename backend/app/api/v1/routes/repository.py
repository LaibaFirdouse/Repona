from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.repository import RepositoryCreateRequest, RepositoryCreateResponse
from app.services.repository_service import RepositoryService, RepositoryServiceError

router = APIRouter(tags=["repository"])
repository_service = RepositoryService()


@router.post(
    "/repository",
    response_model=RepositoryCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_repository(
    request: RepositoryCreateRequest,
    db: Session = Depends(get_db),
) -> RepositoryCreateResponse:
    try:
        return repository_service.create_repository(request, db)
    except RepositoryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
