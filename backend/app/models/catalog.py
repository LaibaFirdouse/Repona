from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SourceRevision(Base):
    __tablename__ = "source_revisions"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", name="uq_source_revisions_commit"),
        UniqueConstraint("repository_id", "id", name="uq_source_revisions_repository_id"),
        CheckConstraint(
            "snapshot_state IN ('absent', 'creating', 'ready', 'deleting')",
            name="ck_source_revisions_snapshot_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_state: Mapped[str] = mapped_column(String(16), nullable=False, default="absent")
    creating_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    creation_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    creation_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleting_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deletion_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IndexGeneration(Base):
    __tablename__ = "index_generations"
    __table_args__ = (
        UniqueConstraint("repository_id", "id", name="uq_index_generations_repository_id"),
        UniqueConstraint(
            "repository_id",
            "id",
            "source_revision_id",
            name="uq_index_generations_pin_target",
        ),
        UniqueConstraint(
            "source_revision_id",
            "parser_version",
            "chunking_version",
            "embedding_config_fingerprint",
            "lexical_index_version",
            "inclusion_rules_version",
            "build_attempt",
            name="uq_index_generations_attempt",
        ),
        ForeignKeyConstraint(
            ["repository_id", "source_revision_id"],
            ["source_revisions.repository_id", "source_revisions.id"],
            name="fk_index_generations_source_revision",
        ),
        CheckConstraint(
            "status IN ('pending', 'failed', 'ready', 'retired')",
            name="ck_index_generations_status",
        ),
        CheckConstraint("build_attempt >= 1", name="ck_index_generations_build_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    vector_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_input_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    lexical_index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    inclusion_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    build_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    qdrant_collection: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gc_claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Directory(Base):
    __tablename__ = "directories"
    __table_args__ = (
        UniqueConstraint("repository_id", "relative_path", name="uq_directories_path"),
        UniqueConstraint("repository_id", "id", name="uq_directories_repository_id"),
        ForeignKeyConstraint(
            ["repository_id", "parent_directory_id"],
            ["directories.repository_id", "directories.id"],
            name="fk_directories_parent",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    parent_directory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "relative_path",
            "content_hash",
            name="uq_files_path_hash",
        ),
        UniqueConstraint("repository_id", "id", name="uq_files_repository_id"),
        ForeignKeyConstraint(
            ["repository_id", "directory_id"],
            ["directories.repository_id", "directories.id"],
            name="fk_files_directory",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    directory_id: Mapped[str] = mapped_column(String(36), nullable=False)
    logical_file_key: Mapped[str] = mapped_column(String(36), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_blob_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("repository_id", "id", name="uq_symbols_repository_id"),
        ForeignKeyConstraint(
            ["repository_id", "file_id"],
            ["files.repository_id", "files.id"],
            name="fk_symbols_file",
        ),
        ForeignKeyConstraint(
            ["repository_id", "parent_symbol_id"],
            ["symbols.repository_id", "symbols.id"],
            name="fk_symbols_parent",
        ),
        CheckConstraint("end_byte >= start_byte", name="ck_symbols_span"),
        CheckConstraint(
            "start_line IS NULL OR end_line IS NULL OR end_line >= start_line",
            name="ck_symbols_lines",
        ),
        CheckConstraint(
            "kind IN ('module', 'class', 'function', 'method', 'interface', 'type', 'enum', 'constant')",
            name="ck_symbols_kind",
        ),
        Index("ix_symbols_file_parser_name", "file_id", "parser_version", "name"),
        Index("ix_symbols_file_qualified_name", "file_id", "qualified_name"),
        Index(
            "ix_symbols_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_symbol_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    identity_key: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    start_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("repository_id", "id", name="uq_chunks_repository_id"),
        ForeignKeyConstraint(
            ["repository_id", "file_id"],
            ["files.repository_id", "files.id"],
            name="fk_chunks_file",
        ),
        ForeignKeyConstraint(
            ["repository_id", "symbol_id"],
            ["symbols.repository_id", "symbols.id"],
            name="fk_chunks_symbol",
        ),
        CheckConstraint("end_byte > start_byte", name="ck_chunks_span"),
        CheckConstraint(
            "start_line IS NULL OR end_line IS NULL OR end_line >= start_line",
            name="ck_chunks_lines",
        ),
        CheckConstraint(
            "kind IN ('symbol_body', 'symbol_part', 'import_block', 'file_overview', 'non_code')",
            name="ck_chunks_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    symbol_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_byte: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    repository: Mapped["Repository"] = relationship(back_populates="chunks")


class GenerationFile(Base):
    __tablename__ = "generation_files"
    __table_args__ = (
        UniqueConstraint(
            "index_generation_id",
            "logical_file_key",
            name="uq_generation_files_logical_path",
        ),
        ForeignKeyConstraint(
            ["repository_id", "index_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_generation_files_generation",
        ),
        ForeignKeyConstraint(
            ["repository_id", "file_id"],
            ["files.repository_id", "files.id"],
            name="fk_generation_files_file",
        ),
        CheckConstraint(
            "parse_status IN ('not_parsed', 'parsed', 'syntax_error')",
            name="ck_generation_files_parse_status",
        ),
    )

    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), primary_key=True)
    index_generation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    logical_file_key: Mapped[str] = mapped_column(String(36), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    parse_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class GenerationDirectory(Base):
    __tablename__ = "generation_directories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "index_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_generation_directories_generation",
        ),
        ForeignKeyConstraint(
            ["repository_id", "directory_id"],
            ["directories.repository_id", "directories.id"],
            name="fk_generation_directories_directory",
        ),
    )

    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), primary_key=True)
    index_generation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    directory_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class GenerationChunk(Base):
    __tablename__ = "generation_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "index_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_generation_chunks_generation",
        ),
        ForeignKeyConstraint(
            ["repository_id", "chunk_id"],
            ["chunks.repository_id", "chunks.id"],
            name="fk_generation_chunks_chunk",
        ),
        Index(
            "ix_generation_chunks_search",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), primary_key=True)
    index_generation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    context_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=False)
    vector_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class DetectedTechnology(Base):
    __tablename__ = "detected_technologies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    index_generation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "index_generation_id"],
            ["index_generations.repository_id", "index_generations.id"],
            name="fk_detected_technologies_generation",
        ),
        ForeignKeyConstraint(
            ["repository_id", "evidence_file_id"],
            ["files.repository_id", "files.id"],
            name="fk_detected_technologies_file",
        ),
    )


class GenerationPin(Base):
    __tablename__ = "generation_pins"
    __table_args__ = (
        ForeignKeyConstraint(
            ["repository_id", "index_generation_id", "source_revision_id"],
            [
                "index_generations.repository_id",
                "index_generations.id",
                "index_generations.source_revision_id",
            ],
            name="fk_generation_pins_generation",
        ),
        ForeignKeyConstraint(
            ["repository_id", "source_revision_id"],
            ["source_revisions.repository_id", "source_revisions.id"],
            name="fk_generation_pins_source_revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    index_generation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hard_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
