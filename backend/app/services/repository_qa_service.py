from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.qa import RepositoryQuestionRequest, RepositoryQuestionResponse


class RepositoryQAServiceError(Exception):
    pass


class RepositoryQAService:
    def answer_question(
        self,
        request: RepositoryQuestionRequest,
        db: Session,
    ) -> RepositoryQuestionResponse:
        raise RepositoryQAServiceError("A question runs through the ask route.")
