"""Verify Neon Postgres: connect, ensure extensions, create Repona tables."""

from __future__ import annotations

import sys

from sqlalchemy import inspect, text

from app.core.config import settings
from app.db.session import create_database_tables, engine


def main() -> int:
    host = engine.url.host or "unknown"
    database = engine.url.database or "unknown"
    print(f"database_url host={host} database={database} driver={engine.url.drivername}")
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
        print(f"postgres_ok version={version.split(',')[0]}")
    create_database_tables()
    tables = sorted(inspect(engine).get_table_names())
    print(f"schema_ok tables={len(tables)}")
    if not tables:
        print("schema_ok failed: no tables after create_database_tables", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
