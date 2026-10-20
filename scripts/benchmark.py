"""Component and end-to-end latency benchmarks for Repona."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from statistics import mean
from time import perf_counter
from typing import Any

from sqlalchemy import text
import psycopg
from app.core.config import settings
from app.db.session import SessionLocal, create_database_tables
from app.ingestion.upstream import ls_remote_head
from app.ingestion.vector_store import _request as qdrant_request
from app.models.catalog import IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.retrieval.answer import AnswerService
from app.services.refresh_service import RefreshService, _embed, _embed_many
from app.storage.identity import normalize_repository_url


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 1)


def _timed(label: str, fn, repeats: int = 1) -> dict[str, Any]:
    samples: list[float] = []
    last_error: str | None = None
    for _ in range(repeats):
        start = perf_counter()
        try:
            fn()
            samples.append(perf_counter() - start)
        except Exception as exc:
            last_error = str(exc)
            break
    if not samples:
        return {"label": label, "ok": False, "error": last_error}
    return {
        "label": label,
        "ok": True,
        "samples_ms": [_ms(value) for value in samples],
        "mean_ms": _ms(mean(samples)),
        "min_ms": _ms(min(samples)),
        "max_ms": _ms(max(samples)),
    }


def bench_postgres() -> dict[str, Any]:
    def once() -> None:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db.query(Repository).count()
        finally:
            db.close()

    return _timed("postgres_ping_and_repo_count", once, repeats=3)


def bench_neo4j() -> dict[str, Any]:
    if not settings.neo4j_password:
        return {"label": "neo4j_verify", "ok": False, "error": "NEO4J_PASSWORD unset"}

    def once() -> None:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        try:
            driver.verify_connectivity()
            with driver.session() as session:
                session.run("RETURN 1 AS n").single()
        finally:
            driver.close()

    return _timed("neo4j_verify_and_query", once, repeats=3)


def bench_qdrant() -> dict[str, Any]:
    url = settings.qdrant_url.rstrip("/")

    def list_collections() -> None:
        qdrant_request(url, "/collections", method="GET", required=True)

    return _timed("qdrant_list_collections", list_collections, repeats=3)


def bench_embedding() -> dict[str, Any]:
    phrase = "Which functions call derive_key?"
    batch = [phrase] * 8

    cold = _timed("embedding_single_cold", lambda: _embed(phrase), repeats=1)
    warm = _timed("embedding_single_warm", lambda: _embed(phrase), repeats=5)
    batch_stat = _timed("embedding_batch_8", lambda: _embed_many(batch), repeats=3)
    return {"single_cold": cold, "single_warm": warm, "batch_8": batch_stat}


def bench_git_probe(repo_url: str) -> dict[str, Any]:
    normalized = normalize_repository_url(repo_url)

    def once() -> None:
        result = ls_remote_head(normalized, "")
        if result.upstream != "authorized" or not result.sha:
            raise RuntimeError(f"probe failed: {result.upstream}")

    return _timed("github_probe_head", once, repeats=3)


def _ready_repository(db, repo_url: str) -> tuple[Repository, IndexGeneration, SourceRevision] | None:
    normalized = normalize_repository_url(repo_url)
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    if repository is None or not repository.active_generation_id:
        return None
    generation = db.get(IndexGeneration, repository.active_generation_id)
    if generation is None or generation.status != "ready":
        return None
    revision = db.get(SourceRevision, generation.source_revision_id)
    if revision is None:
        return None
    return repository, generation, revision


def bench_retrieval(repo_url: str, question: str, user_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        ready = _ready_repository(db, repo_url)
        if ready is None:
            return {"ok": False, "error": "no ready generation for repo_url; index first"}

        def mock_complete(_system: str, _user: str, _max_tokens: int) -> str:
            return '{"claims":[{"kind":"observed","text":"Benchmark stub.","evidence_ids":["E1"]}]}'

        service = RefreshService()
        outcome = service.ask(db, ready[0].id, user_id)
        if outcome.action != "serve" or not outcome.pin_id:
            return {
                "ok": False,
                "error": outcome.message or f"ask action={outcome.action}",
                "action": outcome.action,
            }
        trace: dict[str, Any] = {}
        started = perf_counter()
        try:
            AnswerService(complete=mock_complete).answer(db, question, outcome, trace)
        except Exception:
            pass
        total_ms = _ms(perf_counter() - started)
        stages = trace.get("stage_seconds") or {}
        return {
            "ok": True,
            "repository_id": ready[0].id,
            "generation_id": ready[1].id,
            "commit_sha": ready[2].commit_sha,
            "retrieval_ms": _ms(trace.get("retrieval_seconds")),
            "model_ms": _ms(trace.get("model_seconds")),
            "total_with_stub_model_ms": total_ms,
            "stages_ms": {key: _ms(float(value)) for key, value in stages.items()},
            "lane_anchors": {
                "name": len(trace.get("name_anchor_ids") or []),
                "text": len(trace.get("text_anchor_ids") or []),
                "vector": len(trace.get("vector_anchor_ids") or []),
            },
            "packet_spans": len(trace.get("packet_spans") or []),
        }
    finally:
        db.close()


def _http_json(method: str, url: str, payload: dict | None = None, timeout: int = 600) -> tuple[float, dict, int]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    start = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            elapsed = perf_counter() - start
            parsed = json.loads(body) if body else {}
            return elapsed, parsed, response.status
    except urllib.error.HTTPError as exc:
        elapsed = perf_counter() - start
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": raw}
        return elapsed, parsed, exc.code


def bench_http(
    base_url: str,
    repo_url: str,
    question: str,
    user_id: str,
    run_index: bool,
    repository_id: str = "",
) -> dict[str, Any]:
    base = base_url.rstrip("/")
    out: dict[str, Any] = {"base_url": base}
    elapsed, body, status = _http_json("GET", f"{base}/api/v1/health", timeout=30)
    out["health"] = {"status_code": status, "ms": _ms(elapsed), "body": body}

    repository_id = repository_id.strip() or None
    if run_index:
        elapsed, body, status = _http_json(
            "POST",
            f"{base}/api/v1/repository",
            {"repo_url": repo_url, "user_id": user_id},
            timeout=3600,
        )
        out["index"] = {"status_code": status, "ms": _ms(elapsed), "action": body.get("action"), "message": body.get("message")}
        repository_id = body.get("repository_id") or repository_id

    if not repository_id:
        elapsed, index_body, status = _http_json(
            "POST",
            f"{base}/api/v1/repository",
            {"repo_url": repo_url, "user_id": user_id},
            timeout=3600,
        )
        repository_id = index_body.get("repository_id")
        out["index_lookup"] = {"status_code": status, "ms": _ms(elapsed), "action": index_body.get("action")}

    if not repository_id:
        out["ask"] = {"ok": False, "error": "no repository_id"}
        return out

    elapsed, body, status = _http_json(
        "POST",
        f"{base}/api/v1/ask",
        {"repository_id": repository_id, "user_id": user_id, "question": question},
        timeout=600,
    )
    out["ask"] = {
        "status_code": status,
        "ms": _ms(elapsed),
        "action": body.get("action"),
        "citations": len(body.get("citations") or []),
        "provider": body.get("provider"),
        "message": (body.get("message") or body.get("detail") or "")[:200],
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark components and HTTP flows (defaults target this monorepo for dev only)."
    )
    parser.add_argument(
        "--repo-url",
        default=os.getenv("BENCHMARK_REPO_URL", "https://github.com/LaibaFirdouse/Repona"),
    )
    parser.add_argument(
        "--question",
        default=os.getenv(
            "BENCHMARK_QUESTION",
            "How does AnswerService retrieve evidence for a question?",
        ),
    )
    parser.add_argument(
        "--user-id",
        default="tester",
        help="API user_id (must be a client UUID from GET /api/v1/client when calling the HTTP API)",
    )
    parser.add_argument("--api-url", default="", help="If set, run HTTP end-to-end against this base URL.")
    parser.add_argument("--repository-id", default="", help="With --api-url, ask this repository without indexing.")
    parser.add_argument("--index", action="store_true", help="With --api-url, run a full index before ask.")
    parser.add_argument("--output", default="", help="Write JSON report to this path.")
    args = parser.parse_args()

    create_database_tables()
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": settings.app_env,
        "targets": {
            "postgres": "configured" if settings.database_url else "missing",
            "qdrant": settings.qdrant_url,
            "neo4j": settings.neo4j_uri,
            "llm_provider": settings.llm_provider,
        },
        "components": {
            "postgres": bench_postgres(),
            "neo4j": bench_neo4j(),
            "qdrant": bench_qdrant(),
            "embedding": bench_embedding(),
            "git_probe": bench_git_probe(args.repo_url),
        },
        "retrieval": bench_retrieval(args.repo_url, args.question, args.user_id),
    }
    if args.api_url.strip():
        report["http"] = bench_http(
            args.api_url.strip(),
            args.repo_url,
            args.question,
            args.user_id,
            args.index,
            args.repository_id,
        )

    text = json.dumps(report, indent=2) + "\n"
    print(text)
    if args.output:
        from pathlib import Path

        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"benchmark_failed {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
