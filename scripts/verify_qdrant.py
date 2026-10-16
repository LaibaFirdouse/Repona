"""Verify Qdrant Cloud or local Qdrant responds to the REST API."""

from __future__ import annotations

import sys

from app.core.config import settings
from app.ingestion.vector_store import _request


def main() -> int:
    url = settings.qdrant_url.rstrip("/")
    print(f"qdrant_url={url} api_key={'set' if settings.qdrant_api_key.strip() else 'unset'}")
    body = _request(url, "/collections", method="GET", required=True)
    names = [item["name"] for item in body.get("result", {}).get("collections", [])]
    print(f"qdrant_ok collections={len(names)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"qdrant_failed {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
