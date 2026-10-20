"""Run the full latency benchmark on three public repos (small → medium → large)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from sqlalchemy import func

from app.db.session import SessionLocal, create_database_tables
from app.models.catalog import Chunk, GenerationChunk, GenerationFile, IndexGeneration
from app.models.repository import Repository
from app.services.client_session import ensure_client_user
from app.services.refresh_service import RefreshError, RefreshService
from app.storage.identity import normalize_repository_url

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark import (  # noqa: E402
    bench_embedding,
    bench_git_probe,
    bench_neo4j,
    bench_postgres,
    bench_qdrant,
    bench_retrieval,
)
from compare_retrieval_paths import (  # noqa: E402
    _bench_tree,
    _ready,
    _run_answer,
)

DEFAULT_CLIENT_ID = os.getenv(
    "BENCHMARK_CLIENT_ID",
    "a1b2c3d4-e5f6-4789-a012-3456789abcde",
)


def ensure_benchmark_repository_access(db, repo_url: str, client_id: str) -> Repository:
    """Ensure client user exists and can call RefreshService.ask for an indexed repository."""
    ensure_client_user(db, client_id)
    normalized = normalize_repository_url(repo_url)
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    if repository is None:
        raise RefreshError(404, "repository was not found")
    RefreshService()._join(db, normalized, repository.tracked_branch, client_id, "")
    return repository


SUITE: tuple[dict[str, str], ...] = (
    {
        "tier": "small",
        "repo_url": "https://github.com/pallets/itsdangerous",
        "question": "How does Signer.sign produce a signed value?",
    },
    {
        "tier": "medium",
        "repo_url": "https://github.com/pallets/flask",
        "question": "How does Flask.dispatch_request invoke the matched view function?",
    },
    {
        "tier": "large",
        "repo_url": os.getenv("BENCHMARK_LARGE_REPO_URL", "https://github.com/LaibaFirdouse/Repona"),
        "question": os.getenv(
            "BENCHMARK_LARGE_QUESTION",
            "How does AnswerService retrieve evidence for a question?",
        ),
    },
)


def _generation_stats(db, generation_id: str) -> dict[str, int]:
    file_count = (
        db.query(func.count())
        .select_from(GenerationFile)
        .filter(GenerationFile.index_generation_id == generation_id)
        .scalar()
        or 0
    )
    chunk_count = (
        db.query(func.count())
        .select_from(GenerationChunk)
        .filter(GenerationChunk.index_generation_id == generation_id)
        .scalar()
        or 0
    )
    symbol_count = (
        db.query(func.count())
        .select_from(Chunk)
        .join(GenerationChunk, GenerationChunk.chunk_id == Chunk.id)
        .filter(GenerationChunk.index_generation_id == generation_id, Chunk.symbol_id.isnot(None))
        .scalar()
        or 0
    )
    return {"files": int(file_count), "chunks": int(chunk_count), "symbol_chunks": int(symbol_count)}


def _ensure_ready(db, repo_url: str, user_id: str, *, index: bool) -> dict[str, Any]:
    normalized = normalize_repository_url(repo_url)
    service = RefreshService()
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    generation = None
    if repository is not None and repository.active_generation_id:
        generation = db.get(IndexGeneration, repository.active_generation_id)
    ready = generation is not None and generation.status == "ready"
    index_result: dict[str, Any] = {"skipped": True, "already_ready": ready}
    if ready and not index:
        return index_result
    if not index and not ready:
        return {"skipped": False, "error": "no ready generation; pass --index"}
    started = perf_counter()
    try:
        outcome = service.index(db, repo_url, None, user_id, None)
    except RefreshError as exc:
        return {"skipped": False, "error": exc.detail, "status_code": exc.status_code}
    except Exception as exc:
        return {"skipped": False, "error": f"{type(exc).__name__}: {exc}"}
    index_result = {
        "skipped": False,
        "action": outcome.action,
        "message": outcome.message,
        "commit_sha": outcome.commit_sha,
        "generation_id": outcome.generation_id,
        "index_ms": round((perf_counter() - started) * 1000, 1),
    }
    db.expire_all()
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    generation = None if repository is None else db.get(IndexGeneration, repository.active_generation_id)
    if generation is None or generation.status != "ready":
        index_result["error"] = index_result.get("message") or "generation not ready after index"
    return index_result


def _compare_repo(db, repo_url: str, question: str, user_id: str) -> dict[str, Any]:
    repository, generation, revision = _ready(db, repo_url)
    tree_strict = _bench_tree(repository, revision, light=False)
    tree_light = _bench_tree(repository, revision, light=True)
    strict = _run_answer(
        db,
        repository,
        question,
        user_id,
        light_snapshot=False,
        max_file_filter=99999,
    )
    db.rollback()
    db.expire_all()
    max_filter = int(os.getenv("RETRIEVAL_VECTOR_MAX_FILE_FILTER", "256"))
    optimized = _run_answer(
        db,
        repository,
        question,
        user_id,
        light_snapshot=True,
        max_file_filter=max_filter,
    )
    cold = _run_answer(
        db,
        repository,
        question,
        user_id,
        light_snapshot=True,
        max_file_filter=max_filter,
    )
    db.rollback()
    db.expire_all()
    warm = _run_answer(
        db,
        repository,
        question,
        user_id,
        light_snapshot=True,
        max_file_filter=max_filter,
    )
    same_spans = strict["span_fingerprint"] == optimized["span_fingerprint"]
    same_fused = strict["fused"] == optimized["fused"]
    same_vector = strict["vector_anchors"] == optimized["vector_anchors"]
    return {
        "repository_id": repository.id,
        "generation_id": generation.id,
        "stats": _generation_stats(db, generation.id),
        "accuracy": {
            "same_fused_anchors": same_fused,
            "same_vector_anchors": same_vector,
            "same_span_fingerprints": same_spans,
        },
        "snapshot_validate_ms": {"strict_git_tree": tree_strict, "light_head_check": tree_light},
        "ask_strict": strict,
        "ask_optimized_defaults": optimized,
        "cold_warm": {"cold_ms": cold["total_ms"], "warm_ms": warm["total_ms"]},
        "worth_it": {
            "tree_speedup_x": round(tree_strict["mean_ms"] / max(tree_light["mean_ms"], 0.1), 1),
            "total_retrieval_saved_ms": round(strict["total_ms"] - optimized["total_ms"], 1),
        },
    }


def _write_report(report: dict[str, Any], output: str) -> None:
    text = json.dumps(report, indent=2) + "\n"
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    also = Path("evaluation/benchmark-latest.json")
    if also.resolve() != out.resolve():
        also.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="store_true", help="Index each repo before measuring (slow for large).")
    parser.add_argument("--skip-index", action="store_true", help="Fail if a repo is not ready instead of indexing.")
    parser.add_argument(
        "--output",
        default="evaluation/benchmark-suite-latest.json",
        help="Write the combined JSON report here.",
    )
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    args = parser.parse_args()
    if args.index and args.skip_index:
        parser.error("use either --index or --skip-index, not both")

    create_database_tables()
    db = SessionLocal()
    try:
        ensure_client_user(db, args.client_id)
    finally:
        db.close()

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": args.client_id,
        "components": {
            "postgres": bench_postgres(),
            "neo4j": bench_neo4j(),
            "qdrant": bench_qdrant(),
            "embedding": bench_embedding(),
        },
        "repositories": [],
    }

    for entry in SUITE:
        repo_url = entry["repo_url"]
        question = entry["question"]
        row: dict[str, Any] = {
            "tier": entry["tier"],
            "repo_url": repo_url,
            "question": question,
            "git_probe": bench_git_probe(repo_url),
        }
        db = SessionLocal()
        try:
            do_index = args.index
            if args.skip_index:
                do_index = False
            row["index"] = _ensure_ready(db, repo_url, args.client_id, index=do_index)
            if row["index"].get("error") and not (
                row["index"].get("skipped") and row["index"].get("already_ready")
            ):
                row["retrieval"] = {"ok": False, "error": row["index"]["error"]}
                row["compare"] = {"ok": False, "error": row["index"]["error"]}
                report["repositories"].append(row)
                continue
            row["retrieval"] = bench_retrieval(repo_url, question, args.client_id)
            try:
                row["compare"] = _compare_repo(db, repo_url, question, args.client_id)
            except SystemExit as exc:
                row["compare"] = {"ok": False, "error": str(exc)}
            except Exception as exc:
                row["compare"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            db.close()
        report["repositories"].append(row)
        _write_report(report, args.output)

    failed = []
    for item in report["repositories"]:
        if not item.get("retrieval", {}).get("ok"):
            failed.append(item)
            continue
        compare = item.get("compare") or {}
        accuracy = compare.get("accuracy") if isinstance(compare, dict) else None
        if accuracy is not None and not accuracy.get("same_span_fingerprints", True):
            failed.append(item)
    _write_report(report, args.output)
    print(json.dumps(report, indent=2))
    if failed:
        print(f"benchmark_suite_incomplete tiers={[f['tier'] for f in failed]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
