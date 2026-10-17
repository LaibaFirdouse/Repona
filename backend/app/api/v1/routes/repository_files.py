from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import client_user_id
from app.db.session import get_db
from app.schemas.files import RepositoryFileContentResponse, RepositoryFileListResponse
from app.services.repository_files import RepositoryFilesError, list_repository_files, read_repository_file

router = APIRouter(tags=["repository"])


@router.get("/repositories/{repository_id}/files", response_model=RepositoryFileListResponse)
def repository_files(
    repository_id: str,
    user_id: str = Query(..., min_length=1, max_length=36),
    db: Session = Depends(get_db),
) -> RepositoryFileListResponse:
    scoped = client_user_id(db, user_id)
    try:
        paths = list_repository_files(db, scoped, repository_id)
    except RepositoryFilesError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return RepositoryFileListResponse(repository_id=repository_id, paths=paths)


@router.get("/repositories/{repository_id}/files/content", response_model=RepositoryFileContentResponse)
def repository_file_content(
    repository_id: str,
    path: str = Query(..., min_length=1),
    user_id: str = Query(..., min_length=1, max_length=36),
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> RepositoryFileContentResponse:
    scoped = client_user_id(db, user_id)
    try:
        payload = read_repository_file(db, scoped, repository_id, path, start_line, end_line)
    except RepositoryFilesError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return RepositoryFileContentResponse(**payload)
