from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.qa import RepositoryQuestionRequest, RepositoryQuestionResponse
from app.services.repository_qa_service import (
    RepositoryQAService,
    RepositoryQAServiceError,
)

router = APIRouter(tags=["repository qa"])
qa_service = RepositoryQAService()


@router.post("/ask", response_model=RepositoryQuestionResponse)
def ask_repository_question(
    request: RepositoryQuestionRequest,
    db: Session = Depends(get_db),
) -> RepositoryQuestionResponse:
    try:
        return qa_service.answer_question(request, db)
    except RepositoryQAServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
