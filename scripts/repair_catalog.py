"""Repair git/snapshot paths and optionally reset expired refresh locks."""

from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from app.ingestion.host_paths import repair_repository_data_paths, reset_expired_refresh
from app.models.repository import Repository
from app.storage.identity import normalize_repository_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair catalog paths for the current data directory.")
    parser.add_argument("--url", help="Normalized or raw GitHub URL")
    parser.add_argument("--repository-id", help="Repository UUID")
    parser.add_argument("--all", action="store_true", help="Repair every repository row")
    parser.add_argument(
        "--reset-expired-refresh",
        action="store_true",
        help="Clear refresh_state=running when the lease/heartbeat has expired",
    )
    args = parser.parse_args()
    if not args.all and not args.url and not args.repository_id:
        parser.error("pass --all, --url, or --repository-id")

    db = SessionLocal()
    try:
        if args.all:
            repositories = db.query(Repository).all()
        elif args.repository_id:
            row = db.get(Repository, args.repository_id)
            repositories = [] if row is None else [row]
        else:
            normalized = normalize_repository_url(args.url or "")
            row = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
            repositories = [] if row is None else [row]

        if not repositories:
            print("no matching repository", file=sys.stderr)
            return 1

        for repository in repositories:
            repaired = repair_repository_data_paths(db, repository)
            reset = reset_expired_refresh(db, repository) if args.reset_expired_refresh else False
            db.commit()
            print(
                f"{repository.normalized_url} id={repository.id} "
                f"paths_repaired={repaired} refresh_reset={reset} git_dir={repository.git_dir}"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
