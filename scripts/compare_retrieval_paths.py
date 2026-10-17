"""Compare strict vs optimized retrieval paths on an indexed repo (accuracy + latency)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from statistics import mean
from time import perf_counter
from typing import Any
from unittest.mock import patch

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.catalog import GenerationFile, IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.retrieval.answer import AnswerService, _load_span_text, _require_fresh_tree
from app.retrieval.fusion import materialize_anchors, chunk_ids_for_anchor, consecutive_ranks, fuse_anchors
from app.retrieval.answer import (
    _choices_for,
    _chunks_by_symbol,
    _name_hits,
    _text_hits,
)
from app.services.refresh_service import RefreshService
from app.storage.identity import normalize_repository_url


# Dev default: monorepo for large-repo latency/accuracy A/B (override with --repo-url).
BENCHMARK_REPO_DEFAULT = os.getenv("BENCHMARK_REPO_URL", "https://github.com/LaibaFirdouse/Repona")
DEFAULT_QUESTION = os.getenv(
    "BENCHMARK_QUESTION",
    "How does AnswerService retrieve evidence for a question?",
)


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _ready(db, repo_url: str) -> tuple[Repository, IndexGeneration, SourceRevision]:
    normalized = normalize_repository_url(repo_url)
    repository = db.query(Repository).filter(Repository.normalized_url == normalized).one_or_none()
    if repository is None or not repository.active_generation_id:
        raise SystemExit(f"No indexed repository for {repo_url}")
    generation = db.get(IndexGeneration, repository.active_generation_id)
    if generation is None or generation.status != "ready":
        raise SystemExit(f"Generation not ready for {repo_url}")
    revision = db.get(SourceRevision, generation.source_revision_id)
    if revision is None:
        raise SystemExit("Missing source revision")
    return repository, generation, revision


def _span_fingerprint(spans: list[dict]) -> list[dict[str, str]]:
    return sorted(
        [
            {
                "chunk_id": span.get("chunk_id") or "",
                "path": span.get("relative_path") or "",
                "name": span.get("qualified_name") or "",
            }
            for span in spans
        ],
        key=lambda item: item["chunk_id"],
    )


def _run_answer(
    db,
    repository: Repository,
    question: str,
    user_id: str,
    *,
    light_snapshot: bool,
    max_file_filter: int,
) -> dict[str, Any]:
    mock = lambda _s, _u, _m: '{"claims":[{"kind":"observed","text":"stub","evidence_ids":["E1"]}]}'
    service = RefreshService()
    outcome = service.ask(db, repository.id, user_id)
    if outcome.action != "serve" or not outcome.pin_id:
        raise RuntimeError(outcome.message or outcome.action)

    trace: dict[str, Any] = {}
    env = {
        "RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE": "true" if light_snapshot else "false",
        "RETRIEVAL_VECTOR_MAX_FILE_FILTER": str(max_file_filter),
    }
    started = perf_counter()
    with patch.dict(os.environ, env, clear=False):
        from app.core import config as config_module

        config_module.settings = config_module.Settings()
        AnswerService(complete=mock).answer(db, question, outcome, trace)
    total = perf_counter() - started
    stages = trace.get("stage_seconds") or {}
    return {
        "light_snapshot": light_snapshot,
        "max_file_filter": max_file_filter,
        "total_ms": _ms(total),
        "retrieval_ms": _ms(float(trace.get("retrieval_seconds") or 0)),
        "stages_ms": {key: _ms(float(value)) for key, value in stages.items()},
        "vector_anchors": list(trace.get("vector_anchor_ids") or []),
        "fused": list(trace.get("fused_anchor_ids") or []),
        "span_fingerprint": _span_fingerprint(trace.get("spans_before_budget") or []),
        "packet_spans": len(trace.get("packet_spans") or []),
    }


def _bench_tree(repository: Repository, revision: SourceRevision, *, light: bool, repeats: int = 3) -> dict[str, Any]:
    env = {"RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE": "true" if light else "false"}
    samples: list[float] = []
    with patch.dict(os.environ, env, clear=False):
        from app.core import config as config_module

        config_module.settings = config_module.Settings()
        for _ in range(repeats):
            start = perf_counter()
            _require_fresh_tree(repository, revision)
            samples.append(perf_counter() - start)
    return {"light": light, "mean_ms": _ms(mean(samples)), "min_ms": _ms(min(samples))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=BENCHMARK_REPO_DEFAULT)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--user-id", default="tester")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        repository, generation, revision = _ready(db, args.repo_url)

        tree_strict = _bench_tree(repository, revision, light=False)
        tree_light = _bench_tree(repository, revision, light=True)

        strict = _run_answer(
            db,
            repository,
            args.question,
            args.user_id,
            light_snapshot=False,
            max_file_filter=99999,
        )
        db.rollback()
        db.expire_all()
        max_filter = int(os.getenv("RETRIEVAL_VECTOR_MAX_FILE_FILTER", "256"))
        optimized = _run_answer(
            db,
            repository,
            args.question,
            args.user_id,
            light_snapshot=True,
            max_file_filter=max_filter,
        )

        same_spans = strict["span_fingerprint"] == optimized["span_fingerprint"]
        same_fused = strict["fused"] == optimized["fused"]
        same_vector = strict["vector_anchors"] == optimized["vector_anchors"]

        report = {
            "repository_url": args.repo_url,
            "repository_id": repository.id,
            "generation_id": generation.id,
            "file_count": db.query(GenerationFile).filter(GenerationFile.index_generation_id == generation.id).count(),
            "question": args.question,
            "accuracy": {
                "same_fused_anchors": same_fused,
                "same_vector_anchors": same_vector,
                "same_span_fingerprints": same_spans,
            },
            "snapshot_validate_ms": {"strict_git_tree": tree_strict, "light_head_check": tree_light},
            "ask_strict": strict,
            "ask_optimized_defaults": optimized,
            "worth_it": {
                "tree_speedup_x": round(tree_strict["mean_ms"] / max(tree_light["mean_ms"], 0.1), 1),
                "load_stage_saved_ms": round(
                    strict["stages_ms"].get("load", 0) - optimized["stages_ms"].get("load", 0),
                    1,
                ),
                "total_retrieval_saved_ms": round(strict["total_ms"] - optimized["total_ms"], 1),
            },
        }

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            acc = report["accuracy"]
            print(f"Repo: {args.repo_url} ({report['file_count']} indexed files)")
            print(f"Question: {args.question}")
            print()
            print("Accuracy (strict vs optimized defaults)")
            print(f"  fused anchors match:     {acc['same_fused_anchors']}")
            print(f"  vector anchors match:    {acc['same_vector_anchors']}")
            print(f"  loaded span fingerprints: {acc['same_span_fingerprints']}")
            print()
            print("Snapshot validation (mean of 3 runs)")
            print(f"  strict full git tree:  {tree_strict['mean_ms']} ms")
            print(f"  light HEAD check:      {tree_light['mean_ms']} ms")
            print()
            print("Ask retrieval (stub LLM, ms)")
            print(f"  strict total:    {strict['total_ms']}  stages {strict['stages_ms']}")
            print(f"  optimized total: {optimized['total_ms']}  stages {optimized['stages_ms']}")
            print()
            w = report["worth_it"]
            print(f"Tree speedup ~{w['tree_speedup_x']}x | load stage Δ {w['load_stage_saved_ms']} ms | total Δ {w['total_retrieval_saved_ms']} ms")
            if not (same_fused and same_spans):
                print("\nWARNING: span or anchor mismatch — review before demo.", file=sys.stderr)
                sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
