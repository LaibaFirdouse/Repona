"""Exhaustive step-by-step benchmark for three repo tiers (JSON + markdown report)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Callable

from sqlalchemy import func, text

from app.core.config import settings
from app.db.session import SessionLocal, create_database_tables
from app.ingestion.upstream import ls_remote_head
from app.ingestion.vector_store import _request as qdrant_request, collection_config, search_points
from app.models.catalog import Chunk, GenerationChunk, GenerationFile, IndexGeneration, SourceRevision
from app.models.repository import Repository
from app.retrieval.answer import (
    AnswerService,
    _name_hits,
    _require_fresh_tree,
    _text_hits,
)
from app.retrieval.fusion import consecutive_ranks, fuse_anchors
from app.retrieval.vector_filter import vector_query_filter
from app.services.client_session import ensure_client_user
from app.services.refresh_service import RefreshService, _embed
from app.storage.identity import normalize_repository_url

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from benchmark_suite import DEFAULT_CLIENT_ID, SUITE, _generation_stats, ensure_benchmark_repository_access  # noqa: E402


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 2)


def _step(steps: list[dict[str, Any]], name: str, started: float, **extra: Any) -> None:
    row: dict[str, Any] = {"step": name, "ms": _ms(perf_counter() - started)}
    row.update(extra)
    steps.append(row)


def _timed_samples(label: str, fn: Callable[[], None], repeats: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    samples: list[float] = []
    error: str | None = None
    for index in range(repeats):
        started = perf_counter()
        try:
            fn()
            samples.append(perf_counter() - started)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
    if samples:
        steps.append(
            {
                "step": label,
                "repeats": len(samples),
                "samples_ms": [_ms(value) for value in samples],
                "mean_ms": _ms(mean(samples)),
                "min_ms": _ms(min(samples)),
                "max_ms": _ms(max(samples)),
            }
        )
    return {"ok": error is None, "error": error, "steps": steps}


def bench_components_detailed() -> dict[str, Any]:
    out: dict[str, Any] = {"steps": []}
    root_steps: list[dict[str, Any]] = out["steps"]

    def postgres_detail() -> None:
        db = SessionLocal()
        try:
            t = perf_counter()
            db.execute(text("SELECT 1"))
            _step(root_steps, "postgres.select_1", t)
            t = perf_counter()
            db.query(Repository).count()
            _step(root_steps, "postgres.count_repositories", t)
        finally:
            db.close()

    out["postgres"] = _timed_samples("postgres_session", postgres_detail, 3)

    if not settings.neo4j_password:
        out["neo4j"] = {"ok": False, "error": "NEO4J_PASSWORD unset", "steps": []}
    else:
        def neo4j_detail() -> None:
            from neo4j import GraphDatabase

            t = perf_counter()
            driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            _step(root_steps, "neo4j.driver_create", t)
            try:
                t = perf_counter()
                driver.verify_connectivity()
                _step(root_steps, "neo4j.verify_connectivity", t)
                t = perf_counter()
                with driver.session() as session:
                    session.run("RETURN 1 AS n").single()
                _step(root_steps, "neo4j.session_query", t)
            finally:
                t = perf_counter()
                driver.close()
                _step(root_steps, "neo4j.driver_close", t)

        out["neo4j"] = _timed_samples("neo4j_round_trip", neo4j_detail, 3)

    url = settings.qdrant_url.rstrip("/")

    def qdrant_detail() -> None:
        t = perf_counter()
        qdrant_request(url, "/collections", method="GET", required=True)
        _step(root_steps, "qdrant.http_list_collections", t)

    out["qdrant"] = _timed_samples("qdrant_list_collections", qdrant_detail, 3)

    phrase = "Which functions call derive_key?"
    embed_steps: list[dict[str, Any]] = []

    def embed_once() -> None:
        t = perf_counter()
        _embed(phrase)
        _step(embed_steps, "embedding.encode_single_query", t)

    out["embedding"] = {
        "cold": _timed_samples("embedding_first_load", embed_once, 1),
        "warm": _timed_samples("embedding_warm", embed_once, 5),
    }
    out["embedding"]["step_log"] = embed_steps[-1:] if embed_steps else []

    return out


def _snapshot_substeps(repository: Repository, revision: SourceRevision, *, light: bool) -> list[dict[str, Any]]:
    from app.ingestion.materialize import read_commit_blobs
    from app.ingestion.validation import ChecklistError, _git_text, validate_worktree_bytes
    from app.services.refresh_service import _absolute

    steps: list[dict[str, Any]] = []
    env_prev = os.environ.get("RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE")
    os.environ["RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE"] = "true" if light else "false"
    from app.core import config as config_module

    config_module.settings = config_module.Settings()
    started = perf_counter()
    try:
        if not revision.snapshot_path:
            raise ChecklistError("missing snapshot_path")
        snapshot = Path(revision.snapshot_path)
        t = perf_counter()
        if not snapshot.is_dir():
            raise ChecklistError("snapshot missing")
        _step(steps, "snapshot.path_is_dir", t)
        t = perf_counter()
        head = _git_text(snapshot, "rev-parse", "HEAD")
        _step(steps, "snapshot.git_rev_parse_head", t, commit=head[:12] if head else None)
        if head != revision.commit_sha:
            raise ChecklistError("HEAD mismatch")
        if config_module.settings.retrieval_light_snapshot_validate:
            _step(steps, "snapshot.light_validate_skip_blob_walk", started)
            return steps
        t = perf_counter()
        blobs = read_commit_blobs(_absolute(repository.git_dir), revision.commit_sha)
        _step(steps, "snapshot.read_commit_blobs", t, blob_count=len(blobs))
        t = perf_counter()
        validate_worktree_bytes(_absolute(repository.git_dir), revision.commit_sha, snapshot, blobs)
        _step(steps, "snapshot.validate_worktree_bytes", t)
    except Exception as exc:
        _step(steps, "snapshot.error", started, error=f"{type(exc).__name__}: {exc}")
    finally:
        if env_prev is None:
            os.environ.pop("RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE", None)
        else:
            os.environ["RETRIEVAL_LIGHT_SNAPSHOT_VALIDATE"] = env_prev
        config_module.settings = config_module.Settings()
    _step(steps, "snapshot.total", started, mode="light" if light else "strict")
    return steps


def _vector_lane_substeps(db, service: AnswerService, generation: IndexGeneration, question: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    t = perf_counter()
    file_ids = [
        row.file_id
        for row in db.query(GenerationFile.file_id).filter(GenerationFile.index_generation_id == generation.id).all()
    ]
    _step(steps, "vector.list_generation_file_ids", t, count=len(file_ids))
    if not file_ids:
        return steps
    t = perf_counter()
    size, distance = service._cached_collection_config(generation.qdrant_collection)
    _step(steps, "vector.qdrant_collection_config", t, size=size, distance=distance)
    t = perf_counter()
    vector = service.embed(question)
    _step(steps, "vector.embed_question", t, dimensions=len(vector))
    t = perf_counter()
    query_filter = vector_query_filter(generation, file_ids, settings.retrieval_vector_max_file_filter)
    _step(steps, "vector.build_query_filter", t)
    t = perf_counter()
    batch = search_points(
        service.qdrant_url,
        generation.qdrant_collection,
        vector,
        query_filter,
        16,
        0,
    )
    _step(steps, "vector.qdrant_search_first_batch", t, hits=len(batch))
    t = perf_counter()
    service._vector_hits(db, generation, question)
    _step(steps, "vector.full_lane_with_postgres_hydration", t)
    return steps


def _sequential_lane_timings(db, generation: IndexGeneration, question: str, service: AnswerService) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    t = perf_counter()
    name = _name_hits(db, generation, question)
    _step(steps, "lane.name_sequential", t, hits=len(name))
    t = perf_counter()
    text = _text_hits(db, generation, question)
    _step(steps, "lane.text_sequential", t, hits=len(text))
    t = perf_counter()
    vector = service._vector_hits(db, generation, question)
    _step(steps, "lane.vector_sequential", t, hits=len(vector))
    t = perf_counter()
    lanes = [consecutive_ranks(name), consecutive_ranks(text), consecutive_ranks(vector)]
    fused = fuse_anchors(lanes)
    _step(steps, "lane.fuse_rrf_sequential", t, fused_anchors=len(fused))
    return steps


def detailed_repository(
    db,
    repo_url: str,
    question: str,
    user_id: str,
) -> dict[str, Any]:
    try:
        repository = ensure_benchmark_repository_access(db, repo_url, user_id)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not repository.active_generation_id:
        return {"ok": False, "error": "repository not indexed"}
    generation = db.get(IndexGeneration, repository.active_generation_id)
    revision = db.get(SourceRevision, generation.source_revision_id) if generation else None
    if generation is None or revision is None or generation.status != "ready":
        return {"ok": False, "error": "generation not ready"}

    normalized = normalize_repository_url(repo_url)
    row: dict[str, Any] = {
        "repository_id": repository.id,
        "generation_id": generation.id,
        "commit_sha": revision.commit_sha,
        "stats": _generation_stats(db, generation.id),
        "steps": [],
    }
    steps: list[dict[str, Any]] = row["steps"]

    t = perf_counter()
    probe = ls_remote_head(normalized, "")
    _step(steps, "preflight.github_ls_remote_head", t, upstream=probe.upstream, sha=(probe.sha or "")[:12])

    refresh = RefreshService()
    t = perf_counter()
    outcome = refresh.ask(db, repository.id, user_id)
    _step(
        steps,
        "preflight.refresh_service_ask",
        t,
        action=outcome.action,
        message=outcome.message,
        pin_id=outcome.pin_id,
    )
    if outcome.action != "serve" or not outcome.pin_id:
        row["ok"] = False
        row["error"] = outcome.message or outcome.action
        return row

    row["snapshot_validate"] = {
        "strict": _snapshot_substeps(repository, revision, light=False),
        "light": _snapshot_substeps(repository, revision, light=True),
    }

    mock = lambda _s, _u, _m: '{"claims":[{"kind":"observed","text":"Benchmark stub.","evidence_ids":["E1"]}]}'
    service = AnswerService(complete=mock)

    row["lane_sequential"] = _sequential_lane_timings(db, generation, question, service)
    row["vector_substeps"] = _vector_lane_substeps(db, service, generation, question)

    trace: dict[str, Any] = {"record_steps": True}
    t = perf_counter()
    try:
        AnswerService(complete=mock).answer(db, question, outcome, trace)
    except Exception as exc:
        _step(steps, "ask.answer_error", t, error=f"{type(exc).__name__}: {exc}")
        row["ok"] = False
        row["error"] = str(exc)
        return row
    _step(steps, "ask.answer_total", t)

    row["ask_trace"] = {
        "steps": trace.get("steps") or [],
        "stage_seconds_ms": {k: _ms(float(v)) for k, v in (trace.get("stage_seconds") or {}).items()},
        "lane_anchor_counts": {
            "name": len(trace.get("name_anchor_ids") or []),
            "text": len(trace.get("text_anchor_ids") or []),
            "vector": len(trace.get("vector_anchor_ids") or []),
        },
        "fused_anchor_count": len(trace.get("fused_anchor_ids") or []),
        "candidate_chunk_count": len(trace.get("candidate_chunk_ids") or []),
        "spans_before_budget": trace.get("spans_before_budget") or [],
        "packet_span_count": len(trace.get("packet_spans") or []),
        "packet_spans_summary": [
            {
                "chunk_id": span.get("chunk_id"),
                "qualified_name": span.get("qualified_name"),
                "chunk_kind": span.get("chunk_kind"),
            }
            for span in (trace.get("packet_spans") or [])[:20]
        ],
        "technologies": trace.get("packet_technologies") or [],
        "prompt_tokens": trace.get("prompt_tokens"),
        "packed_counts": trace.get("packed_counts"),
        "answer_strategy": trace.get("answer_strategy"),
        "retrieval_ms": _ms(float(trace.get("retrieval_seconds") or 0)),
        "model_ms": _ms(float(trace.get("model_seconds") or 0)),
        "total_ms": trace.get("total_ms"),
    }
    row["ok"] = True
    return row


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Repona detailed benchmark",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Infrastructure components",
        "",
    ]
    for step in report.get("components", {}).get("steps", []):
        lines.append(f"- **{step['step']}**: {step['ms']} ms")
    lines.append("")
    for key in ("postgres", "neo4j", "qdrant"):
        block = report["components"].get(key) or {}
        if block.get("steps"):
            mean_ms = block["steps"][0].get("mean_ms")
            lines.append(f"- {key} round-trip mean: **{mean_ms}** ms")
    lines.append("")
    for repo in report.get("repositories", []):
        lines.extend(
            [
                f"## {repo['tier'].upper()} — `{repo['repo_url']}`",
                "",
                f"Question: {repo['question']}",
                "",
            ]
        )
        detail = repo.get("detail") or {}
        if not detail.get("ok"):
            lines.append(f"**Failed:** {detail.get('error')}")
            lines.append("")
            continue
        stats = detail.get("stats") or {}
        lines.append(f"Indexed: **{stats.get('files')}** files, **{stats.get('chunks')}** chunks")
        lines.append("")
        lines.append("### Top-level steps")
        for step in detail.get("steps") or []:
            extra = ", ".join(f"{k}={v}" for k, v in step.items() if k not in {"step", "ms"})
            suffix = f" ({extra})" if extra else ""
            lines.append(f"- `{step['step']}`: **{step['ms']}** ms{suffix}")
        lines.append("")
        lines.append("### Sequential lane timings (not parallel)")
        for step in detail.get("lane_sequential") or []:
            lines.append(f"- `{step['step']}`: **{step['ms']}** ms")
        lines.append("")
        lines.append("### Vector lane substeps")
        for step in detail.get("vector_substeps") or []:
            lines.append(f"- `{step['step']}`: **{step['ms']}** ms")
        lines.append("")
        trace = detail.get("ask_trace") or {}
        lines.append("### Ask pipeline (parallel lanes + full answer path)")
        lines.append("")
        lines.append("| Step | ms |")
        lines.append("|------|-----|")
        for step in trace.get("steps") or []:
            lines.append(f"| `{step['step']}` | {step['ms']} |")
        lines.append("")
        lines.append("**Parallel lane stages (inside step 02):**")
        for name, ms in (trace.get("stage_seconds_ms") or {}).items():
            lines.append(f"- {name}: **{ms}** ms")
        lines.append("")
        lines.append(
            f"Packet: {trace.get('packed_counts')} · prompt tokens: {trace.get('prompt_tokens')} · "
            f"strategy: {trace.get('answer_strategy')}"
        )
        lines.append("")
        snap = detail.get("snapshot_validate") or {}
        for mode in ("strict", "light"):
            lines.append(f"### Snapshot validation ({mode})")
            for step in snap.get(mode) or []:
                lines.append(f"- `{step['step']}`: **{step['ms']}** ms")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="store_true", help="Re-index each repo before measuring.")
    parser.add_argument("--output", default="evaluation/benchmark-detailed-latest.json")
    parser.add_argument("--markdown", default="evaluation/benchmark-detailed-report.md")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    args = parser.parse_args()
    do_index = args.index

    create_database_tables()
    db = SessionLocal()
    try:
        ensure_client_user(db, args.client_id)
    finally:
        db.close()

    from benchmark_suite import _ensure_ready  # noqa: E402

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": args.client_id,
        "environment": settings.app_env,
        "targets": {
            "postgres": "configured" if settings.database_url else "missing",
            "qdrant": settings.qdrant_url,
            "neo4j": settings.neo4j_uri,
            "llm_provider": settings.llm_provider,
        },
        "components": bench_components_detailed(),
        "repositories": [],
    }

    for entry in SUITE:
        repo_row: dict[str, Any] = dict(entry)
        db = SessionLocal()
        try:
            if do_index:
                repo_row["index"] = _ensure_ready(db, entry["repo_url"], args.client_id, index=True)
            repo_row["detail"] = detailed_repository(db, entry["repo_url"], entry["question"], args.client_id)
        finally:
            db.close()
        report["repositories"].append(repo_row)
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md = _markdown_report(report)
    Path(args.markdown).write_text(md, encoding="utf-8")
    print(md)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = [r for r in report["repositories"] if not (r.get("detail") or {}).get("ok")]
    if failed:
        print(f"detailed_benchmark_incomplete: {[r['tier'] for r in failed]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
