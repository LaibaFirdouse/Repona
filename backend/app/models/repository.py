from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis_report import AnalysisReport
    from app.models.catalog import Chunk
    from app.models.code_chunk import CodeChunk


class Repository(Base):
    """One indexed Git URL. Questions read active_generation_id."""

    __tablename__ = "repositories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_repositories_active_generation",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["id", "refresh_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_repositories_refresh_generation",
            use_alter=True,
        ),
        CheckConstraint(
            "refresh_state IN ('idle', 'running')",
            name="ck_repositories_refresh_state",
        ),
        CheckConstraint(
            "provider IN ('github', 'gitlab')",
            name="ck_repositories_provider",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    normalized_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    tracked_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    git_dir: Mapped[str] = mapped_column(Text, nullable=False)
    active_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    refresh_state: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    refresh_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    refresh_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    refresh_epoch: Mapped[int] = mapped_column(nullable=False, default=0)
    refresh_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    analysis_reports: Mapped[list["AnalysisReport"]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )
    indexed_chunks: Mapped[list["Chunk"]] = relationship(back_populates="repository")

    @property
    def repo_url(self) -> str:
        return self.normalized_url


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RepositoryMember(Base):
    __tablename__ = "repository_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'reader')", name="ck_repository_members_role"),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_repository_members_status",
        ),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RepositoryCredential(Base):
    __tablename__ = "repository_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "repository_id", name="uq_repository_credentials_user_repo"),
        CheckConstraint(
            "provider IN ('github', 'gitlab')",
            name="ck_repository_credentials_provider",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
