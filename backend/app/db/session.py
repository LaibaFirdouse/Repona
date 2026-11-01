from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# Columns added after the first release. create_all() does not alter existing tables.
_COLUMN_UPGRADES = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP",
    "UPDATE users SET last_activity_at = created_at WHERE last_activity_at IS NULL",
    "ALTER TABLE generation_chunks ADD COLUMN IF NOT EXISTS vector_status VARCHAR(16) NOT NULL DEFAULT 'pending'",
    "ALTER TABLE index_generations ADD COLUMN IF NOT EXISTS capability_report_json TEXT",
)
# Indexes added after the first release (also declared on the models for new databases).
# create_all() only creates indexes together with a new table, so existing databases need these.
_INDEX_UPGRADES = (
    "CREATE INDEX IF NOT EXISTS ix_chunks_symbol_id ON chunks (symbol_id)",
    "CREATE INDEX IF NOT EXISTS ix_chunks_file_id ON chunks (file_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_files_generation_file ON generation_files (index_generation_id, file_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_chunks_generation_chunk ON generation_chunks (index_generation_id, chunk_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_chunks_generation_status ON generation_chunks (index_generation_id, vector_status)",
)


def create_database_tables() -> None:
    import app.models  # noqa: F401

    with engine.begin() as connection:
        # Before create_all: the trigram index on symbols.name needs the extension.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP"))
        connection.execute(text("UPDATE users SET last_activity_at = created_at WHERE last_activity_at IS NULL"))
        connection.execute(
            text(
                "ALTER TABLE generation_chunks ADD COLUMN IF NOT EXISTS vector_status VARCHAR(16) NOT NULL DEFAULT 'pending'"
            )
        )
        connection.execute(
            text("ALTER TABLE index_generations ADD COLUMN IF NOT EXISTS capability_report_json TEXT")
        )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        for statement in _COLUMN_UPGRADES + _INDEX_UPGRADES:
            connection.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
