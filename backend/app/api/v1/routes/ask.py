import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import client_user_id
from app.db.session import get_db
from app.schemas.qa import ASK_PROVIDERS, RepositoryQuestionRequest, RepositoryQuestionResponse
from app.services.repository_catalog import record_qa_message
from app.services.refresh_service import RefreshError, RefreshService
from app.retrieval.answer import Answer, AnswerService, Cancelled, RetrievalError
from app.retrieval.conversation_context import QaTurn, normalize_turns
from app.services.llm.base import LLMProviderError
from app.services.llm.llm_factory import LLMFactory, provider_name

def _answered_provider(requested: str | None) -> str | None:
    selected = requested or provider_name()
    if selected in ASK_PROVIDERS:
        return selected
    return None


def _question_response(
    *,
    request: RepositoryQuestionRequest,
    outcome,
    answered: Answer | None,
) -> RepositoryQuestionResponse:
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


router = APIRouter(tags=["repository qa"])
refresh_service = RefreshService()
answer_service = AnswerService()


def _conversation_from_request(request: RepositoryQuestionRequest) -> list[QaTurn]:
    return normalize_turns(
        QaTurn(question=turn.question, answer=turn.answer) for turn in request.history
    )


def _run_ask(
    db: Session,
    request: RepositoryQuestionRequest,
) -> tuple[object, Answer | None]:
    user_id = client_user_id(db, request.user_id)
    outcome = refresh_service.ask(db, request.repository_id, user_id)
    answered = None
    if outcome.action == "serve":
        answered = answer_service.answer(
            db,
            request.question,
            outcome,
            model=request.model,
            provider=request.provider,
            conversation=_conversation_from_request(request),
        )
    if outcome.action == "serve" and answered is not None:
        record_qa_message(
            db,
            user_id=user_id,
            repository_id=outcome.repository_id,
            question=request.question,
            answer=answered.answer,
            citations=list(answered.citations),
            generation_id=outcome.generation_id,
            commit_sha=outcome.commit_sha,
        )
    return outcome, answered


@router.post("/ask", response_model=RepositoryQuestionResponse)
def ask_repository_question(
    request: RepositoryQuestionRequest,
    db: Session = Depends(get_db),
) -> RepositoryQuestionResponse:
    try:
        outcome, answered = _run_ask(db, request)
    except LLMProviderError as error:
        raise HTTPException(status_code=503, detail="the model provider is unavailable") from error
    except RefreshError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except RetrievalError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except Cancelled as error:
        raise HTTPException(status_code=408, detail="the question was cancelled") from error
    return _question_response(request=request, outcome=outcome, answered=answered)


@router.post("/ask/stream")
def ask_repository_question_stream(
    request: RepositoryQuestionRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    def event_lines():
        try:
            user_id = client_user_id(db, request.user_id)
            outcome = refresh_service.ask(db, request.repository_id, user_id)
            if outcome.action != "serve":
                payload = _question_response(request=request, outcome=outcome, answered=None)
                yield json.dumps({"type": "complete", "response": payload.model_dump()}) + "\n"
                return
            answered: Answer | None = None
            conversation = _conversation_from_request(request)
            for event in answer_service._answer_events(
                db,
                request.question,
                outcome,
                model=request.model,
                provider=request.provider,
                conversation=conversation,
            ):
                if event["type"] == "phase":
                    yield json.dumps({"type": "phase", "phase": event["phase"]}) + "\n"
                elif event["type"] == "complete":
                    answered = event["answer"]
            if answered is None:
                raise RetrievalError(500, "the answer did not complete")
            record_qa_message(
                db,
                user_id=user_id,
                repository_id=outcome.repository_id,
                question=request.question,
                answer=answered.answer,
                citations=list(answered.citations),
                generation_id=outcome.generation_id,
                commit_sha=outcome.commit_sha,
            )
            payload = _question_response(request=request, outcome=outcome, answered=answered)
            yield json.dumps({"type": "complete", "response": payload.model_dump()}) + "\n"
        except LLMProviderError:
            yield json.dumps({"type": "error", "status": 503, "detail": "the model provider is unavailable"}) + "\n"
        except RefreshError as error:
            yield json.dumps({"type": "error", "status": error.status_code, "detail": error.detail}) + "\n"
        except RetrievalError as error:
            yield json.dumps({"type": "error", "status": error.status_code, "detail": error.detail}) + "\n"
        except Cancelled:
            yield json.dumps({"type": "error", "status": 408, "detail": "the question was cancelled"}) + "\n"
        except Exception as error:
            yield json.dumps(
                {"type": "error", "status": 500, "detail": f"the question failed: {error}"},
            ) + "\n"

    return StreamingResponse(event_lines(), media_type="application/x-ndjson")
