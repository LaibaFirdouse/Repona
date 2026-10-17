"""Anonymous browser sessions keyed by client_id with sliding TTL."""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.repository import RepositoryMember, User
from app.services.repository_delete import purge_repository

_CLIENT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_last_expiry_sweep = 0.0
COOKIE_NAME = "repona_client_id"


def valid_client_id(value: str | None) -> bool:
    return bool(value and _CLIENT_ID.fullmatch(value.strip()))


def resolve_client_id(cookie_value: str | None, query_value: str | None) -> str:
    if valid_client_id(cookie_value):
        return cookie_value.strip()  # type: ignore[union-attr]
    if valid_client_id(query_value):
        return query_value.strip()  # type: ignore[union-attr]
    return str(uuid.uuid4())


def ensure_client_user(db: Session, client_id: str) -> User:
    now = datetime.utcnow()
    user = db.get(User, client_id)
    if user is None:
        user = User(id=client_id, created_at=now, last_activity_at=now)
        db.add(user)
    else:
        user.last_activity_at = now
    db.commit()
    db.refresh(user)
    return user


def touch_client_activity(db: Session, client_id: str) -> None:
    if not valid_client_id(client_id):
        return
    user = db.get(User, client_id)
    now = datetime.utcnow()
    if user is None:
        db.add(User(id=client_id, created_at=now, last_activity_at=now))
    else:
        user.last_activity_at = now
    db.commit()


def maybe_expire_inactive_clients(db: Session) -> None:
    global _last_expiry_sweep
    now_mono = time.monotonic()
    if now_mono - _last_expiry_sweep < 300:
        return
    _last_expiry_sweep = now_mono
    expire_inactive_clients(db)


def expire_inactive_clients(db: Session) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=settings.client_session_ttl_hours)
    stale_users = db.query(User).filter(User.last_activity_at < cutoff).all()
    removed = 0
    for user in stale_users:
        repo_ids = [
            row[0]
            for row in db.query(RepositoryMember.repository_id)
            .filter(RepositoryMember.user_id == user.id)
            .distinct()
            .all()
        ]
        for repository_id in repo_ids:
            purge_repository(db, repository_id, ignore_refresh_lease=True)
        db.delete(user)
        removed += 1
    if removed:
        db.commit()
    return removed
