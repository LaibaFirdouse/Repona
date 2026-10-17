from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.client_session import maybe_expire_inactive_clients, touch_client_activity, valid_client_id


def client_user_id(db: Session, user_id: str) -> str:
    if not valid_client_id(user_id):
        raise HTTPException(status_code=400, detail="user_id must be a client session id")
    maybe_expire_inactive_clients(db)
    touch_client_activity(db, user_id)
    return user_id
