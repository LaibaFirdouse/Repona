from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.qa import ASK_PROVIDERS, RepositoryQuestionRequest, RepositoryQuestionResponse
from app.services.repository_catalog import record_qa_message
from app.services.refresh_service import RefreshError, RefreshService
from app.retrieval.answer import AnswerService, Cancelled, RetrievalError
from app.services.llm.base import LLMProviderError
from app.services.llm.llm_factory import LLMFactory, provider_name

def _answered_provider(requested: str | None) -> str | None:
    selected = requested or provider_name()
    if selected in ASK_PROVIDERS:
        return selected
    return None


router = APIRouter(tags=["repository qa"])
refresh_service = RefreshService()
answer_service = AnswerService()


@router.post("/ask", response_model=RepositoryQuestionResponse)
def ask_repository_question(
    request: RepositoryQuestionRequest,
    db: Session = Depends(get_db),
) -> RepositoryQuestionResponse:
    try:
        outcome = refresh_service.ask(db, request.repository_id, request.user_id)
        answered = None
        if outcome.action == "serve":
            answered = answer_service.answer(
                db,
                request.question,
                outcome,
                model=request.model,
                provider=request.provider,
            )
    except LLMProviderError as error:
        raise HTTPException(status_code=503, detail="the model provider is unavailable") from error
    except RefreshError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except RetrievalError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except Cancelled as error:
        raise HTTPException(status_code=408, detail="the question was cancelled") from error
    if outcome.action == "serve" and answered is not None:
        record_qa_message(
            db,
            user_id=request.user_id,
            repository_id=outcome.repository_id,
            question=request.question,
            answer=answered.answer,
            citations=list(answered.citations),
            generation_id=outcome.generation_id,
            commit_sha=outcome.commit_sha,
        )
    return RepositoryQuestionResponse(
        repository_id=outcome.repository_id,
        question=request.question,
        action=outcome.action,
        generation_id=outcome.generation_id,
        commit_sha=outcome.commit_sha,
        message=answered.message if answered is not None else outcome.message,
        answer=answered.answer if answered is not None else "",
        citations=answered.citations if answered is not None else [],
        provider=_answered_provider(request.provider) if answered is not None else None,
        model=(request.model or LLMFactory.configured_model(request.provider)) if answered is not None else None,
    )
