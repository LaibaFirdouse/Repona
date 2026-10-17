from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RepositoryQaMessage(Base):
    __tablename__ = "repository_qa_messages"
    __table_args__ = (Index("ix_repository_qa_messages_user_repo_created", "user_id", "repository_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
