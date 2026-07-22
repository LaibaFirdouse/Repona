from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    directory_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ignored_directories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    technologies: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    directory_structure: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    token_usage: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    repository: Mapped["Repository"] = relationship(back_populates="analysis_reports")
