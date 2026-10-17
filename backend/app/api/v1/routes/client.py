from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.schemas.client import ClientSessionResponse
from app.services.client_session import (
    COOKIE_NAME,
    ensure_client_user,
    maybe_expire_inactive_clients,
    resolve_client_id,
)

router = APIRouter(tags=["client"])


@router.get("/client", response_model=ClientSessionResponse)
def ensure_client_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    client_id: str | None = Query(default=None, max_length=36),
) -> ClientSessionResponse:
    maybe_expire_inactive_clients(db)
    resolved = resolve_client_id(request.cookies.get(COOKIE_NAME), client_id)
    ensure_client_user(db, resolved)
    max_age = settings.client_session_ttl_hours * 3600
    response.set_cookie(
        key=COOKIE_NAME,
        value=resolved,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.client_cookie_secure,
    )
    return ClientSessionResponse(
        client_id=resolved,
        ttl_hours=settings.client_session_ttl_hours,
    )
