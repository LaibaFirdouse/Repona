#!/usr/bin/env python3
"""Delete Neo4j nodes for one or more repository ids (frees Aura node quota)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.ingestion.graph_cleanup import delete_repository_graph  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository_ids", nargs="+", help="Repository UUIDs to purge from Neo4j")
    args = parser.parse_args()
    for repository_id in args.repository_ids:
        print(f"purging graph for {repository_id}")
        delete_repository_graph(repository_id)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
